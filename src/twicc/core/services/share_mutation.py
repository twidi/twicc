"""Create / update / revoke / delete / propagate shares.

Single source of truth for the two surfaces that mutate ``Share``:
- the REST endpoints in ``twicc.views`` (``/api/shares/…``), and
- the CLI drop-request handlers (``share:*`` kinds).

Mirrors ``artifact_bookmark_mutation.py``: ``*_from_payload`` return a
``ShareMutationResult`` (never raise for business-rule errors), writes run
under ``run_under_db_write_lock``, and every mutation broadcasts on ``updates``.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, UTC
from typing import NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.auth.hashers import hash_password
from twicc.core.services.public_origin import usable_public_origin
from twicc.core.services.share_tokens import mint_share_id, mint_token
from twicc.paths import get_share_snapshot_dir
from twicc.providers.db_writer import run_under_db_write_lock

logger = logging.getLogger(__name__)

# Snapshot size guard (design §9.2).
_MAX_SNAPSHOT_BYTES = 200 * 1024 * 1024

# Option key allowlists per kind (design §5.2). Unknown keys are rejected.
_SESSION_OPTION_KEYS = frozenset({
    "mode", "frozen_at_line", "max_display_mode", "include_subagents",
    "show_timestamps", "show_title", "display_title",
})
_ARTIFACT_OPTION_KEYS = frozenset({"snapshot_at", "show_title", "display_title"})
_DISPLAY_MODES = ("conversation", "simplified", "normal", "debug")


class ShareError(NamedTuple):
    field: str
    code: str
    message: str


class ShareMutationResult(NamedTuple):
    success: bool
    share_id: str | None
    errors: list[ShareError] | None


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ── Option validation ──────────────────────────────────────────────────────

def _validate_session_options(opts: dict) -> tuple[dict, list[ShareError]]:
    errors: list[ShareError] = []
    unknown = set(opts) - _SESSION_OPTION_KEYS
    if unknown:
        errors.append(ShareError("options", "unknown_keys", f"unknown option keys: {sorted(unknown)}"))
    out = {
        "mode": opts.get("mode", "live"),
        "max_display_mode": opts.get("max_display_mode", "normal"),
        "include_subagents": bool(opts.get("include_subagents", True)),
        "show_timestamps": bool(opts.get("show_timestamps", True)),
        "show_title": bool(opts.get("show_title", True)),
    }
    if out["mode"] not in ("snapshot", "live"):
        errors.append(ShareError("mode", "invalid", "mode must be 'snapshot' or 'live'"))
    if out["max_display_mode"] not in _DISPLAY_MODES:
        errors.append(ShareError("max_display_mode", "invalid", f"must be one of {_DISPLAY_MODES}"))
    if "frozen_at_line" in opts:
        fal = opts["frozen_at_line"]
        if fal is None:
            # Sentinel meaning "no freeze" (patch_share pops it downstream).
            out["frozen_at_line"] = None
        else:
            try:
                out["frozen_at_line"] = int(fal)
            except (TypeError, ValueError):
                errors.append(ShareError("frozen_at_line", "invalid", "frozen_at_line must be an integer"))
    # Optional owner-set public display title (else the real session title is used).
    title = (opts.get("display_title") or "").strip()
    if title:
        out["display_title"] = title[:200]
    return out, errors


def _validate_artifact_options(opts: dict) -> tuple[dict, list[ShareError]]:
    errors: list[ShareError] = []
    unknown = set(opts) - _ARTIFACT_OPTION_KEYS
    if unknown:
        errors.append(ShareError("options", "unknown_keys", f"unknown option keys: {sorted(unknown)}"))
    # show_title is the master switch (mirrors sessions): off ⇒ the viewer sees a
    # generic label instead of the artifact name/override.
    out: dict = {"show_title": bool(opts.get("show_title", True))}
    # Optional owner-set public display title (else the real bookmark name is used).
    title = (opts.get("display_title") or "").strip()
    if title:
        out["display_title"] = title[:200]
    return out, errors  # snapshot_at is set by the snapshot step, never by the caller


# ── Artifact snapshotting (design §9.2) ─────────────────────────────────────

def confined_snapshot_path(share_id: str, rel_path: str) -> str | None:
    """Resolve ``rel_path`` inside a share's snapshot dir, confined (realpath
    stays a strict sub-path). Returns the realpath or ``None``."""
    root_real = os.path.realpath(str(get_share_snapshot_dir(share_id)))
    abs_path = os.path.realpath(os.path.join(root_real, rel_path))
    if abs_path != root_real and not abs_path.startswith(root_real + os.sep):
        return None
    return abs_path


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(dirpath, name)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def source_updated_at(bookmark) -> datetime | None:
    """Max mtime under the bookmark's *live* directory (the propagate/outdated
    signal). ``None`` if the dir is gone."""
    from twicc.core.services.artifact_bookmark_mutation import confined_artifact_path

    abs_file = confined_artifact_path(bookmark.session_id, bookmark.relative_path)
    if abs_file is None:
        return None
    src_dir = os.path.dirname(abs_file)
    if not os.path.isdir(src_dir):
        return None
    latest = 0.0
    for dirpath, _dirs, files in os.walk(src_dir):
        for name in files:
            try:
                latest = max(latest, os.path.getmtime(os.path.join(dirpath, name)))
            except OSError:
                pass
    return datetime.fromtimestamp(latest, tz=UTC) if latest else None


def snapshot_artifact_share(share) -> str | None:
    """Copy the bookmark's directory into the snapshot dir (atomic: copy to
    ``.tmp`` then swap). Returns an error message or ``None`` on success.
    Sync (FS) — call via ``sync_to_async``."""
    from twicc.core.services.artifact_bookmark_mutation import confined_artifact_path

    bookmark = share.artifact_bookmark
    abs_file = confined_artifact_path(bookmark.session_id, bookmark.relative_path)
    if abs_file is None or not os.path.isfile(abs_file):
        return "artifact file not found"
    src_dir = os.path.dirname(abs_file)
    size = _dir_size(src_dir)
    if size > _MAX_SNAPSHOT_BYTES:
        return f"artifact directory too large to share ({size // (1024 * 1024)} MB > 200 MB)"
    dest = str(get_share_snapshot_dir(share.id))
    tmp = dest + ".tmp"
    if os.path.exists(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src_dir, tmp)
    if os.path.exists(dest):
        old = dest + ".old"
        os.replace(dest, old)
        shutil.rmtree(old, ignore_errors=True)
    os.replace(tmp, dest)
    return None


def remove_snapshot(share_id: str) -> None:
    shutil.rmtree(str(get_share_snapshot_dir(share_id)), ignore_errors=True)


# ── Broadcasts ──────────────────────────────────────────────────────────────

async def broadcast_share_updated(share) -> None:
    from twicc.core.serializers import serialize_share

    layer = get_channel_layer()
    if layer is None:
        return
    payload = await sync_to_async(serialize_share)(share)
    await layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "share_updated", "share": payload},
    })


async def broadcast_share_removed(share_id: str) -> None:
    layer = get_channel_layer()
    if layer is None:
        return
    await layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "share_removed", "share_id": share_id},
    })


# ── Core mutations ──────────────────────────────────────────────────────────

async def create_share(
    kind: str,
    *,
    session=None,
    bookmark=None,
    label: str = "",
    options: dict | None = None,
    password: str | None = None,
    expires_at: datetime | None = None,
    notify_on_view: bool = False,
    created_by_session=None,
) -> ShareMutationResult:
    """Create a share. Validates options, snapshots the artifact (aborting on
    failure), freezes the session line for snapshot mode, then writes + broadcasts."""
    from twicc.core.enums import ShareKind
    from twicc.core.models import Share

    options = dict(options or {})
    if kind == ShareKind.SESSION.value:
        if session is None:
            return ShareMutationResult(False, None, [ShareError("session", "missing", "session required")])
        opts, errors = _validate_session_options(options)
        if errors:
            return ShareMutationResult(False, None, errors)
        if opts["mode"] == "snapshot" and "frozen_at_line" not in opts:
            opts["frozen_at_line"] = session.last_line
    elif kind == ShareKind.ARTIFACT.value:
        if bookmark is None:
            return ShareMutationResult(False, None, [ShareError("bookmark", "missing", "bookmark required")])
        opts, errors = _validate_artifact_options(options)
        if errors:
            return ShareMutationResult(False, None, errors)
    else:
        return ShareMutationResult(False, None, [ShareError("kind", "invalid", f"unknown kind {kind!r}")])

    # Mint id with a small collision-retry loop (id space is 2^32 per token_hex(4)).
    share_id = mint_share_id()
    for _ in range(5):
        exists = await sync_to_async(Share.objects.filter(id=share_id).exists)()
        if not exists:
            break
        share_id = mint_share_id()

    share = Share(
        id=share_id,
        token=mint_token(),
        kind=kind,
        session=session,
        artifact_bookmark=bookmark,
        label=label or "",
        password_hash=hash_password(password) if password else "",
        expires_at=expires_at,
        options=opts,
        notify_on_view=notify_on_view,
        created_by_session=created_by_session,
    )

    if kind == ShareKind.ARTIFACT.value:
        # Take the snapshot BEFORE the row lands so a copy failure aborts creation.
        err = await sync_to_async(snapshot_artifact_share)(share)
        if err:
            return ShareMutationResult(False, None, [ShareError("bookmark", "snapshot_failed", err)])
        share.options = {**opts, "snapshot_at": _now().isoformat()}

    await run_under_db_write_lock(lambda: share.asave(force_insert=True))
    await broadcast_share_updated(share)
    logger.info("[share_create] id=%s kind=%s target=%s", share.id, kind,
                session.id if session else bookmark.id)
    return ShareMutationResult(True, share.id, None)


async def patch_share(share, fields: dict) -> ShareMutationResult:
    """Partial update of label / options / password / expires_at / notify_on_view.
    A password change re-hashes (invalidating viewer grants via the new fingerprint)."""
    from twicc.core.enums import ShareKind

    update_fields: list[str] = []
    if "label" in fields:
        share.label = (fields["label"] or "").strip()
        update_fields.append("label")
    if "notify_on_view" in fields:
        share.notify_on_view = bool(fields["notify_on_view"])
        update_fields.append("notify_on_view")
    if "expires_at" in fields:
        # REST passes a parsed datetime|None; the drop-request/CLI path passes a raw
        # ISO string (JSON has no datetime). Strictly parse the raw string here so the
        # in-memory value stays a datetime and serialize_share never hits ``str.isoformat``.
        raw_exp = fields["expires_at"]
        if isinstance(raw_exp, str):
            parsed, exp_err = _parse_expires({"expires_at": raw_exp})
            if exp_err:
                return ShareMutationResult(False, share.id, [exp_err])
            share.expires_at = parsed
        else:
            share.expires_at = raw_exp
        update_fields.append("expires_at")
    if "password" in fields:
        pw = fields["password"]
        share.password_hash = hash_password(pw) if pw else ""
        update_fields.append("password_hash")
    if "options" in fields:
        raw = dict(fields["options"] or {})
        if share.kind == ShareKind.SESSION.value:
            # Preserve frozen_at_line (only the re-freeze action changes it).
            raw.setdefault("frozen_at_line", share.options.get("frozen_at_line"))
            opts, errors = _validate_session_options(raw)
            # Switching an existing share to snapshot mode with no frozen line yet
            # freezes it at the session's current last_line (mirrors create_share);
            # otherwise a nominal "snapshot" share would keep serving the live tail.
            if (opts.get("mode") == "snapshot" and opts.get("frozen_at_line") is None
                    and share.session is not None):
                opts["frozen_at_line"] = share.session.last_line
            if opts.get("frozen_at_line") is None:
                opts.pop("frozen_at_line", None)
        else:
            # display_title is a free edit; snapshot_at stays owned by propagate.
            opts, errors = _validate_artifact_options(raw)
            opts["snapshot_at"] = share.options.get("snapshot_at")
        if errors:
            return ShareMutationResult(False, share.id, errors)
        share.options = opts
        update_fields.append("options")
    if not update_fields:
        return ShareMutationResult(True, share.id, None)
    update_fields.append("updated_at")
    await run_under_db_write_lock(lambda: share.asave(update_fields=update_fields))
    await broadcast_share_updated(share)
    return ShareMutationResult(True, share.id, None)


async def propagate_share(share) -> ShareMutationResult:
    """Session: re-freeze to the current last_line. Artifact: re-snapshot + bump
    snapshot_at (atomic swap). Broadcasts."""
    from twicc.core.enums import ShareKind

    if share.kind == ShareKind.SESSION.value:
        if share.options.get("mode") != "snapshot":
            return ShareMutationResult(False, share.id,
                                       [ShareError("mode", "not_snapshot", "only snapshot shares re-freeze")])
        share.session = await sync_to_async(lambda: share.session)()
        fresh = await sync_to_async(type(share.session).objects.get)(id=share.session_id)
        share.options = {**share.options, "frozen_at_line": fresh.last_line}
    else:
        err = await sync_to_async(snapshot_artifact_share)(share)
        if err:
            return ShareMutationResult(False, share.id, [ShareError("bookmark", "snapshot_failed", err)])
        share.options = {**share.options, "snapshot_at": _now().isoformat()}
    await run_under_db_write_lock(lambda: share.asave(update_fields=["options", "updated_at"]))
    await broadcast_share_updated(share)
    return ShareMutationResult(True, share.id, None)


async def revoke_share(share, *, revoked: bool = True) -> ShareMutationResult:
    share.revoked_at = _now() if revoked else None
    await run_under_db_write_lock(lambda: share.asave(update_fields=["revoked_at", "updated_at"]))
    await broadcast_share_updated(share)
    return ShareMutationResult(True, share.id, None)


async def delete_share(share) -> ShareMutationResult:
    from twicc.core.enums import ShareKind

    share_id = share.id
    kind = share.kind
    await run_under_db_write_lock(lambda: share.adelete())
    if kind == ShareKind.ARTIFACT.value:
        await sync_to_async(remove_snapshot)(share_id)
    await broadcast_share_removed(share_id)
    return ShareMutationResult(True, share_id, None)


# ── Agent gate (agent-sharing design §7.1) ──────────────────────────────────

async def _resolve_caller_session(payload: dict):
    """§7.1 step 1: absent key → human (None). A well-typed but UNKNOWN id also
    resolves to None → human, current behaviour. Type errors are caught by
    ``share_agent_gate.caller_type_error`` BEFORE this runs (this lookup is
    itself the first ORM access)."""
    from twicc.core.models import Session

    from twicc.mcp.identity import external_caller
    if external_caller.get() is not None:
        # Explicit owner-granted full access, without a fabricated internal session.
        # The MCP dispatcher records the connection and share operation durably.
        return None
    cid = payload.get("caller_session_id")
    if not isinstance(cid, str):
        return None
    return await sync_to_async(lambda: Session.objects.filter(id=cid).first())()


def _agent_disabled_error(kind: str) -> ShareError:
    return ShareError(
        "settings", "agent_sharing_disabled",
        f"agent-created {kind} shares are disabled; ask the user to enable them "
        f"in Settings → Sharing before retrying",
    )


def _kind_setting_on(kind: str) -> bool:
    from twicc.core.services import share_agent_gate
    from twicc.synced_settings import read_synced_settings

    return bool(read_synced_settings().get(share_agent_gate.setting_key_for(kind), False))


async def _caller_scope_ids(caller) -> set[str]:
    from twicc.core.services.spawn_scope import descendant_ids

    return {caller.id} | await sync_to_async(descendant_ids)(caller.id)


async def _agent_gate_for_loaded_share(caller, share, *, check_provenance: bool) -> list[ShareError]:
    """Steps 4-5 for the five share-loading ops. ``check_provenance=False`` is
    revoke's A7 exception: any provenance, the kind setting still applies."""
    if caller is None:
        return []
    if not _kind_setting_on(share.kind):
        return [_agent_disabled_error(share.kind)]
    if not check_provenance:
        return []
    allowed = await _caller_scope_ids(caller)
    if share.created_by_session_id is None or share.created_by_session_id not in allowed:
        # Provenance wording, never target wording: a descendant touching a
        # parent-created share OF ITSELF fails here while the target is its
        # own session (§7.5).
        return [ShareError(
            "share_id", "out_of_scope",
            "this share was created outside your spawn subtree "
            "(or by the user); you can manage only shares created by yourself "
            "or any session in your spawn subtree",
        )]
    return []


# ── Drop-request glue (kind="share:*") ──────────────────────────────────────

async def _resolve_target_from_payload(payload: dict):
    """Return (kind, session, bookmark, errors)."""
    from twicc.core.enums import ShareKind
    from twicc.core.models import ArtifactBookmark, Session

    kind = payload.get("kind_target") or payload.get("share_kind")
    if kind == ShareKind.SESSION.value:
        sid = (payload.get("session_id") or "").strip()
        session = await sync_to_async(lambda: Session.objects.filter(id=sid).first())()
        if session is None:
            return kind, None, None, [ShareError("session_id", "not_found", f"session {sid!r} not found")]
        return kind, session, None, []
    if kind == ShareKind.ARTIFACT.value:
        bid = payload.get("bookmark_id")
        bookmark = await sync_to_async(lambda: ArtifactBookmark.objects.filter(id=bid).first())()
        if bookmark is None:
            return kind, None, None, [ShareError("bookmark_id", "not_found", f"bookmark {bid!r} not found")]
        return kind, None, bookmark, []
    return kind, None, None, [ShareError("kind", "invalid", f"unknown kind {kind!r}")]


def _parse_expires(payload: dict) -> tuple[datetime | None, ShareError | None]:
    """Strict expiry parse (§7.2 defect fix): absent/None/"" → no expiry; a
    non-empty value must parse under ``datetime.fromisoformat`` or the caller
    gets ``expires_at``/``invalid`` — never a silently never-expiring link."""
    raw = payload.get("expires_at")
    if raw is None or raw == "":
        return None, None
    try:
        return datetime.fromisoformat(raw), None
    except (ValueError, TypeError):
        return None, ShareError(
            "expires_at", "invalid",
            f"invalid expires_at {raw!r}: use an ISO 8601 datetime, "
            f"e.g. 2026-12-31T23:59:00+00:00",
        )


async def create_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.enums import ShareKind
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_create(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)

    kind, session, bookmark, errors = await _resolve_target_from_payload(payload)
    if errors:
        return ShareMutationResult(False, None, errors)

    if caller is None:
        # Preserve Task 5's human-path precedence: strict expiry is checked
        # before create_share converts options to a dict.
        expires_at, exp_err = _parse_expires(payload)
        if exp_err:
            return ShareMutationResult(False, None, [exp_err])
        options = payload.get("options") or {}
    else:
        # Layer 1 established that options is an object before this copy.
        options = dict(payload.get("options") or {})
        if not _kind_setting_on(kind):
            return ShareMutationResult(False, None, [_agent_disabled_error(kind)])
        target_session_id = session.id if session is not None else bookmark.session_id
        allowed = await _caller_scope_ids(caller)
        if target_session_id not in allowed:
            field = "session_id" if session is not None else "bookmark_id"
            return ShareMutationResult(False, None, [ShareError(
                field, "out_of_scope",
                "the target belongs to another session, outside your own spawn "
                "subtree; you can share only your own session or any session "
                "in your spawn subtree",
            )])
        if options.get("max_display_mode") == "debug":
            return ShareMutationResult(False, None, [ShareError(
                "max_display_mode", "display_mode_forbidden",
                "the debug display mode is not available to agents; allowed: "
                "conversation, simplified, normal",
            )])
        from twicc.synced_settings import read_synced_settings
        if not usable_public_origin(read_synced_settings().get("shareBaseUrl")):
            return ShareMutationResult(False, None, [ShareError(
                "share_base_url", "share_host_unset",
                "no share host is configured, so the link would resolve nowhere; "
                "ask the user to set one in Settings → Sharing first",
            )])
        if kind == ShareKind.SESSION.value and "mode" not in options:
            # A9: the agent default is a frozen snapshot; --live stays explicit.
            options["mode"] = "snapshot"
        expires_at, exp_err = _parse_expires(payload)
        if exp_err:
            return ShareMutationResult(False, None, [exp_err])
    return await create_share(
        kind, session=session, bookmark=bookmark,
        label=payload.get("label") or "",
        options=options,
        password=payload.get("password") or None,
        expires_at=expires_at,
        notify_on_view=bool(payload.get("notify_on_view", False)),
        created_by_session=caller,
    )


async def _load_share_or_error(payload: dict):
    from twicc.core.models import Share

    share_id = (payload.get("share_id") or "").strip()
    share = await sync_to_async(
        lambda: Share.objects.select_related(
            "session", "artifact_bookmark", "created_by_session",
        ).filter(id=share_id).first()
    )()
    if share is None:
        return None, ShareMutationResult(False, None, [ShareError("share_id", "not_found", f"share {share_id!r} not found")])
    return share, None


async def update_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_update(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    gate_errors = await _agent_gate_for_loaded_share(caller, share, check_provenance=True)
    if gate_errors:
        return ShareMutationResult(False, share.id, gate_errors)
    if caller is not None and (payload.get("fields") or {}).get("password") == "":
        return ShareMutationResult(False, share.id, [ShareError(
            "password", "field_forbidden",
            "agents may set or replace a share password, never clear it; "
            "clearing is available from the human CLI or the owner UI",
        )])
    return await patch_share(share, payload.get("fields") or {})


async def revoke_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_simple(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    gate_errors = await _agent_gate_for_loaded_share(
        caller, share, check_provenance=False,
    )
    if gate_errors:
        return ShareMutationResult(False, share.id, gate_errors)
    return await revoke_share(share, revoked=True)


async def unrevoke_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_simple(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    gate_errors = await _agent_gate_for_loaded_share(
        caller, share, check_provenance=True,
    )
    if gate_errors:
        return ShareMutationResult(False, share.id, gate_errors)
    return await revoke_share(share, revoked=False)


async def delete_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_simple(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    gate_errors = await _agent_gate_for_loaded_share(
        caller, share, check_provenance=True,
    )
    if gate_errors:
        return ShareMutationResult(False, share.id, gate_errors)
    return await delete_share(share)


async def propagate_share_from_payload(payload: dict) -> ShareMutationResult:
    from twicc.core.services import share_agent_gate

    err = share_agent_gate.caller_type_error(payload)
    if err:
        return ShareMutationResult(False, None, [err])
    caller = await _resolve_caller_session(payload)
    if caller is not None:
        shape_errors = share_agent_gate.validate_simple(payload)
        if shape_errors:
            return ShareMutationResult(False, None, shape_errors)
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    gate_errors = await _agent_gate_for_loaded_share(
        caller, share, check_provenance=True,
    )
    if gate_errors:
        return ShareMutationResult(False, share.id, gate_errors)
    return await propagate_share(share)
