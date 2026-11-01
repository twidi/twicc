# Sharing — Implementation Plan (ready-to-execute, code included)

**Status:** consolidated on the resolved decisions (design §18, 2026-07-05); single implementation path. This revision embeds the **full code to write** so execution is "apply file by file". Code blocks are the source of truth; prose only frames them.
**Date:** 2026-07-05 (code-complete revision 2026-07-06)
**Design:** `2026-07-05-sharing-design.md` (read it first; this plan does not restate rationale).

> Conventions honoured throughout: services in `core/services/` shared by REST + drop-requests; writes under `run_under_db_write_lock`; broadcasts on the `updates` group; `orjson` backend-side; `NamedTuple` for immutable results; all code/UI/docs in English; migrations created but **never run by the agent** (remind the user); Web Awesome components imported in `frontend/src/main.js` for SPA additions (the share bundle imports its own). Every Bash command in a worktree is prefixed `cd <worktree> &&`.

**Decisions applied:** O1 token-is-the-credential (no instance-password gate on `/share/`) · O2 plaintext token · O3 mandatory host-only share origin (settings-driven) · O4 dedicated WS · O5 full CLI for humans, **not exposed to agents** (no skill, no MCP tools) · D6 single broker policy (owner allowlist, server-enforced, no viewer consent) · D7 artifact = snapshot + explicit propagation.

**How each file entry reads:** `NEW` = create the file with the given content verbatim. `EDIT` = apply the shown anchor→replacement to the existing file (the anchor is a unique existing substring). Line numbers in prose are indicative (they drift); match on the anchor text.

---

## Phase 1 — Data model, tokens, service, serializers

### 1.1 `EDIT` `src/twicc/core/enums.py` — add `ShareKind`

Anchor (end of file, after the `Provider` class):
```python
class Provider(StrEnum):
    """Backend provider that produced a session."""
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
```
Append after it:
```python


class ShareKind(StrEnum):
    """What a share link targets."""
    SESSION = "session"
    ARTIFACT = "artifact"
```

### 1.2 `EDIT` `src/twicc/core/models.py` — add `Share` + `ShareAccess`

Add the import at the top (anchor `from twicc.core.enums import ItemKind, Provider`):
```python
from twicc.core.enums import ItemKind, Provider, ShareKind
```

Append these two models at the end of the file (after `class Command`):
```python
def generate_share_id() -> str:
    """Non-secret admin handle for a Share (CLI, logs, UI): ``shr_<hex8>``.

    Collision handling lives in the service (retry loop), not here — a
    ``default=`` callable can't retry against the DB cheaply.
    """
    import secrets
    return "shr_" + secrets.token_hex(4)


class Share(models.Model):
    """One capability URL targeting one object (session or artifact bookmark).

    The ``token`` is the URL secret (256-bit, plaintext — O2); ``id`` is a short
    non-secret handle. Exactly one of ``session`` / ``artifact_bookmark`` is set,
    matching ``kind`` (DB CheckConstraint). Revoking keeps the row + counters;
    deleting removes it (and its snapshot dir). CASCADE on the target: a share
    must not outlive what it exposes. See design §5.
    """

    id = models.CharField(max_length=16, primary_key=True, default=generate_share_id)
    token = models.CharField(max_length=64, unique=True, db_index=True)
    kind = models.CharField(max_length=16, choices=[(k.value, k.value) for k in ShareKind])
    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, null=True, blank=True, related_name="shares",
    )
    artifact_bookmark = models.ForeignKey(
        ArtifactBookmark, on_delete=models.CASCADE, null=True, blank=True, related_name="shares",
    )
    label = models.CharField(max_length=255, blank=True, default="")
    # Per-link password (same PBKDF2 format as auth/hashers). Empty = none.
    password_hash = models.CharField(max_length=255, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    # Kind-specific options (design §5.2). Validated by the service, never trusted raw.
    options = models.JSONField(default=dict, blank=True)
    view_count = models.PositiveIntegerField(default=0)
    last_viewed_at = models.DateTimeField(null=True, blank=True)
    notify_on_view = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["kind"], name="idx_share_kind"),
        ]
        constraints = [
            models.CheckConstraint(
                name="share_target_matches_kind",
                condition=(
                    Q(kind="session", session__isnull=False, artifact_bookmark__isnull=True)
                    | Q(kind="artifact", artifact_bookmark__isnull=False, session__isnull=True)
                ),
            ),
        ]

    def status(self, now: datetime | None = None) -> str:
        """``active`` | ``revoked`` | ``expired`` (revoked wins over expired)."""
        if self.revoked_at is not None:
            return "revoked"
        now = now or datetime.now(tz=timezone.utc)
        if self.expires_at is not None and self.expires_at <= now:
            return "expired"
        return "active"

    def is_active(self, now: datetime | None = None) -> bool:
        return self.status(now) == "active"

    def __str__(self):
        return f"Share[{self.kind}] {self.id} -> {self.session_id or self.artifact_bookmark_id}"


class ShareAccess(models.Model):
    """One row per share *page view* (design §13). Opportunistically pruned to the
    newest 500 rows per share on insert; ``Share.view_count`` / ``last_viewed_at``
    stay denormalised for cheap list display."""

    share = models.ForeignKey(Share, on_delete=models.CASCADE, related_name="accesses")
    at = models.DateTimeField(auto_now_add=True)
    ip = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-at"]
        indexes = [
            models.Index(fields=["share", "-at"], name="idx_shareaccess_share_at"),
        ]

    def __str__(self):
        return f"ShareAccess {self.share_id} @ {self.at.isoformat()}"
```

### 1.3 `EDIT` `src/twicc/paths.py` — snapshot dir helper

After `get_session_artifacts_dir` (anchor `    return get_artifacts_dir() / session_id`):
```python


def get_shares_dir() -> Path:
    """Root of per-share artifact snapshots (``<data_dir>/shares/``)."""
    return get_data_dir() / "shares"


def get_share_snapshot_dir(share_id: str) -> Path:
    """Snapshot copy dir for one artifact share (``<data_dir>/shares/<share_id>/``).
    Path only — the service creates/removes it."""
    return get_shares_dir() / share_id
```

### 1.4 `NEW` `src/twicc/core/services/share_tokens.py`

```python
"""Token minting + resolution for shares.

The token is the URL secret (O2: stored plaintext). Resolution fetches by the
indexed ``token`` column, then re-checks with ``hmac.compare_digest`` so the
comparison stays constant-time even though the column is indexed. Callers apply
the revoked/expired policy themselves (uniform 404) — this only resolves identity.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from asgiref.sync import sync_to_async


def mint_token() -> str:
    """256-bit URL-safe secret. ~43 chars, matches the ``[A-Za-z0-9_-]{20,}`` route regex."""
    return secrets.token_urlsafe(32)


def mint_share_id() -> str:
    return "shr_" + secrets.token_hex(4)


def password_fingerprint(password_hash: str) -> str:
    """Short one-way digest of a share's password hash — stored in the viewer's
    Django session so rotating the password invalidates the grant (design §7.2).
    Mirrors ``auth/session_auth.compute_fingerprint``."""
    if not password_hash:
        return ""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def resolve_share(token: str):
    """Return the Share for ``token`` (constant-time compare) or ``None``.
    Sync — call via ``sync_to_async`` from async views. Does NOT apply
    revoked/expired policy."""
    from twicc.core.models import Share

    if not token:
        return None
    share = Share.objects.filter(token=token).first()
    if share is None:
        return None
    if not hmac.compare_digest(share.token, token):
        return None
    return share


async def aresolve_share(token: str):
    return await sync_to_async(resolve_share)(token)
```

### 1.5 `NEW` `src/twicc/core/services/share_mutation.py`

```python
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
from datetime import datetime, timezone
from typing import NamedTuple

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.auth.hashers import hash_password
from twicc.core.services.share_tokens import mint_share_id, mint_token
from twicc.paths import get_share_snapshot_dir
from twicc.providers.db_writer import run_under_db_write_lock

logger = logging.getLogger(__name__)

# Snapshot size guard (design §9.2).
_MAX_SNAPSHOT_BYTES = 200 * 1024 * 1024

# Option key allowlists per kind (design §5.2). Unknown keys are rejected.
_SESSION_OPTION_KEYS = frozenset({
    "mode", "frozen_at_line", "max_display_mode", "include_subagents",
    "show_costs", "show_timestamps", "show_title", "display_title",
})
_ARTIFACT_OPTION_KEYS = frozenset({"snapshot_at", "display_title"})
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
    return datetime.now(tz=timezone.utc)


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
        "show_costs": bool(opts.get("show_costs", False)),
        "show_timestamps": bool(opts.get("show_timestamps", True)),
        "show_title": bool(opts.get("show_title", True)),
    }
    if out["mode"] not in ("snapshot", "live"):
        errors.append(ShareError("mode", "invalid", "mode must be 'snapshot' or 'live'"))
    if out["max_display_mode"] not in _DISPLAY_MODES:
        errors.append(ShareError("max_display_mode", "invalid", f"must be one of {_DISPLAY_MODES}"))
    if "frozen_at_line" in opts:
        out["frozen_at_line"] = opts["frozen_at_line"]
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
    out: dict = {}
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
    return datetime.fromtimestamp(latest, tz=timezone.utc) if latest else None


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
    )

    if kind == ShareKind.ARTIFACT.value:
        # Take the snapshot BEFORE the row lands so a copy failure aborts creation.
        err = await sync_to_async(snapshot_artifact_share)(share)
        if err:
            return ShareMutationResult(False, None, [ShareError("bookmark", "snapshot_failed", err)])
        share.options = {"snapshot_at": _now().isoformat()}

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
        share.expires_at = fields["expires_at"]
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
        share.options = {"snapshot_at": _now().isoformat()}
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


def _parse_expires(payload: dict) -> datetime | None:
    raw = payload.get("expires_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


async def create_share_from_payload(payload: dict) -> ShareMutationResult:
    kind, session, bookmark, errors = await _resolve_target_from_payload(payload)
    if errors:
        return ShareMutationResult(False, None, errors)
    return await create_share(
        kind, session=session, bookmark=bookmark,
        label=payload.get("label") or "",
        options=payload.get("options") or {},
        password=payload.get("password") or None,
        expires_at=_parse_expires(payload),
        notify_on_view=bool(payload.get("notify_on_view", False)),
    )


async def _load_share_or_error(payload: dict):
    from twicc.core.models import Share

    share_id = (payload.get("share_id") or "").strip()
    share = await sync_to_async(
        lambda: Share.objects.select_related("session", "artifact_bookmark").filter(id=share_id).first()
    )()
    if share is None:
        return None, ShareMutationResult(False, None, [ShareError("share_id", "not_found", f"share {share_id!r} not found")])
    return share, None


async def update_share_from_payload(payload: dict) -> ShareMutationResult:
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    return await patch_share(share, payload.get("fields") or {})


async def revoke_share_from_payload(payload: dict) -> ShareMutationResult:
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    return await revoke_share(share, revoked=True)


async def unrevoke_share_from_payload(payload: dict) -> ShareMutationResult:
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    return await revoke_share(share, revoked=False)


async def delete_share_from_payload(payload: dict) -> ShareMutationResult:
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    return await delete_share(share)


async def propagate_share_from_payload(payload: dict) -> ShareMutationResult:
    share, err = await _load_share_or_error(payload)
    if err:
        return err
    return await propagate_share(share)
```

### 1.6 `EDIT` `src/twicc/core/serializers.py` — share serializers

Append at the end of the file:
```python
def serialize_share(share):
    """Owner-facing full serializer. Query-light: reads target refs via *_id and
    only touches the related row's already-loaded fields (callers select_related).
    Computes ``url_path`` and, for artifact shares, ``source_updated_at`` (outdated
    badge) — the latter walks the FS, so callers on a hot path may skip it by
    passing a pre-fetched share whose related bookmark is None."""
    from twicc.core.enums import ShareKind

    data = {
        "id": share.id,
        "token": share.token,  # plaintext (O2) — owner UI needs it to build the URL
        "kind": share.kind,
        "label": share.label,
        "has_password": bool(share.password_hash),
        "expires_at": share.expires_at.isoformat() if share.expires_at else None,
        "revoked_at": share.revoked_at.isoformat() if share.revoked_at else None,
        "status": share.status(),
        "options": share.options,
        "view_count": share.view_count,
        "last_viewed_at": share.last_viewed_at.isoformat() if share.last_viewed_at else None,
        "notify_on_view": share.notify_on_view,
        "created_at": share.created_at.isoformat() if share.created_at else None,
        "updated_at": share.updated_at.isoformat() if share.updated_at else None,
        "url_path": f"/share/{share.token}/",
    }
    if share.kind == ShareKind.SESSION.value:
        data["session_id"] = share.session_id
        sess = share.session if share.session_id else None
        data["project_id"] = sess.project_id if sess else None
        data["target_title"] = (sess.title if sess else None)
    else:
        data["bookmark_id"] = share.artifact_bookmark_id
        bm = share.artifact_bookmark if share.artifact_bookmark_id else None
        data["target_name"] = bm.name if bm else None
        data["allowed_hosts"] = bm.allowed_hosts if bm else {}
        if bm is not None:
            from twicc.core.services.share_mutation import source_updated_at
            src = source_updated_at(bm)
            data["source_updated_at"] = src.isoformat() if src else None
    return data


def serialize_share_public_meta(share):
    """Viewer-facing meta (design §6.2). Never label, never counters, never real
    bookmark/project ids. Session id IS included (needed for media URL rewriting;
    grants nothing — every real route is gated). Title = the `display_title` override,
    else the real session title (per `show_title`) / bookmark name; costs per `show_costs`."""
    from twicc.core.enums import ShareKind

    opts = share.options or {}
    if share.kind == ShareKind.SESSION.value:
        sess = share.session
        frozen = opts.get("frozen_at_line")
        last_line = sess.last_line if sess else 0
        if opts.get("mode") == "snapshot" and frozen is not None:
            last_line = min(last_line, frozen)
        data = {
            "kind": "session",
            "session_id": share.session_id,
            "provider": sess.provider if sess else None,
            "last_line": last_line,
            "mode": opts.get("mode", "live"),
            "max_display_mode": opts.get("max_display_mode", "normal"),
            "include_subagents": opts.get("include_subagents", True),
            "show_timestamps": opts.get("show_timestamps", True),
            "created_at": sess.created_at.isoformat() if sess and sess.created_at else None,
            "last_updated_at": sess.last_updated_at.isoformat() if sess and sess.last_updated_at else None,
        }
        # Public title: owner override wins; else the real session title, unless the
        # owner hid it via show_title.
        display_title = (opts.get("display_title") or "").strip()
        if display_title:
            data["title"] = display_title
        elif opts.get("show_title", True):
            data["title"] = sess.title if sess else None
        if opts.get("show_costs", False):
            data["total_cost"] = float(sess.total_cost) if sess and sess.total_cost else None
        return data
    # Artifact: owner override, else the real bookmark name.
    display_title = (opts.get("display_title") or "").strip()
    bookmark = share.artifact_bookmark
    return {
        "kind": "artifact",
        "snapshot_at": opts.get("snapshot_at"),
        "title": display_title or (bookmark.name if bookmark else None),
    }
```

### 1.7 `NEW` `src/twicc/core/migrations/0124_share.py`

Generate rather than hand-write, so field kwargs match Django exactly:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/sharing
TWICC_DATA_DIR=$PWD uv run python -m django makemigrations core --settings=twicc.settings --name share
```
Expected output: `src/twicc/core/migrations/0124_share.py` (auto-numbered after the current latest `0123_session_browser_url`, on which it depends) creating `Share` + `ShareAccess` with the CheckConstraint and indexes. **Do not run `migrate`** — remind the user (devctl applies it at backend start).

### 1.8 Tests (`tests/test_share_model.py`, `tests/test_share_mutation.py`)

Cover: CheckConstraint rejects a kind/target mismatch; `resolve_share` returns None for unknown; `status()` transitions (active/revoked/expired); option validation (unknown key rejected, mode/display bounds); snapshot size cap; `create_share` freezes `frozen_at_line` at `last_line` for snapshot mode. Follow the existing pytest-django layout (`@pytest.mark.django_db`, `async def` + `pytest.mark.asyncio` where the service is async).

---

## Phase 2 — Public share routes (backend; curl-testable before any frontend)

### 2.1 `EDIT` `src/twicc/auth/middleware.py` — exempt `/share/`

Anchor:
```python
PUBLIC_PATHS = (
    "/api/auth/",
    "/static/",
    "/artifacts/auth",
)
```
Replace with:
```python
PUBLIC_PATHS = (
    "/api/auth/",
    "/static/",
    "/artifacts/auth",
    # Public share surface (O1: the token is the credential; per-link password
    # gating lives inside the share views). ``/_twicc/share/`` is the built
    # viewer bundle (no data). Both exempt from the instance-password gate AND
    # the local-only remote gate — ``_is_data_path`` returns False for them.
    "/share/",
    "/_twicc/share/",
)
```
No other change: `_is_data_path` already returns `False` for anything under `PUBLIC_PATHS`, so `remote_access_blocked` never fires for share URLs (O1). Per-link authorization is enforced inside the share views (2.3).

### 2.2 `NEW` `src/twicc/share/__init__.py`

```python
"""Public, read-only share surface: session transcripts and artifact snapshots
served under ``/share/<token>/`` by capability URL. See
``docs/plans/2026-07-05-sharing-design.md``."""
```

### 2.3 `NEW` `src/twicc/share/resolver.py`

```python
"""Resolve a share token to a request context, applying the uniform-404 policy
and per-link password gate (design §6.1/§7)."""

from __future__ import annotations

from typing import NamedTuple

from django.http import Http404, HttpResponseRedirect, JsonResponse
from urllib.parse import urlencode

from twicc.core.services.share_tokens import aresolve_share, password_fingerprint

SHARE_GRANTS_SESSION_KEY = "share_grants"


class ShareContext(NamedTuple):
    share: object
    session: object | None
    bookmark: object | None
    options: dict


class SharePasswordRequired(Exception):
    """Raised when a per-link password is set and the viewer has no valid grant.
    Carried up to the view, which renders the redirect (HTML) or 401 (API)."""

    def __init__(self, token: str):
        self.token = token


async def resolve_or_404(request, token: str) -> ShareContext:
    """Resolve ``token`` → active share, enforcing revoked/expired (uniform 404)
    and the per-link password grant. Raises ``Http404`` or ``SharePasswordRequired``."""
    from asgiref.sync import sync_to_async

    share = await aresolve_share(token)
    if share is None or not share.is_active():
        raise Http404("This link is not available.")

    # Hydrate the related target (select_related equivalent for async).
    if share.kind == "session":
        session = await sync_to_async(lambda: share.session)()
        bookmark = None
        if session is None or session.stale:
            raise Http404("This link is not available.")
    else:
        session = None
        bookmark = await sync_to_async(lambda: share.artifact_bookmark)()
        if bookmark is None:
            raise Http404("This link is not available.")

    # Per-link password grant (design §7.2).
    if share.password_hash:
        grants = await request.session.aget(SHARE_GRANTS_SESSION_KEY) or {}
        if grants.get(share.id) != password_fingerprint(share.password_hash):
            raise SharePasswordRequired(token)

    return ShareContext(share=share, session=session, bookmark=bookmark, options=share.options or {})


def password_required_response(request, token: str):
    """Render the response for a missing password grant: redirect to the share
    password page for a document navigation, 401 JSON for an API/asset request."""
    dest = request.headers.get("Sec-Fetch-Dest", "")
    accept = request.headers.get("Accept", "")
    wants_html = dest in ("document", "iframe") or "text/html" in accept
    if wants_html:
        query = urlencode({"redirect": request.get_full_path()})
        return HttpResponseRedirect(f"/share/{token}/auth?{query}")
    return JsonResponse({"error": "share_password_required"}, status=401)
```

### 2.4 `NEW` `src/twicc/share/headers.py`

```python
"""Response hardening applied to every ``/share/`` response (design §6.1)."""

from __future__ import annotations


def apply_share_headers(response):
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response
```

### 2.5 `NEW` `src/twicc/share/display.py`

```python
"""Server-side content filtering for shared sessions (design §6.2). The display
ceiling and frozen line are enforced in SQL so nothing above them ever reaches
the viewer's network tab."""

from __future__ import annotations

from django.db.models import Q

# ItemDisplayLevel: ALWAYS=1, COLLAPSIBLE=2, DEBUG_ONLY=3.
# A max_display_mode caps which levels are visible. Only "debug" exposes level 3.
_CEILING = {
    "conversation": 2,
    "simplified": 2,
    "normal": 2,
    "debug": 3,
}


def display_ceiling(max_display_mode: str) -> int:
    return _CEILING.get(max_display_mode, 2)


def filtered_items_qs(session, *, max_display_mode: str, max_line: int | None, extra: Q | None = None):
    """Base queryset for a shared session's items, ceiling- and frozen-line-filtered.
    ``display_level`` NULL rows (uncomputed) are excluded except in debug (they'd
    only be visible there anyway)."""
    ceiling = display_ceiling(max_display_mode)
    qs = session.items.all()
    if ceiling < 3:
        qs = qs.filter(display_level__isnull=False, display_level__lte=ceiling)
    if max_line is not None:
        qs = qs.filter(line_num__lte=max_line)
    if extra is not None:
        qs = qs.filter(extra)
    return qs


async def is_descendant_of(candidate, root, *, max_hops: int = 16) -> bool:
    """Whether ``candidate`` is a subagent descendant of ``root`` — walk
    ``parent_session`` up to ``max_hops`` (with a ``spawn_root`` shortcut)."""
    from asgiref.sync import sync_to_async

    if candidate.id == root.id:
        return False
    if candidate.spawn_root_id and candidate.spawn_root_id == root.id:
        return True
    node = candidate
    for _ in range(max_hops):
        parent_id = node.parent_session_id
        if parent_id is None:
            return False
        if parent_id == root.id:
            return True
        node = await sync_to_async(lambda pid=parent_id: type(root).objects.filter(id=pid).first())()
        if node is None:
            return False
    return False
```

### 2.6 `NEW` `src/twicc/share/html.py`

```python
"""Server-rendered HTML shells for the share pages (data-island pattern, like
``artifacts/broker_html.py``)."""

from __future__ import annotations

import orjson
from django.http import HttpResponse
from django.utils.cache import add_never_cache_headers

from twicc.artifacts.broker_html import ARTIFACT_CSP, inject_broker_shim
from twicc.share.headers import apply_share_headers

SHARE_SESSION_JS_URL = "/_twicc/share/share-session.js"
SHARE_SESSION_CSS_URL = "/_twicc/share/share-session.css"
_DATA_ID = "twicc-share-data"


def _page(data: dict, *, title: str) -> bytes:
    payload = orjson.dumps(data).decode().replace("<", "\\u003c")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        f"<title>{title}</title>\n"
        f'<link rel="stylesheet" href="{SHARE_SESSION_CSS_URL}">\n'
        f'<script type="application/json" id="{_DATA_ID}">{payload}</script>\n'
        f'<script type="module" src="{SHARE_SESSION_JS_URL}"></script>\n'
        "</head>\n<body class=\"loading\">\n<div id=\"app\"></div>\n</body>\n</html>\n"
    ).encode()


def share_page_response(*, token_path: str, meta: dict, mode: str = "session") -> HttpResponse:
    """The shared-session viewer page (``mode="session"``) or the markdown/mermaid
    doc view (``mode="doc"``). ``meta`` is the public meta island."""
    data = {"tokenPath": token_path, "mode": mode, "meta": meta}
    resp = HttpResponse(_page(data, title="Shared session"), content_type="text/html; charset=utf-8")
    resp["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resp)
    return apply_share_headers(resp)


def share_recent_response() -> HttpResponse:
    """The share host homepage (``/share/`` — no token): a client-side list of the
    shares this browser has opened, read from ``localStorage`` on the share origin
    (design §12). Carries NO server data — no per-viewer tracking exists."""
    resp = HttpResponse(_page({"mode": "recent"}, title="Shared with you"),
                        content_type="text/html; charset=utf-8")
    resp["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resp)
    return apply_share_headers(resp)


def share_artifact_shell_response(*, token_path: str, inner_doc_url: str, snapshot_at, title: str = "") -> HttpResponse:
    """Trusted shell for an HTML artifact share: iframes the CSP-wrapped inner doc,
    mounts the prompt-less broker (design §9.3). Reuses the artifact-shell bundle
    in share mode via its data island. ``title`` only seeds the recent-shares list."""
    data = {"mode": "share", "tokenPath": token_path, "innerDocUrl": inner_doc_url,
            "snapshotAt": snapshot_at, "title": title}
    payload = orjson.dumps(data).decode().replace("<", "\\u003c")
    html = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        "<title>Shared artifact</title>\n"
        '<link rel="stylesheet" href="/_twicc/artifact-shell/shell.css">\n'
        f'<script type="application/json" id="twicc-shell-data">{payload}</script>\n'
        '<script type="module" src="/_twicc/artifact-shell/shell.js"></script>\n'
        "</head>\n<body>\n<div id=\"app\"></div>\n</body>\n</html>\n"
    ).encode()
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resp)
    return apply_share_headers(resp)


def share_artifact_doc_response(html: bytes) -> HttpResponse:
    """The artifact's inner document: shim injected + strict CSP, share-hardened."""
    resp = HttpResponse(inject_broker_shim(html), content_type="text/html; charset=utf-8")
    resp["Content-Security-Policy"] = ARTIFACT_CSP
    resp["X-Content-Type-Options"] = "nosniff"
    add_never_cache_headers(resp)
    return apply_share_headers(resp)
```

### 2.7 `NEW` `src/twicc/share/session_views.py`

```python
"""Session-share HTTP views. Thin wrappers: resolve context, then delegate to the
shared item-query helpers, applying the display ceiling + frozen line."""

from __future__ import annotations

import os

from asgiref.sync import sync_to_async
from django.http import Http404, HttpResponseNotAllowed, JsonResponse

from twicc.core.serializers import (
    serialize_session_item,
    serialize_session_item_metadata,
)
from twicc.share.display import filtered_items_qs, is_descendant_of
from twicc.share.headers import apply_share_headers
from twicc.share.html import share_page_response
from twicc.share.resolver import SharePasswordRequired, password_required_response, resolve_or_404


def _json(data, *, safe=True):
    return apply_share_headers(JsonResponse(data, safe=safe))


async def _ctx(request, token):
    """Resolve or translate the password/404 outcome into a response the caller
    returns directly. Returns (ctx, None) or (None, response)."""
    try:
        ctx = await resolve_or_404(request, token)
    except SharePasswordRequired:
        return None, password_required_response(request, token)
    if ctx.share.kind != "session":
        raise Http404("This link is not available.")
    return ctx, None


def _parse_ranges(request):
    q = []
    from django.db.models import Q
    for r in request.GET.getlist("range"):
        try:
            if ":" not in r:
                q.append(Q(line_num=int(r)))
            else:
                lo, hi = r.split(":", 1)
                lo = int(lo) if lo else None
                hi = int(hi) if hi else None
                if lo is not None and hi is not None:
                    q.append(Q(line_num__gte=lo, line_num__lte=hi))
                elif lo is not None:
                    q.append(Q(line_num__gte=lo))
                elif hi is not None:
                    q.append(Q(line_num__lte=hi))
        except ValueError:
            continue
    combined = None
    for cond in q:
        combined = cond if combined is None else (combined | cond)
    return combined


async def _resolve_shared_subagent(ctx, sid):
    """Return the descendant subagent Session or raise 404. Only reachable when
    include_subagents is on."""
    from twicc.core.models import Session

    if not ctx.options.get("include_subagents", True):
        raise Http404("Not found")
    sub = await sync_to_async(lambda: Session.objects.filter(id=sid).first())()
    if sub is None or not await is_descendant_of(sub, ctx.session):
        raise Http404("Not found")
    return sub


# ── Page ────────────────────────────────────────────────────────────────────

async def share_session_page(request, ctx):
    from twicc.core.serializers import serialize_share_public_meta

    meta = await sync_to_async(serialize_share_public_meta)(ctx.share)
    # View tracking (Phase 8) — accumulate a page view.
    from twicc.share.view_tracking import note_view
    note_view(ctx.share, request)
    return share_page_response(token_path=f"/share/{ctx.share.token}", meta=meta)


# ── API ─────────────────────────────────────────────────────────────────────

async def share_session_meta(request, token):
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    from twicc.core.serializers import serialize_share_public_meta
    return _json(await sync_to_async(serialize_share_public_meta)(ctx.share))


def _ceiling_kwargs(ctx, session):
    opts = ctx.options
    max_line = opts.get("frozen_at_line") if opts.get("mode") == "snapshot" else None
    # Subagents are never frozen-line clamped (frozen line applies to the root).
    if session.id != ctx.session.id:
        max_line = None
    return {"max_display_mode": opts.get("max_display_mode", "normal"), "max_line": max_line}


async def _items_metadata(request, token, subagent_id=None):
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    session = ctx.session if subagent_id is None else await _resolve_shared_subagent(ctx, subagent_id)
    qs = filtered_items_qs(session, **_ceiling_kwargs(ctx, session)).defer("content").order_by("line_num")
    items = await sync_to_async(list)(qs)
    return _json([serialize_session_item_metadata(i) for i in items], safe=False)


async def _items(request, token, subagent_id=None):
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    ranges = _parse_ranges(request)
    if ranges is None:
        return _json({"error": "At least one 'range' query parameter is required"}, safe=True)
    session = ctx.session if subagent_id is None else await _resolve_shared_subagent(ctx, subagent_id)
    qs = filtered_items_qs(session, extra=ranges, **_ceiling_kwargs(ctx, session)).order_by("line_num")
    items = await sync_to_async(list)(qs)
    return _json([serialize_session_item(i) for i in items], safe=False)


async def _tool_results(request, token, line_num, tool_id, subagent_id=None):
    from twicc.core.models import SessionItem, ToolResultLink
    from twicc.providers.helpers import get_provider_helpers

    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    session = ctx.session if subagent_id is None else await _resolve_shared_subagent(ctx, subagent_id)
    link_lines = await sync_to_async(list)(
        ToolResultLink.objects.filter(session=session, tool_use_line_num=line_num, tool_use_id=tool_id)
        .values_list("tool_result_line_num", flat=True)
    )
    if not link_lines:
        return _json({"results": []})
    kw = _ceiling_kwargs(ctx, session)
    # Tool-result rows are always at/under the ceiling for a visible tool_use, but
    # clamp defensively so a frozen snapshot never leaks a post-freeze result.
    qs = SessionItem.objects.filter(session=session, line_num__in=link_lines)
    if kw["max_line"] is not None:
        qs = qs.filter(line_num__lte=kw["max_line"])
    items = await sync_to_async(list)(qs.order_by("line_num"))
    results = get_provider_helpers(session.provider).get_tool_results(items, tool_id)
    return _json({"results": results})


async def share_session_subagents(request, token):
    from twicc.core.models import AgentLink, Session

    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    if not ctx.options.get("include_subagents", True):
        return _json([], safe=False)
    links = await sync_to_async(list)(
        AgentLink.objects.filter(session=ctx.session).order_by("id")
    )
    slugs = dict(await sync_to_async(list)(
        Session.objects.filter(id__in=[link.agent_id for link in links]).values_list("id", "slug")
    ))
    return _json([
        {
            "agent_id": link.agent_id,
            "agent_slug": slugs.get(link.agent_id),
            "tool_use_id": link.tool_use_id,
            "tool_use_line_num": link.tool_use_line_num,
            "is_background": link.is_background,
            "started_at": link.started_at.isoformat() if link.started_at else None,
        }
        for link in links
    ], safe=False)


# Public entry points (URL-mapped). Method-guarded, delegating to the helpers above.

async def api_meta(request, token):
    return await share_session_meta(request, token)


async def api_items_metadata(request, token):
    return await _items_metadata(request, token)


async def api_items(request, token):
    return await _items(request, token)


async def api_tool_results(request, token, line_num, tool_id):
    return await _tool_results(request, token, line_num, tool_id)


async def api_subagents(request, token):
    return await share_session_subagents(request, token)


async def api_subagent_items_metadata(request, token, subagent_id):
    return await _items_metadata(request, token, subagent_id)


async def api_subagent_items(request, token, subagent_id):
    return await _items(request, token, subagent_id)


async def api_subagent_tool_results(request, token, subagent_id, line_num, tool_id):
    return await _tool_results(request, token, line_num, tool_id, subagent_id)


# ── Inline transcript media (design §6.2) ───────────────────────────────────

async def share_session_media(request, token, filename):
    """Serve an inline artifact image referenced by the transcript, confined to the
    shared session's artifacts dir + the extension allowlist (copied contract from
    ``views.session_artifact``, keyed by token)."""
    import stat

    from django.http import FileResponse
    from twicc.paths import get_session_artifacts_dir
    from twicc.views import _classify_artifact_filename

    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    content_type = _classify_artifact_filename(filename)
    if content_type is None:
        raise Http404("Artifact not found")
    artifacts_dir = get_session_artifacts_dir(ctx.session.id)
    target = artifacts_dir / filename

    def _open():
        try:
            root = artifacts_dir.resolve(strict=True)
            resolved = target.resolve(strict=True)
        except (FileNotFoundError, RuntimeError, OSError):
            return None
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        try:
            if not stat.S_ISREG(resolved.stat().st_mode):
                return None
            return resolved.open("rb")
        except OSError:
            return None

    fp = await sync_to_async(_open)()
    if fp is None:
        raise Http404("Artifact not found")
    return apply_share_headers(FileResponse(fp, content_type=content_type, as_attachment=False))
```

### 2.8 `NEW` `src/twicc/share/artifact_views.py`

```python
"""Artifact-share HTTP views. Always serves from the snapshot dir (D7); HTML gets
the trusted shell + CSP-wrapped inner doc; the broker proxy enforces the owner's
allowlist server-side (D6)."""

from __future__ import annotations

import os

from asgiref.sync import sync_to_async
from django.http import Http404, HttpResponseNotAllowed, JsonResponse

from twicc.artifacts.broker_html import ARTIFACT_INNER_DOC_PATH
from twicc.share.headers import apply_share_headers
from twicc.share.html import (
    share_artifact_doc_response,
    share_artifact_shell_response,
    share_page_response,
)
from twicc.share.resolver import SharePasswordRequired, password_required_response, resolve_or_404


async def _ctx(request, token):
    try:
        ctx = await resolve_or_404(request, token)
    except SharePasswordRequired:
        return None, password_required_response(request, token)
    if ctx.share.kind != "artifact":
        raise Http404("This link is not available.")
    return ctx, None


def _snapshot_rel(ctx) -> str:
    """The bookmarked file's path RELATIVE to its own directory — i.e. its basename,
    since the snapshot copies the parent dir as the new root."""
    return os.path.basename(ctx.bookmark.relative_path)


async def share_artifact_page(request, ctx):
    """Root: HTML → shell page; markdown/mermaid → doc view (share-session bundle);
    other → the file directly. Counts a view."""
    from twicc.share.view_tracking import note_view
    from twicc.share.session_views import _json  # reuse header helper indirectly
    from twicc.views import _guess_raw_content_type, _serve_artifact_file
    from twicc.core.services.share_mutation import confined_snapshot_path

    note_view(ctx.share, request)
    rel = _snapshot_rel(ctx)
    abs_root = confined_snapshot_path(ctx.share.id, rel)
    if abs_root is None:
        raise Http404("File not found")
    ext = os.path.splitext(rel)[1].lower()
    ctype = _guess_raw_content_type(abs_root)
    # Public display title (page header + recent-shares list): owner override, else
    # the real bookmark name, else the file's basename.
    display_title = (ctx.options.get("display_title") or "").strip()
    art_title = display_title or (ctx.bookmark.name if ctx.bookmark else None) or rel

    if ctype == "text/html":
        token_path = f"/share/{ctx.share.token}"
        return share_artifact_shell_response(
            token_path=token_path,
            inner_doc_url=f"{token_path}/{ARTIFACT_INNER_DOC_PATH}",
            snapshot_at=ctx.options.get("snapshot_at"),
            title=art_title,
        )
    if ext in (".md", ".markdown", ".mmd"):
        token_path = f"/share/{ctx.share.token}"
        meta = {"kind": "artifact", "docExt": ext.lstrip("."), "title": art_title,
                "docUrl": f"{token_path}/__twicc_raw__/{rel}", "snapshot_at": ctx.options.get("snapshot_at")}
        return share_page_response(token_path=token_path, meta=meta, mode="doc")
    response = await sync_to_async(_serve_artifact_file)(abs_root, as_document=False)
    if response is None:
        raise Http404("File not found")
    return apply_share_headers(response)


async def api_meta(request, token):
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    return apply_share_headers(JsonResponse({"snapshot_at": ctx.options.get("snapshot_at")}))


async def share_artifact_doc(request, token):
    """``__twicc_doc__``: the artifact HTML wrapped (shim + CSP)."""
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    from twicc.core.services.share_mutation import confined_snapshot_path

    abs_path = confined_snapshot_path(ctx.share.id, _snapshot_rel(ctx))
    if abs_path is None or not os.path.isfile(abs_path):
        raise Http404("File not found")
    html = await sync_to_async(lambda: open(abs_path, "rb").read())()
    return share_artifact_doc_response(html)


async def share_artifact_asset(request, token, asset):
    """A sibling asset (or the raw doc for markdown), confined to the snapshot dir."""
    from twicc.core.services.share_mutation import confined_snapshot_path
    from twicc.views import _raw_file_response

    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    # ``__twicc_raw__/<path>`` is the doc-view fetch of the raw markdown; strip it.
    if asset.startswith("__twicc_raw__/"):
        asset = asset[len("__twicc_raw__/"):]
    abs_path = confined_snapshot_path(ctx.share.id, asset)
    if abs_path is None:
        raise Http404("File not found")
    response = await sync_to_async(_raw_file_response)(abs_path)
    if response is None:
        raise Http404("File not found")
    return apply_share_headers(response)


async def share_artifact_proxy(request, token):
    """Broker proxy for a shared artifact (D6): server-enforces the owner's
    allowlist (unlike the owner proxy). Metadata block + IP pinning inherited."""
    from twicc.artifacts import proxy as artifact_proxy_mod

    ctx, resp = await _ctx(request, token)
    if resp:
        return resp
    allowed = set((ctx.bookmark.allowed_hosts or {}).keys())
    return await artifact_proxy_mod.artifact_proxy(request, enforced_allowlist=allowed)
```

### 2.9 `EDIT` `src/twicc/artifacts/proxy.py` — server-enforced allowlist for share proxy

Change the view signature and add the enforcement in `fetch` mode (owner path keeps `enforced_allowlist=None` → no re-check, per broker design §6.4).

Anchor:
```python
async def artifact_proxy(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
```
Replace with:
```python
async def artifact_proxy(request, *, enforced_allowlist: set[str] | None = None):
    # ``enforced_allowlist`` is set ONLY by the share proxy (design §9.3/D6): the
    # normalized scheme://host:port must be in it or the fetch is refused 403.
    # The owner path passes None and deliberately does NOT re-check (broker §6.4).
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
```
Then, inside the `if mode == "fetch":` branch, right after the metadata block check
```python
        if kind == "metadata":
            return JsonResponse({"error": "blocked", "reason": "metadata"})
```
insert:
```python
        if enforced_allowlist is not None:
            key = normalize_host_key(url)
            if key not in enforced_allowlist:
                return JsonResponse({"error": "blocked", "reason": "not_allowed"})
```
And in the `if mode == "preflight":` branch, when `enforced_allowlist is not None`, refuse preflight (share host does no preflight — it fetches directly). After the `mode == "preflight":` line's resolve, add at the very top of that branch:
```python
    if mode == "preflight":
        if enforced_allowlist is not None:
            return JsonResponse({"error": "bad_request", "reason": "preflight_disabled"}, status=400)
```

### 2.10 `NEW` `src/twicc/share/router.py`

```python
"""Dispatch ``/share/<token>/`` (and its bottom ``<path:asset>`` catch) by the
share's kind, so one flat URL space serves both session and artifact shares."""

from __future__ import annotations

from django.http import Http404, HttpResponseNotAllowed

from twicc.share.resolver import SharePasswordRequired, password_required_response, resolve_or_404


async def share_recent(request):
    """``/share/`` (no token): the share host homepage — a client-side list of the
    shares this browser has opened (localStorage, design §12). No server data."""
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    from twicc.share.html import share_recent_response
    return share_recent_response()


async def share_root(request, token):
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    try:
        ctx = await resolve_or_404(request, token)
    except SharePasswordRequired:
        return password_required_response(request, token)
    if ctx.share.kind == "session":
        from twicc.share.session_views import share_session_page
        return await share_session_page(request, ctx)
    from twicc.share.artifact_views import share_artifact_page
    return await share_artifact_page(request, ctx)


async def share_asset_or_doc(request, token, asset):
    """Bottom catch for artifact shares: ``__twicc_doc__`` → wrapped inner doc,
    anything else → a sibling asset. Session shares have no such sub-paths → 404."""
    from twicc.artifacts.broker_html import ARTIFACT_INNER_DOC_PATH
    from twicc.share.artifact_views import share_artifact_asset, share_artifact_doc

    if asset == ARTIFACT_INNER_DOC_PATH:
        return await share_artifact_doc(request, token)
    return await share_artifact_asset(request, token, asset)
```

### 2.11 `NEW` `src/twicc/share/password_views.py`

```python
"""Standalone per-link password page (design §7.1). Mirrors ``auth.views.artifact_auth``
and shares its IP rate limiter."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render

from twicc.auth.hashers import verify_password
from twicc.auth.views import _check_rate_limit, _get_client_ip, _login_attempts, _record_failed_attempt
from twicc.core.services.share_tokens import aresolve_share, password_fingerprint
from twicc.share.resolver import SHARE_GRANTS_SESSION_KEY


def _safe_redirect(token: str, value: str) -> str:
    """Confine the post-login redirect to this share's own path."""
    prefix = f"/share/{token}/"
    if value and value.startswith(prefix) and not value.startswith("//"):
        return value
    return prefix


async def share_auth(request, token):
    if request.method not in ("GET", "POST"):
        return HttpResponse(status=405)

    share = await aresolve_share(token)
    if share is None or not share.is_active() or not share.password_hash:
        # No password (or gone): send them to the share root (uniform behaviour).
        return HttpResponseRedirect(f"/share/{token}/")

    if request.method == "GET":
        target = _safe_redirect(token, request.GET.get("redirect", ""))
        return render(request, "share_auth.html", {"redirect": target, "error": False})

    body = parse_qs(request.body.decode("utf-8", "replace"))
    target = _safe_redirect(token, (body.get("redirect") or [""])[0])
    ip = _get_client_ip(request)
    wait = _check_rate_limit(ip)
    if wait is not None:
        return render(request, "share_auth.html", {"redirect": target, "error": True}, status=429)
    password = (body.get("password") or [""])[0]
    if await asyncio.to_thread(verify_password, password, share.password_hash):
        grants = await request.session.aget(SHARE_GRANTS_SESSION_KEY) or {}
        grants[share.id] = password_fingerprint(share.password_hash)
        await request.session.aset(SHARE_GRANTS_SESSION_KEY, grants)
        _login_attempts.pop(ip, None)
        return HttpResponseRedirect(target)
    _record_failed_attempt(ip)
    return render(request, "share_auth.html", {"redirect": target, "error": True}, status=401)
```

> `_check_rate_limit`, `_record_failed_attempt`, `_login_attempts` are module-private in `auth/views.py` today but importable. Keep the import as-is (no refactor needed — they are plain module functions).

> Grant persistence caveat: the grant lives in the Django session cookie, set and read on the same `/share/` origin (never cross-site), so `SameSite` doesn't gate it. But in DEBUG the cookie is `Secure`, so it isn't set over a plain-HTTP origin — test password-protected shares over an HTTPS tunnel or `localhost` (a secure context), not a plain-http LAN IP. Production (`DEBUG=False`) is `Lax`/non-Secure and unaffected.

### 2.12 `NEW` `src/twicc/share/templates/share_auth.html`

Clone `artifacts/templates/artifact_auth.html`, changing only the `action` and copy:
```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>TwiCC — Password required</title>
<style>
:root{--bg:#18181b;--card:#27272a;--border:#3f3f46;--text:#f4f4f5;--muted:#a1a1aa;--brand:#06b6d4;--brand-text:#08171c;--input-bg:#18181b;--danger-bg:#7f1d1d;--danger-text:#fecaca;color-scheme:dark}
@media (prefers-color-scheme: light){:root{--bg:#f4f4f5;--card:#ffffff;--border:#e4e4e7;--text:#18181b;--muted:#71717a;--brand:#0891b2;--brand-text:#ffffff;--input-bg:#ffffff;--danger-bg:#fee2e2;--danger-text:#991b1b;color-scheme:light}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}
.card{width:100%;max-width:360px;margin:1rem;padding:2rem;background:var(--card);border:1px solid var(--border);border-radius:12px}
.head{text-align:center;margin-bottom:1.5rem}
.title{margin:0;font-size:1.4rem;font-weight:700;letter-spacing:.05em}
.sub{margin:.4rem 0 0;font-size:.85rem;color:var(--muted)}
label{display:block;font-size:.85rem;font-weight:600;margin-bottom:.4rem}
input[type=password]{width:100%;padding:.6rem .7rem;font-size:1rem;border-radius:8px;border:1px solid var(--border);background:var(--input-bg);color:var(--text)}
input[type=password]:focus{outline:2px solid var(--brand);outline-offset:1px}
.err{margin:.9rem 0 0;padding:.55rem .7rem;border-radius:8px;font-size:.85rem;background:var(--danger-bg);color:var(--danger-text)}
button{width:100%;margin-top:1.1rem;padding:.65rem;font-size:.95rem;font-weight:600;border:none;border-radius:8px;background:var(--brand);color:var(--brand-text);cursor:pointer}
button:hover{filter:brightness(1.08)}
</style>
</head>
<body>
<form class="card" method="post">
  <div class="head">
    <h1 class="title">TwiCC</h1>
    <p class="sub">This shared link is password-protected</p>
  </div>
  <input type="hidden" name="redirect" value="{{ redirect }}">
  <label for="pw">Password</label>
  <input id="pw" type="password" name="password" placeholder="Enter password" autofocus autocomplete="current-password">
  {% if error %}<p class="err">Incorrect password. Try again.</p>{% endif %}
  <button type="submit">Open</button>
</form>
</body>
</html>
```
The form POSTs to its own URL (`/share/<token>/auth`), so no explicit `action` is needed. Ensure the `templates/` dir is on the `TEMPLATES['DIRS']` path — the `artifacts/templates` entry already covers per-app template dirs via `APP_DIRS`? It does **not** (twicc uses an explicit `DIRS` entry pointing at `artifacts/templates`). Add `share/templates` to that list — see 2.13.

### 2.13 `EDIT` `src/twicc/settings.py` — register the share templates dir

Find the `TEMPLATES` setting's `DIRS` (it lists `TEMPLATES_DIR` from `twicc.artifacts`). Add the share templates dir next to it:
```python
from twicc.artifacts import TEMPLATES_DIR as ARTIFACT_TEMPLATES_DIR
from pathlib import Path as _Path
SHARE_TEMPLATES_DIR = _Path(__file__).resolve().parent / "share" / "templates"
# ... in TEMPLATES[0]["DIRS"]:
"DIRS": [ARTIFACT_TEMPLATES_DIR, SHARE_TEMPLATES_DIR],
```
Match the existing style in `settings.py` (read the current `TEMPLATES` block first; keep any other DIRS entries).

### 2.14 `NEW` `src/twicc/share/views_assets.py`

```python
"""Serve the built share-session bundle from ``static/share-session/`` under the
public ``/_twicc/share/`` prefix. Clone of ``views.artifact_shell_asset``."""

from __future__ import annotations

import asyncio
import os

from django.conf import settings
from django.http import Http404, HttpResponseNotAllowed

from twicc.views import _raw_file_response


async def share_asset(request, asset):
    if request.method not in ("GET", "HEAD"):
        return HttpResponseNotAllowed(["GET", "HEAD"])
    base = (settings.PACKAGE_DIR / "static" / "share-session").resolve()
    target = (base / asset).resolve()
    if not str(target).startswith(str(base) + os.sep):
        raise Http404("Not found")
    response = await asyncio.to_thread(_raw_file_response, str(target))
    if response is None:
        raise Http404("Share bundle not built")
    return response
```

### 2.15 `EDIT` `src/twicc/urls.py` — mount share routes

Add the imports at the top:
```python
from .share import artifact_views as share_artifact_views
from .share import password_views as share_password_views
from .share import router as share_router
from .share import session_views as share_session_views
from .share import views_assets as share_views_assets
```
Insert this block **before** the SPA catch-all (after the `artifacts/…` block, before the `rpc/` block is fine):
```python
    # Public share surface (design §6). Order: password page, kind-specific API,
    # then root + bottom catch (LAST so it can't shadow the API routes).
    path("share/<str:token>/auth", share_password_views.share_auth),
    path("share/<str:token>/api/meta/", share_session_views.api_meta),
    path("share/<str:token>/api/items/metadata/", share_session_views.api_items_metadata),
    path("share/<str:token>/api/items/", share_session_views.api_items),
    path("share/<str:token>/api/items/<int:line_num>/tool-results/<str:tool_id>/", share_session_views.api_tool_results),
    path("share/<str:token>/api/subagents/", share_session_views.api_subagents),
    path("share/<str:token>/api/subagent/<str:subagent_id>/items/metadata/", share_session_views.api_subagent_items_metadata),
    path("share/<str:token>/api/subagent/<str:subagent_id>/items/", share_session_views.api_subagent_items),
    path("share/<str:token>/api/subagent/<str:subagent_id>/items/<int:line_num>/tool-results/<str:tool_id>/", share_session_views.api_subagent_tool_results),
    path("share/<str:token>/media/<str:filename>", share_session_views.share_session_media),
    # Artifact-share meta + proxy live under /api/ too (shape-uniform with sessions).
    path("share/<str:token>/api/artifact-meta/", share_artifact_views.api_meta),
    path("share/<str:token>/api/proxy/", share_artifact_views.share_artifact_proxy),
    path("share/", share_router.share_recent),
    path("share/<str:token>/", share_router.share_root),
    path("share/<str:token>/<path:asset>", share_router.share_asset_or_doc),
    path("_twicc/share/<str:asset>", share_views_assets.share_asset),
```
Finally, exclude `share/` and `_twicc/` from the SPA catch-all so unknown share URLs 404 instead of serving `index.html`. Anchor:
```python
    re_path(r"^(?!api/|rpc/|static/|ws/|artifacts/).*$", views.spa_index),
```
Replace with:
```python
    re_path(r"^(?!api/|rpc/|static/|ws/|artifacts/|share/|_twicc/).*$", views.spa_index),
```

### 2.16 `EDIT` `src/twicc/drop_requests_watcher.py` — `share:*` kinds

Add to `_KIND_HANDLERS` (after the `artifact_bookmark:*` entries):
```python
    "share:create": (
        "twicc.core.services.share_mutation",
        "create_share_from_payload",
        "created",
    ),
    "share:update": (
        "twicc.core.services.share_mutation",
        "update_share_from_payload",
        "updated",
    ),
    "share:revoke": (
        "twicc.core.services.share_mutation",
        "revoke_share_from_payload",
        "updated",
    ),
    "share:unrevoke": (
        "twicc.core.services.share_mutation",
        "unrevoke_share_from_payload",
        "updated",
    ),
    "share:delete": (
        "twicc.core.services.share_mutation",
        "delete_share_from_payload",
        "deleted",
    ),
    "share:propagate": (
        "twicc.core.services.share_mutation",
        "propagate_share_from_payload",
        "updated",
    ),
```
And add `"share_id"` to `_RESULT_ID_FIELDS` so the status file echoes it:
```python
_RESULT_ID_FIELDS: tuple[str, ...] = (
    "session_id",
    "provider",
    "project_id",
    "workspace_id",
    "bookmark_id",
    "share_id",
)
```

### 2.17 Curl test matrix (before any frontend)

```
# create a share via the shell (Phase 9 CLI), or ad-hoc in a django shell, then:
curl -sS http://localhost:3500/share/<token>/api/meta/           # 200 public meta
curl -sS http://localhost:3500/share/<bad>/api/meta/             # 404 uniform
curl -sS http://localhost:3500/share/<revoked>/api/meta/         # 404 uniform (no oracle)
curl -sS "http://localhost:3500/share/<token>/api/items/?range=1:50"  # ceiling+frozen filtered
curl -sS http://localhost:3500/share/<token>/api/items/          # 200 {"error": "...range required"}
curl -sSI http://localhost:3500/share/<token>/api/meta/          # X-Robots-Tag, Referrer-Policy, Cache-Control: no-store
# password share: /api/meta/ → 401 share_password_required; /auth GET → form; POST ok → grant
# remote reachability with NO instance password (O1): same 200s from a non-loopback client
```

---

## Phase 3 — Share-session frontend bundle

### 3.1 `NEW` `frontend/vite.config.share.js`

```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

// Standalone read-only transcript viewer bundle (design §8). Reuses the SPA's
// transcript component tree verbatim; the store imports are aliased to shims so
// none of the main SPA (auth store, router, WebSocket) is pulled in.
const r = (p) => fileURLToPath(new URL(p, import.meta.url))

export default defineConfig({
    plugins: [
        vue({ template: { compilerOptions: { isCustomElement: (tag) => tag.startsWith('wa-') } } }),
    ],
    publicDir: false,
    base: '/_twicc/share/',
    define: { 'process.env.NODE_ENV': JSON.stringify('production') },
    resolve: {
        alias: [
            { find: /.*\/stores\/data(\.js)?$/, replacement: r('src/share-session/shims/dataStoreShim.js') },
            { find: /.*\/stores\/settings(\.js)?$/, replacement: r('src/share-session/shims/settingsStoreShim.js') },
            { find: /.*\/stores\/codeComments(\.js)?$/, replacement: r('src/share-session/shims/codeCommentsShim.js') },
            { find: /.*\/composables\/useWebSocket(\.js)?$/, replacement: r('src/share-session/shims/noWebSocket.js') },
        ],
    },
    build: {
        outDir: '../src/twicc/static/share-session',
        emptyOutDir: true,
        cssCodeSplit: false,
        lib: { entry: 'src/share-session/main.js', formats: ['es'], fileName: () => 'share-session.js' },
        rollupOptions: {
            output: {
                inlineDynamicImports: true,
                assetFileNames: (info) => {
                    const name = info.name || (info.names && info.names[0]) || ''
                    return name.endsWith('.css') ? 'share-session.css' : 'assets/[name]-[hash][extname]'
                },
            },
        },
    },
})
```

### 3.2 `EDIT` `frontend/package.json` — chain the build

Anchor (the build already chains the SPA + shim + artifact-shell + browser-companion bundles):
```json
    "build": "vite build && vite build --config vite.config.shim.js && vite build --config vite.config.shell.js && vite build --config vite.config.companion.js",
```
Replace with (append the share bundle last):
```json
    "build": "vite build && vite build --config vite.config.shim.js && vite build --config vite.config.shell.js && vite build --config vite.config.companion.js && vite build --config vite.config.share.js",
```

### 3.3 `NEW` `frontend/src/styles/transcript-tokens.css` (extracted App.vue trio)

```css
/* Root transcript CSS custom properties shared by the SPA (App.vue) and the
   share bundle so they can't drift (design §8.8). Only the App.vue-level trio;
   --card-spacing / --max-card-width ship with SessionItem.vue's own CSS. */
:root {
    --base-user-assistant-card-color: var(--wa-color-gray-95);
    --user-card-base-color: oklch(from var(--base-user-assistant-card-color) calc(l + 0.015) c h);
    --assistant-card-base-color: oklch(from var(--base-user-assistant-card-color) calc(l + 0.03) c h);
    --main-shadow-size: var(--wa-shadow-offset-y-s);
}
.wa-dark {
    --base-user-assistant-card-color: var(--wa-color-surface-raised);
}
```

`EDIT` `frontend/src/App.vue`: replace the four inlined declarations (`--base-user-assistant-card-color`, `--user-card-base-color`, `--assistant-card-base-color` at :789-791 and `--main-shadow-size` at :852, plus the `.wa-dark` override at :870) by an `@import '../styles/transcript-tokens.css'` at the top of App.vue's `<style>` — or, simpler and HMR-safe, import it in `main.js` after the WA CSS and delete those five lines from App.vue. Choose the `main.js` import (keeps App.vue's `<style>` scoped-free of the shared file). Verify no other rule in App.vue depends on the deleted lines' position.

### 3.4 `NEW` `frontend/src/share-session/main.js`

```js
// Entry for the read-only shared-session viewer (design §8). Mounts a minimal
// Pinia app over the reused transcript tree; the store imports are aliased to
// shims (vite.config.share.js), so no SPA/auth/router/WS code is pulled in.
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { initShareTheme } from './theme'

initShareTheme()

// Web Awesome: tokens + the elements the transcript tree actually renders.
import '@awesome.me/webawesome/dist/styles/webawesome.css'
import '@awesome.me/webawesome/dist/styles/themes/default.css'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/button-group/button-group.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'
import '@awesome.me/webawesome/dist/components/tag/tag.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
import '@awesome.me/webawesome/dist/components/spinner/spinner.js'
import '@awesome.me/webawesome/dist/components/switch/switch.js'
import '@awesome.me/webawesome/dist/components/select/select.js'
import '@awesome.me/webawesome/dist/components/option/option.js'
import '@awesome.me/webawesome/dist/components/details/details.js'
import '@awesome.me/webawesome/dist/components/dialog/dialog.js'
import '@awesome.me/webawesome/dist/components/tooltip/tooltip.js'

import '../styles/transcript-tokens.css'
import ShareSessionApp from './ShareSessionApp.vue'
import ShareDocApp from './ShareDocApp.vue'
import ShareRecentApp from './ShareRecentApp.vue'   // 3.20
import { recordShareView } from '../share-recent/recordView'  // 3.19

const el = document.getElementById('twicc-share-data')
const data = el ? JSON.parse(el.textContent) : {}

let app
if (data.mode === 'recent') {
    // Share host homepage (/share/, no token): the localStorage-backed recent list.
    app = createApp(ShareRecentApp)
} else {
    // A real share page was opened → record it in this browser's recent list.
    recordShareView({
        tokenPath: data.tokenPath,
        kind: data.mode === 'session' ? 'session' : 'artifact',
        title: (data.meta && data.meta.title) || '',
    })
    app = createApp(data.mode === 'doc' ? ShareDocApp : ShareSessionApp, {
        tokenPath: data.tokenPath,
        meta: data.meta || {},
    })
}
app.use(createPinia())
app.mount('#app')
document.body.classList.remove('loading')
```

### 3.5 `NEW` `frontend/src/share-session/theme.js`

```js
// Minimal theme init for the share bundle: apply the default WA theme/brand and a
// viewer color scheme (own localStorage key, defaulting to the OS preference).
// No dependency on the SPA settings store.
const KEY = 'twicc-share-color-scheme'

export function getShareColorScheme() {
    try { return localStorage.getItem(KEY) || 'system' } catch { return 'system' }
}

export function applyShareColorScheme(mode) {
    const isDark = mode === 'dark' || (mode !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.classList.toggle('wa-dark', isDark)
    document.documentElement.dataset.colorScheme = isDark ? 'dark' : 'light'
    try { localStorage.setItem(KEY, mode) } catch { /* ignore */ }
}

export function initShareTheme() {
    document.documentElement.classList.add('wa-theme-default', 'wa-palette-default', 'wa-brand-cyan')
    document.documentElement.dataset.theme = 'default'
    applyShareColorScheme(getShareColorScheme())
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        if (getShareColorScheme() === 'system') applyShareColorScheme('system')
    })
}
```

### 3.6 `NEW` `frontend/src/share-session/shims/shareApi.js`

```js
// Fetch layer for the share bundle — all requests stay under the share token path.
export function makeShareApi(tokenPath) {
    const base = tokenPath.replace(/\/+$/, '')
    async function jget(url) {
        const res = await fetch(url, { credentials: 'same-origin' })
        if (!res.ok) throw new Error(`share fetch ${res.status}`)
        return res.json()
    }
    return {
        base,
        fetchMeta: () => jget(`${base}/api/meta/`),
        fetchItemsMetadata: (subagentId = null) =>
            jget(subagentId ? `${base}/api/subagent/${subagentId}/items/metadata/` : `${base}/api/items/metadata/`),
        fetchItems: (rangesQS, subagentId = null) =>
            jget(subagentId ? `${base}/api/subagent/${subagentId}/items/?${rangesQS}` : `${base}/api/items/?${rangesQS}`),
        fetchToolResults: (lineNum, toolId, subagentId = null) =>
            jget(subagentId
                ? `${base}/api/subagent/${subagentId}/items/${lineNum}/tool-results/${toolId}/`
                : `${base}/api/items/${lineNum}/tool-results/${toolId}/`),
        fetchSubagents: () => jget(`${base}/api/subagents/`),
        mediaUrl: (filename) => `${base}/media/${filename}`,
    }
}

// Module-scoped singleton so the shim stores (which can't take constructor args)
// reach the same API instance the app configured at boot.
let _api = null
export function setShareApi(api) { _api = api }
export function shareApi() {
    if (!_api) throw new Error('share API not configured')
    return _api
}
```

### 3.7 `NEW` `frontend/src/share-session/shims/dataStoreShim.js`

A real Pinia store exposing exactly the read surface the transcript tree touches (enumerated by grepping the reused components: `getSession`, `getProject`, `getSessionItem`, `getSessionItems`, `getSessionVisualItems`, `recomputeVisualItems`, `getExpandedGroups`/`toggleExpandedGroup`, `getInternalExpandedGroups`/`toggleInternalExpandedGroup`, `isDetailOpen`/`setDetailOpen`, `isBlockDetailed`/`toggleBlockDetailedMode`, `getDisplayMode`, `getAgentLink`, `getAgentToolUseLineNum`, `getWorkflowLink`, `getToolState`, `getProcessState`, `getPendingRequests`, `isItemLive`, `isStartupInProgress`, `getProjectIndicatorScopeIds`, `loadSessionItemsRanges`, `loadSessionMetadata`, `initSessionItemsFromMetadata`, `updateSessionItemsContent`, `addSessionItems`, plus the failed-send / api-error surface as no-ops).

```js
// Read-only mirror of the SPA data store for the share bundle. Only the surface
// the reused transcript components actually touch is implemented; anything else
// throws in dev so drift is caught, and no-ops the write surface (failed sends,
// api-error recovery) that read-only mode never exercises.
import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { computeVisualItems, insertDaySeparators, visualItemEqual } from '../../../utils/visualItems'
import { getParsedContent, setParsedContent, clearParsedContent } from '../../../utils/parsedContent'
import { shareApi } from './shareApi'
import { useSettingsStore } from '../../../stores/settings' // aliased to settingsStoreShim

function rangesToQS(ranges) {
    const params = new URLSearchParams()
    for (const range of ranges) {
        if (typeof range === 'number' || typeof range === 'string') {
            params.append('range', String(range))
        } else if (Array.isArray(range)) {
            const [lo, hi] = range
            params.append('range', `${lo ?? ''}:${hi ?? ''}`)
        }
    }
    return params.toString()
}

export const useDataStore = defineStore('shareData', {
    state: () => ({
        // The single shared session (plus subagents keyed by id).
        sessions: {},            // id -> session-ish meta
        sessionItems: {},        // id -> [{ line_num, content, display_level, group_head, group_tail, kind, timestamp }]
        visualItems: {},         // id -> stabilized visual items
        expandedGroups: {},      // id -> [groupHeadLineNum]
        internalExpandedGroups: {},
        detailedBlocks: {},      // id -> [userMessageLineNum]
        openDetails: {},         // id -> { key: bool }
        agentLinks: {},          // id -> { toolId: { agentId, isBackground, toolUseLineNum, slug } }
        toolStates: {},          // id -> { toolId: {...} }
        _cache: {},              // id -> Map for visual-item stabilization
    }),
    getters: {
        getSession: (s) => (id) => s.sessions[id] || null,
        getProject: () => () => null,
        getSessionItems: (s) => (id) => s.sessionItems[id] || [],
        getSessionItem: (s) => (id, lineNum) => {
            const items = s.sessionItems[id]
            if (!items || lineNum < 1) return null
            return items[lineNum - 1] || null
        },
        getSessionVisualItems: (s) => (id) => s.visualItems[id] || [],
        getExpandedGroups: (s) => (id) => s.expandedGroups[id] || [],
        getInternalExpandedGroups: (s) => (id, lineNum) => (s.internalExpandedGroups[id]?.[lineNum]) || [],
        isBlockDetailed: (s) => (id, u) => (s.detailedBlocks[id] || []).includes(u),
        isDetailOpen: (s) => (id, key) => !!s.openDetails[id]?.[key],
        getAgentLink: (s) => (id, toolId) => s.agentLinks[id]?.[toolId],
        getAgentToolUseLineNum: (s) => (parentId, subId) => {
            const links = s.agentLinks[parentId]
            if (!links) return null
            for (const l of Object.values(links)) if (l.agentId === subId) return l.toolUseLineNum ?? null
            return null
        },
        getWorkflowLink: () => () => undefined,          // no "View Workflow" in shares
        getToolState: (s) => (id, toolId) => s.toolStates[id]?.[toolId] || null,
        getProcessState: () => () => null,               // never live → no spinners/stop
        getPendingRequests: () => () => [],
        isItemLive: () => () => false,
        isStartupInProgress: () => () => false,
        getProjectIndicatorScopeIds: () => () => [],
        // failed-send / api-error read surface (unused in read-only)
        getFailedSend: () => () => null,
        apiErrorRecovery: () => () => null,
    },
    actions: {
        // ── Loading ──────────────────────────────────────────────────────
        async loadSessionMetadata(_projectId, sessionId, parentSessionId = null) {
            try { return await shareApi().fetchItemsMetadata(parentSessionId ? sessionId : null) }
            catch { return null }
        },
        initSessionItemsFromMetadata(sessionId, metadata) {
            this.sessionItems[sessionId] = metadata.map((m) => ({
                line_num: m.line_num, display_level: m.display_level,
                group_head: m.group_head, group_tail: m.group_tail,
                kind: m.kind, timestamp: m.timestamp ?? null, content: null,
            }))
            this.recomputeVisualItems(sessionId)
        },
        updateSessionItemsContent(sessionId, items) {
            const arr = this.sessionItems[sessionId]
            if (!arr) return
            for (const it of items) {
                const i = it.line_num - 1
                if (!arr[i]) continue
                arr[i].content = it.content
                clearParsedContent(arr[i])
                if (it.display_level != null) arr[i].display_level = it.display_level
                if (it.group_head != null) arr[i].group_head = it.group_head
                if (it.group_tail != null) arr[i].group_tail = it.group_tail
                if (it.kind !== undefined) arr[i].kind = it.kind
                if (it.timestamp !== undefined) arr[i].timestamp = it.timestamp
            }
            this.recomputeVisualItems(sessionId)
        },
        addSessionItems(sessionId, items) {
            const arr = this.sessionItems[sessionId] || (this.sessionItems[sessionId] = [])
            for (const it of items) {
                const i = it.line_num - 1
                arr[i] = { ...it, content: it.content ?? null }
                clearParsedContent(arr[i])
            }
            this.recomputeVisualItems(sessionId)
        },
        async loadSessionItemsRanges(_projectId, sessionId, ranges, parentSessionId = null) {
            if (!ranges?.length) return true
            const qs = rangesToQS(ranges)
            if (!qs) return false
            try {
                const items = await shareApi().fetchItems(qs, parentSessionId ? sessionId : null)
                this.addSessionItems(sessionId, items)
                return true
            } catch { return false }
        },
        areSessionItemsFetched(sessionId) { return !!this.sessionItems[sessionId] },

        // ── Visual items (simplified: no streaming/optimistic/working/failed) ──
        recomputeVisualItems(sessionId) {
            const items = this.sessionItems[sessionId] || []
            if (!items.length) { this.visualItems[sessionId] = []; this._cache[sessionId] = new Map(); return }
            const settings = useSettingsStore()
            const mode = settings.getDisplayMode
            const expanded = this.expandedGroups[sessionId] || []
            const detailed = new Set(this.detailedBlocks[sessionId] || [])
            const vis = computeVisualItems(items, mode, expanded, false, detailed)
            for (let i = 0; i < vis.length; i++) {
                const isUser = vis[i].kind === 'user_message'
                const prevUser = i > 0 ? vis[i - 1].kind === 'user_message' : null
                const nextUser = i < vis.length - 1 ? vis[i + 1].kind === 'user_message' : null
                vis[i].isBlockStart = i === 0 || isUser !== prevUser
                vis[i].isBlockEnd = i === vis.length - 1 || isUser !== nextUser
            }
            const render = settings.areMessageTimestampsShown ? insertDaySeparators(vis) : vis
            const cache = this._cache[sessionId] || new Map()
            const next = new Map()
            const stable = render.map((vi) => {
                const cached = cache.get(vi.lineNum)
                if (visualItemEqual(cached, vi)) {
                    const p = getParsedContent(vi); if (p !== null) setParsedContent(cached, p)
                    next.set(vi.lineNum, cached); return cached
                }
                const p = getParsedContent(vi); if (p !== null) setParsedContent(vi, p)
                next.set(vi.lineNum, vi); return vi
            })
            this._cache[sessionId] = next
            this.visualItems[sessionId] = stable
        },

        // ── Toggles / detail state (persist within the tab session) ──────
        toggleExpandedGroup(sessionId, head) {
            const arr = this.expandedGroups[sessionId] || (this.expandedGroups[sessionId] = [])
            const i = arr.indexOf(head)
            if (i >= 0) arr.splice(i, 1); else arr.push(head)
            this.recomputeVisualItems(sessionId)
        },
        toggleInternalExpandedGroup(sessionId, lineNum, startIndex) {
            const s = this.internalExpandedGroups[sessionId] || (this.internalExpandedGroups[sessionId] = {})
            const arr = s[lineNum] || (s[lineNum] = [])
            const i = arr.indexOf(startIndex)
            if (i >= 0) arr.splice(i, 1); else arr.push(startIndex)
        },
        toggleBlockDetailedMode(sessionId, u) {
            const arr = this.detailedBlocks[sessionId] || (this.detailedBlocks[sessionId] = [])
            const i = arr.indexOf(u)
            if (i >= 0) arr.splice(i, 1); else arr.push(u)
            this.recomputeVisualItems(sessionId)
        },
        setDetailOpen(sessionId, key, open) {
            const s = this.openDetails[sessionId] || (this.openDetails[sessionId] = {})
            if (open) s[key] = true; else delete s[key]
        },

        // ── Seed helpers used by ShareSessionApp ─────────────────────────
        setSession(session) { this.sessions[session.id] = markRaw(session) },
        setAgentLinks(sessionId, links) {
            const map = {}
            for (const l of links) map[l.tool_use_id] = {
                agentId: l.agent_id, isBackground: l.is_background,
                toolUseLineNum: l.tool_use_line_num, slug: l.agent_slug || null,
            }
            this.agentLinks[sessionId] = map
        },

        // ── No-op write surface (statically imported by reused components) ──
        registerOutgoingSend() {}, removeFailedSend() {}, restoreDraftAttachments() {},
        setProcessState() {}, markItemsLive() {}, clearEndedStreamingBlocks() {},
        auditInflightSends() {}, ensureSessionItemsCoverage() { return Promise.resolve() },
        fetchToolStates() { return Promise.resolve() },
    },
})
```

### 3.8 `NEW` `frontend/src/share-session/shims/settingsStoreShim.js`

```js
// Viewer-local settings for the share bundle. Reactive display mode + color
// scheme (viewer toggles), timestamps/costs seeded from the share options.
import { defineStore } from 'pinia'
import { applyShareColorScheme, getShareColorScheme } from '../theme'

export const useSettingsStore = defineStore('shareSettings', {
    state: () => ({
        displayMode: 'normal',            // bounded to <= max_display_mode by the app
        _colorScheme: getShareColorScheme(),
        areMessageTimestampsShown: true,
        areCostsShown: false,
    }),
    getters: {
        getDisplayMode: (s) => s.displayMode,
        _effectiveColorScheme: (s) => (s._colorScheme === 'dark'
            || (s._colorScheme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches)) ? 'dark' : 'light',
        showDiffs: () => false,
        isMac: () => /Mac/i.test(navigator.platform || ''),
        isTouchDevice: () => matchMedia('(pointer: coarse)').matches,
        isToolDiffSideBySide: () => false,
        isToolDiffWordWrap: () => true,
        isClaudeHybridEnabled: () => false,
        isTitleGenerationEnabled: () => false,
        getTitleSystemPrompt: () => '',
        waTheme: () => 'default',
        waBrand: () => 'cyan',
    },
    actions: {
        setDisplayMode(mode) { this.displayMode = mode; document.body.dataset.displayMode = mode },
        setColorScheme(mode) { this._colorScheme = mode; applyShareColorScheme(mode) },
        setToolDiffSideBySide() {}, setToolDiffWordWrap() {},
    },
})
```

### 3.9 `NEW` `frontend/src/share-session/shims/codeCommentsShim.js`

```js
// No code comments in read-only shares. All-zero counts, empty lists, no-op writes.
import { defineStore } from 'pinia'

export const useCodeCommentsStore = defineStore('shareCodeComments', {
    getters: {
        getCommentsBySession: () => () => [],
        countBySession: () => () => 0,
        countByProjects: () => () => 0,
        countBySource: () => () => 0,
    },
    actions: { hydrateComments() {} },
})
```

### 3.10 `NEW` `frontend/src/share-session/shims/noWebSocket.js`

```js
// The transcript tree statically imports a few WS helpers (ToolUseContent,
// ApiError, FailedSendBanner, providers/*/ws.js). Read-only mode never invokes
// them; export no-ops so the module resolves without pulling the SPA WS layer.
export function sendWsMessage() {}
export function stopSubagent() {}
export function interruptSession() {}
export function requestTitleSuggestion() {}
export function notifyProcessStateChange() {}
export const isConnected = { value: false }
```

### 3.11 `NEW` `frontend/src/share-session/shims/shareLive.js`

```js
// Optional live updates for a mode="live" session share (Phase 5). Connects to
// ws/share/<token>/, appends filtered items into the shim store, refreshes meta.
export function connectShareLive({ tokenPath, sessionId, onItems, onMeta, onClosed }) {
    const wsBase = location.origin.replace(/^http/, 'ws')
    const token = tokenPath.replace(/^\/share\//, '').replace(/\/+$/, '')
    let ws = null, closed = false, backoff = 1000
    function open() {
        if (closed) return
        ws = new WebSocket(`${wsBase}/ws/share/${token}/`)
        ws.onmessage = (ev) => {
            const msg = JSON.parse(ev.data)
            if (msg.type === 'share_items_added') onItems(msg.items)
            else if (msg.type === 'share_meta') onMeta(msg.meta)
            else if (msg.type === 'share_closed') { closed = true; onClosed?.() }
        }
        ws.onopen = () => { backoff = 1000 }
        ws.onclose = () => { if (!closed) { setTimeout(open, backoff); backoff = Math.min(backoff * 2, 15000) } }
    }
    open()
    return () => { closed = true; ws?.close() }
}
```

### 3.12 `NEW` `frontend/src/share-session/ShareItemsList.vue`

Thin re-implementation of `SessionItemsList.vue`'s load/scroll glue over the shim store. No footer, no composer, no search, no WS wiring beyond the optional live feed passed in.

```vue
<script setup>
import { computed, ref, watch, onMounted, provide } from 'vue'
import VirtualScroller from '../../components/virtual-scroller/VirtualScroller.vue'
import SessionItem from '../../components/session/detail/SessionItem.vue'
import GroupToggle from '../../components/session/detail/GroupToggle.vue'
import DaySeparator from '../../components/session/detail/items/DaySeparator.vue'
import { useDataStore } from '../../stores/data'          // aliased → dataStoreShim
import { getParsedContent, hasContent } from '../../utils/parsedContent'
import { useDebounceFn } from '@vueuse/core'

const props = defineProps({
    projectId: { type: String, default: 'share' },
    sessionId: { type: String, required: true },
    parentSessionId: { type: String, default: null },
    lastLine: { type: Number, required: true },
})

const store = useDataStore()
const scrollerRef = ref(null)
const INITIAL = 100, BUFFER = 40, MIN_ITEM = 40

const visualItems = computed(() => store.getSessionVisualItems(props.sessionId))

async function loadInitial() {
    const ranges = []
    if (props.lastLine <= INITIAL) ranges.push([1, props.lastLine])
    else if (props.parentSessionId) ranges.push([1, INITIAL])
    else ranges.push([props.lastLine - INITIAL + 1, props.lastLine])
    const qs = new URLSearchParams()
    for (const [lo, hi] of ranges) qs.append('range', `${lo}:${hi}`)
    const [metadata] = await Promise.all([
        store.loadSessionMetadata(props.projectId, props.sessionId, props.parentSessionId),
        store.loadSessionItemsRanges(props.projectId, props.sessionId, ranges, props.parentSessionId),
    ])
    if (metadata) {
        // Metadata first initializes the array; the ranges call above already
        // added content for the initial window — re-apply metadata then let the
        // content fill (order-independent because both recompute).
        store.initSessionItemsFromMetadata(props.sessionId, metadata)
        await store.loadSessionItemsRanges(props.projectId, props.sessionId, ranges, props.parentSessionId)
    }
}
onMounted(loadInitial)

const pending = ref(null)
const flush = useDebounceFn(async () => {
    const lines = pending.value; pending.value = null
    if (!lines?.length) return
    // Coalesce contiguous line numbers into ranges.
    const sorted = [...new Set(lines)].sort((a, b) => a - b)
    const ranges = []; let s = sorted[0], e = sorted[0]
    for (let i = 1; i < sorted.length; i++) {
        if (sorted[i] === e + 1) e = sorted[i]
        else { ranges.push([s, e]); s = e = sorted[i] }
    }
    ranges.push([s, e])
    await store.loadSessionItemsRanges(props.projectId, props.sessionId, ranges, props.parentSessionId)
}, 120)

function onUpdate({ visibleStartIndex, visibleEndIndex }) {
    const vis = visualItems.value
    if (!vis?.length) return
    const lo = Math.max(0, visibleStartIndex - BUFFER)
    const hi = Math.min(vis.length - 1, visibleEndIndex + BUFFER)
    const need = []
    for (let i = lo; i <= hi; i++) {
        const vi = vis[i]
        if (vi && !vi.isDaySeparator && !hasContent(vi)) need.push(vi.lineNum)
    }
    if (need.length) { pending.value = need; flush() }
}

function toggleGroup(head) { store.toggleExpandedGroup(props.sessionId, head) }

// The reused components inject these; provide only the media rewrite (share-mode).
const shareApi = inject('shareApi')
provide('rewriteContentMediaUrl', (url) => {
    // /artifacts/<sid>/<file> → /share/<t>/media/<file> when sid === shared session.
    const m = /^\/artifacts\/([^/]+)\/([^/?#]+)$/.exec(url)
    if (m && m[1] === props.sessionId) return shareApi.mediaUrl(m[2])
    if (m) return null       // a different session's artifact — not shared
    return url
})
</script>

<template>
    <div class="session-items-list share-items-list">
        <VirtualScroller
            ref="scrollerRef"
            :items="visualItems"
            :item-key="(item) => item.lineNum"
            :min-item-height="MIN_ITEM"
            :buffer="5000"
            :unload-buffer="10000"
            :prevent-auto-scroll-to-bottom="!!parentSessionId"
            class="session-items"
            @update="onUpdate"
        >
            <template #default="{ item }">
                <DaySeparator v-if="item.isDaySeparator" :label="item.dayLabel" :day-key="item.dayKey" />
                <div v-else-if="!hasContent(item)"
                     :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd }"
                     :style="{ minHeight: MIN_ITEM + 'px' }"></div>
                <template v-else-if="item.isGroupHead">
                    <GroupToggle
                        :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd && !item.isExpanded }"
                        :expanded="item.isExpanded" :item-count="item.groupSize" :comments-count="0"
                        @toggle="toggleGroup(item.lineNum)" />
                    <SessionItem v-if="item.isExpanded" :class="{ 'is-block-end': item.isBlockEnd }"
                        :content="getParsedContent(item)" :kind="item.kind" :synthetic-kind="null"
                        :project-id="projectId" :session-id="sessionId" :parent-session-id="parentSessionId"
                        :line-num="item.lineNum" :is-block-end="item.isBlockEnd || false" />
                </template>
                <SessionItem v-else
                    :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd }"
                    :content="getParsedContent(item)" :kind="item.kind" :synthetic-kind="null"
                    :project-id="projectId" :session-id="sessionId" :parent-session-id="parentSessionId"
                    :line-num="item.lineNum" :group-head="item.groupHead" :group-tail="item.groupTail"
                    :prefix-expanded="item.prefixExpanded || false" :suffix-expanded="item.suffixExpanded || false"
                    :detail-toggle-for="item.detailToggleFor ?? null" :is-block-end="item.isBlockEnd || false"
                    @toggle-suffix="toggleGroup(item.suffixGroupHead)" />
            </template>
        </VirtualScroller>
    </div>
</template>
```
> Add `import { inject } from 'vue'` to the script imports (kept out of the snippet above for brevity — include it).

### 3.13 `NEW` `frontend/src/share-session/ShareSessionApp.vue`

```vue
<script setup>
import { ref, reactive, provide, onMounted, computed } from 'vue'
import ShareItemsList from './ShareItemsList.vue'
import SharedSubagentView from './SharedSubagentView.vue'
import GlobalMediaPreview from '../components/media/GlobalMediaPreview.vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { makeShareApi, setShareApi } from './shims/shareApi'
import { connectShareLive } from './shims/shareLive'

const props = defineProps({ tokenPath: String, meta: Object })

const api = makeShareApi(props.tokenPath); setShareApi(api)
provide('shareApi', api)

const store = useDataStore()
const settings = useSettingsStore()
const meta = reactive({ ...props.meta })
const revoked = ref(false)
const updatedBanner = ref(false)

// Seed a session-ish object the reused components read via getSession.
store.setSession({
    id: meta.session_id, provider: meta.provider, project_id: 'share',
    title: meta.title || 'Shared session', total_cost: meta.total_cost ?? null,
    last_line: meta.last_line, git_directory: null, cwd: null, artifacts_dir: null,
    created_at: meta.created_at, last_updated_at: meta.last_updated_at,
})
settings.areMessageTimestampsShown = meta.show_timestamps !== false
settings.areCostsShown = !!meta.total_cost
settings.setDisplayMode(clampMode(meta.max_display_mode || 'normal'))

const displayModes = computed(() => boundedModes(meta.max_display_mode || 'normal'))
function boundedModes(max) {
    const order = ['conversation', 'simplified', 'normal', 'debug']
    return order.slice(0, order.indexOf(max) + 1)
}
function clampMode(m) { return boundedModes(meta.max_display_mode || 'normal').includes(m) ? m : 'normal' }

// Subagent overlay stack (design §8.6).
const subagentStack = ref([])
if (meta.include_subagents) {
    provide('openSubagent', (agentId) => subagentStack.value.push(agentId))
    api.fetchSubagents().then((links) => store.setAgentLinks(meta.session_id, links)).catch(() => {})
}
provide('sessionActive', ref(true))

onMounted(() => {
    if (meta.mode === 'live') {
        connectShareLive({
            tokenPath: props.tokenPath, sessionId: meta.session_id,
            onItems: (items) => store.addSessionItems(meta.session_id, items),
            onMeta: (m) => Object.assign(meta, m),
            onClosed: () => { revoked.value = true },
        })
    }
})
</script>

<template>
    <div class="share-shell">
        <header class="share-header">
            <div class="share-title">
                <wa-icon :name="meta.provider === 'codex' ? 'circle' : 'robot'"></wa-icon>
                <strong>{{ meta.title || 'Shared session' }}</strong>
                <wa-tag size="small" variant="neutral">Read-only</wa-tag>
                <wa-tag v-if="meta.mode === 'live'" size="small" variant="success">Live</wa-tag>
            </div>
            <div class="share-controls">
                <wa-select size="small" :value="settings.displayMode"
                           @change="settings.setDisplayMode($event.target.value)">
                    <wa-option v-for="m in displayModes" :key="m" :value="m">{{ m }}</wa-option>
                </wa-select>
                <wa-switch size="small" :checked="settings.areMessageTimestampsShown"
                           @change="settings.areMessageTimestampsShown = $event.target.checked">Times</wa-switch>
                <wa-button size="small" appearance="plain"
                           @click="settings.setColorScheme(settings._effectiveColorScheme === 'dark' ? 'light' : 'dark')">
                    <wa-icon :name="settings._effectiveColorScheme === 'dark' ? 'sun' : 'moon'"></wa-icon>
                </wa-button>
            </div>
        </header>

        <wa-callout v-if="revoked" variant="warning" class="share-banner">
            This share is no longer available.
        </wa-callout>

        <ShareItemsList
            :session-id="meta.session_id"
            :last-line="meta.last_line"
        />

        <SharedSubagentView v-if="subagentStack.length"
            :stack="subagentStack" @close="subagentStack.pop()" @clear="subagentStack = []" />

        <GlobalMediaPreview />
        <footer class="share-footer">Shared with TwiCC</footer>
    </div>
</template>

<style>
.share-shell { max-width: 60rem; margin: 0 auto; padding: 1rem; }
.share-header { position: sticky; top: 0; z-index: 3; display: flex; justify-content: space-between;
    align-items: center; gap: 1rem; padding: .5rem 0; background: var(--wa-color-surface-default); }
.share-title { display: flex; align-items: center; gap: .5rem; }
.share-controls { display: flex; align-items: center; gap: .5rem; }
.share-footer { text-align: center; color: var(--wa-color-text-quiet); font-size: var(--wa-font-size-s);
    padding: 2rem 0 1rem; }
@media print { .share-header, .share-controls, .share-footer { display: none; } }
</style>
```

### 3.14 `NEW` `frontend/src/share-session/SharedSubagentView.vue`

Drawer overlay hosting a nested `ShareItemsList` on `/api/subagent/<sid>/…`, breadcrumbs, recursion-safe (its own "View Agent" pushes onto the same stack).

```vue
<script setup>
import { computed } from 'vue'
import ShareItemsList from './ShareItemsList.vue'

const props = defineProps({ stack: { type: Array, required: true } })
const emit = defineEmits(['close', 'clear'])
const current = computed(() => props.stack[props.stack.length - 1])
</script>

<template>
    <div class="subagent-drawer">
        <div class="subagent-backdrop" @click="emit('clear')"></div>
        <div class="subagent-panel">
            <header class="subagent-head">
                <nav class="crumbs">
                    <span v-for="(id, i) in stack" :key="id" class="crumb">
                        Agent {{ i + 1 }}<span v-if="i < stack.length - 1"> ›</span>
                    </span>
                </nav>
                <wa-button size="small" appearance="plain" @click="emit('close')">
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
            </header>
            <ShareItemsList :key="current" :session-id="current" :parent-session-id="current" :last-line="100000" />
        </div>
    </div>
</template>

<style scoped>
.subagent-drawer { position: fixed; inset: 0; z-index: 20; }
.subagent-backdrop { position: absolute; inset: 0; background: rgba(0,0,0,.4); }
.subagent-panel { position: absolute; top: 0; right: 0; bottom: 0; width: min(52rem, 100%);
    background: var(--wa-color-surface-default); box-shadow: -4px 0 24px rgba(0,0,0,.3);
    display: flex; flex-direction: column; overflow: auto; }
.subagent-head { position: sticky; top: 0; display: flex; justify-content: space-between;
    align-items: center; padding: .5rem 1rem; background: var(--wa-color-surface-default); }
.crumbs { display: flex; gap: .35rem; font-size: var(--wa-font-size-s); color: var(--wa-color-text-quiet); }
</style>
```
> The subagent's `last-line` is unknown up front; pass a large value so the initial load fetches the first-N window (subagents open at the top). A follow-up meta fetch could tighten it, but the top-N load is correct as-is.

### 3.15 `NEW` `frontend/src/share-session/ShareDocApp.vue`

For markdown/mermaid artifact bookmarks rendered inside the share bundle (design §9.1). Fetches the raw doc and renders it with `MarkdownContent`.

```vue
<script setup>
import { ref, onMounted } from 'vue'
import MarkdownContent from '../components/ui/MarkdownContent.vue'

const props = defineProps({ tokenPath: String, meta: Object })
const source = ref('')
const error = ref(false)
const snapshotAt = ref(props.meta.snapshot_at)
const updated = ref(false)

async function load() {
    try {
        const res = await fetch(props.meta.docUrl, { credentials: 'same-origin' })
        if (!res.ok) throw new Error(String(res.status))
        source.value = await res.text()
    } catch { error.value = true }
}
onMounted(load)

// Poll snapshot freshness (D7) while visible.
onMounted(() => {
    setInterval(async () => {
        if (document.hidden) return
        try {
            const m = await (await fetch(`${props.tokenPath}/api/artifact-meta/`, { credentials: 'same-origin' })).json()
            if (m.snapshot_at && m.snapshot_at !== snapshotAt.value) updated.value = true
        } catch { /* ignore */ }
    }, 30000)
})
</script>

<template>
    <div class="share-doc">
        <wa-callout v-if="updated" variant="brand" class="share-banner">
            This artifact was updated — <a href="#" @click.prevent="location.reload()">Reload</a>
        </wa-callout>
        <wa-callout v-if="error" variant="danger">This document is not available.</wa-callout>
        <MarkdownContent v-else :source="source" :show-toolbar="false" />
        <footer class="share-footer">Shared with TwiCC</footer>
    </div>
</template>

<style>
.share-doc { max-width: 55rem; margin: 0 auto; padding: 1.5rem; }
</style>
```
> Mermaid inside markdown renders via `MarkdownContent`'s existing pipeline — no extra wiring.

### 3.16 `EDIT` `frontend/src/components/ui/MarkdownContent.vue` — router-absence guard + media rewrite

**(a) Router guard.** `useRouter()` returns `undefined` in the router-less share app. Anchor:
```js
    event.preventDefault()
    router.push(href)
}
```
Replace with:
```js
    event.preventDefault()
    if (router) {
        router.push(href)
    } else if (/^https?:/i.test(href)) {
        // Router-less host (share bundle): open absolute links in a new tab; a
        // relative SPA route has no meaning here, so it stays inert.
        window.open(href, '_blank', 'noopener,noreferrer')
    }
}
```

**(b) Media URL rewrite hook.** Add the inject near the other injects (anchor `const fileLinks = inject('markdownFileLinks', null)`):
```js
// Share-mode hook: rewrite in-content media URLs (e.g. /artifacts/<sid>/x.png →
// /share/<t>/media/x.png). Absent in the SPA (behaviour unchanged); when present,
// a null return marks the media as not-shared (rendered as a broken placeholder).
const rewriteContentMediaUrl = inject('rewriteContentMediaUrl', null)
```
Add a rewrite pass in `renderOneBlock`, right after `annotateFileLinksIn(tmp)`:
```js
    annotateFileLinksIn(tmp)
    if (rewriteContentMediaUrl) rewriteContentMediaUrlsIn(tmp)
```
And define the helper next to `annotateFileLinksIn`:
```js
// Rewrite <img src> (and wrapping <a href> to the same target) through the
// injected share-mode hook. A null return means "not shared" → drop the src so
// the browser shows the alt text rather than a broken cross-origin request.
function rewriteContentMediaUrlsIn(root) {
    if (!rewriteContentMediaUrl) return
    for (const img of root.querySelectorAll('img')) {
        const src = img.getAttribute('src')
        if (!src) continue
        const next = rewriteContentMediaUrl(src)
        if (next == null) { img.removeAttribute('src'); img.setAttribute('data-media-unavailable', 'true') }
        else if (next !== src) img.setAttribute('src', next)
    }
}
```

### 3.17 `EDIT` `frontend/src/components/session/detail/items/ToolUseContent.vue` — `openSubagent` seam

Add the inject near the top injects (anchor `const viewFileInFilesTab = inject('viewFileInFilesTab', null)`):
```js
// Share bundle supplies this to open subagents in an in-page overlay instead of
// routing. Default null → SPA keeps its router.push behaviour (navigateToSubagent).
const openSubagent = inject('openSubagent', null)
```
Anchor in `navigateToSubagent`:
```js
function navigateToSubagent() {
    if (!agentId.value) return
```
Replace with:
```js
function navigateToSubagent() {
    if (!agentId.value) return
    if (openSubagent) { openSubagent(agentId.value); return }
```
(The rest of the function — the `router.push(...)` — stays as the default path.)

### 3.18 Build + manual E2E

`cd frontend && npm run build` (the bundle is NOT HMR'd). Manual checklist: both providers; every display mode within the bound; collapsible groups expand/collapse; diffs; mermaid; inline images (rewritten to `/share/<t>/media/…`); a non-shared artifact image shows the alt/placeholder; lightbox; subagent drawer with nesting; live mode appends new lines; revoke-mid-view shows the banner; opening a share adds it to `/share/` (the recent list), and that page lists/opens/removes entries. In the network tab, confirm **zero** requests outside `/share/<t>/…` and `/_twicc/share/…` (the recent list makes no network calls at all).

### 3.19 `NEW` `frontend/src/share-recent/recordView.js`

Shared by both share bundles (share-session **and** artifact-shell, Phase 4) — a per-browser list of opened shares kept in `localStorage` on the share origin (design §12). Purely local; the server never sees it.

```js
// Recent-shares list for the share host homepage (/share/). Isolated by origin
// (the dedicated share host), never sent anywhere. token + kind + title + lastAccess.
const KEY = 'twicc-share-recent'
const MAX = 50

export function readRecentShares() {
    try {
        const arr = JSON.parse(localStorage.getItem(KEY) || '[]')
        return Array.isArray(arr) ? arr : []
    } catch { return [] }
}

function write(list) {
    try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX))) } catch { /* quota/opaque origin */ }
}

/** Upsert an opened share, keyed by token. `tokenPath` is like `/share/<token>`. */
export function recordShareView({ tokenPath, kind, title }) {
    const token = String(tokenPath || '').replace(/\/+$/, '').split('/').pop()
    if (!token) return
    const list = readRecentShares().filter((e) => e.token !== token)
    list.unshift({ token, kind: kind || 'session', title: title || '', lastAccess: new Date().toISOString() })
    write(list)
}

export function removeRecentShare(token) {
    write(readRecentShares().filter((e) => e.token !== token))
}
```

### 3.20 `NEW` `frontend/src/share-session/ShareRecentApp.vue`

The share host homepage (`/share/`, no token, `mode: 'recent'`): lists the shares this browser opened, links each to `/share/<token>/`, and offers a per-row remove. No network, no server data. Entries that 404 on open can simply be removed here.

```vue
<script setup>
import { ref } from 'vue'
import { readRecentShares, removeRecentShare } from '../share-recent/recordView'

const entries = ref(readRecentShares())

function open(token) { window.location.assign(`/share/${token}/`) }
function remove(token) { removeRecentShare(token); entries.value = readRecentShares() }
function when(iso) { try { return new Date(iso).toLocaleString() } catch { return '' } }
</script>

<template>
    <main class="share-recent">
        <h1>Shared with you</h1>
        <p v-if="!entries.length" class="empty">No shared links opened on this browser yet.</p>
        <ul v-else>
            <li v-for="e in entries" :key="e.token">
                <button class="row" type="button" @click="open(e.token)">
                    <wa-tag size="small">{{ e.kind }}</wa-tag>
                    <span class="title">{{ e.title || e.token }}</span>
                    <span class="when">{{ when(e.lastAccess) }}</span>
                </button>
                <wa-button size="small" appearance="plain" title="Remove" @click="remove(e.token)">
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
            </li>
        </ul>
    </main>
</template>

<style scoped>
.share-recent { max-width: 640px; margin: 3rem auto; padding: 0 1rem; }
h1 { font-size: 1.4rem; margin-bottom: 1rem; }
.empty { color: var(--wa-color-text-quiet, #888); }
ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: .3rem; }
li { display: flex; align-items: center; gap: .5rem; }
.row { flex: 1; display: flex; align-items: center; gap: .6rem; background: none; border: 1px solid var(--wa-color-surface-border, #333); border-radius: 8px; padding: .55rem .7rem; color: inherit; cursor: pointer; text-align: left; }
.row:hover { background: var(--wa-color-surface-raised, rgba(255,255,255,.04)); }
.title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.when { color: var(--wa-color-text-quiet, #888); font-size: .8rem; }
</style>
```

All `wa-*` used here (`wa-tag`, `wa-button`, `wa-icon`) are already imported by the share bundle's `main.js` (3.4).

---

## Phase 4 — Share-artifact shell (prompt-less broker)

The dedicated artifact page's shell bundle (`frontend/src/artifact-shell/`) gains a **share mode**: same iframe + inner-doc mechanics, but the broker host forwards every request through `/share/<t>/api/proxy/` with **no preflight and no consent prompt** (the server enforces the owner allowlist, D6).

### 4.1 `EDIT` `frontend/src/composables/useArtifactBroker.js` — share-mode option

The composable's `getConfig` may now return a `mode: 'share'` and a `proxyUrl`. When in share mode, `mountBrokerHost` is called with a share flag so the host skips the prompt/preflight path. Add to the config passed through (anchor `brokerConnection = mountBrokerHost(iframe, {`):
```js
        brokerConnection = mountBrokerHost(iframe, {
            documentUrl: config.documentUrl,
            getBookmarkId: config.getBookmarkId ?? (() => null),
            allowedHosts: config.allowedHosts ?? {},
            showPrompt: showBrokerPrompt,
            persistAllow: config.persistAllow,
            mode: config.mode ?? 'owner',
            proxyUrl: config.proxyUrl,
        })
```

### 4.2 `EDIT` `frontend/src/artifact-broker/host.js` — share proxy path

Make the proxy URL configurable and add a `mode: 'share'` fetch path that skips preflight + prompt (allowlist enforced server-side; a 403 surfaces as a failed fetch).

Anchor:
```js
const PROXY_URL = '/api/artifact-proxy/'
```
Replace with:
```js
const DEFAULT_PROXY_URL = '/api/artifact-proxy/'
```
Change `callProxy` to take the URL (anchor `async function callProxy(body) {`):
```js
async function callProxy(proxyUrl, body) {
    const res = await fetch(proxyUrl, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
        credentials: 'same-origin',
    })
    if (res.status === 401) throw new Error('not authenticated')
    return await res.json()
}
```
In `createBrokerHost`, thread `mode` + `proxyUrl` (anchor `export function createBrokerHost({ documentUrl, getBookmarkId, allowedHosts, showPrompt, persistAllow }) {`):
```js
export function createBrokerHost({ documentUrl, getBookmarkId, allowedHosts, showPrompt, persistAllow, mode = 'owner', proxyUrl = DEFAULT_PROXY_URL }) {
```
Add a share branch at the top of `proxyFetch`, right after the same-origin own-assets shortcut (anchor `if (sameOrigin && url.href.startsWith(ownDir)) return await hostDirectFetch(req)`):
```js
        if (sameOrigin && url.href.startsWith(ownDir)) return await hostDirectFetch(req)

        // Share mode (design §9.3/D6): no preflight, no prompt. The server proxy
        // enforces the owner's allowlist; a non-listed host comes back as an error
        // which surfaces to the artifact as a failed fetch. Same-origin non-asset
        // targets are still brokered (never host-direct) — a viewer holds no cookie.
        if (mode === 'share') {
            const res = await callProxy(proxyUrl, { mode: 'fetch', request: req })
            if (res.error) throw new Error(`broker: ${res.reason || res.error}`)
            return res
        }
```
Update the two remaining owner-path `callProxy(...)` calls to pass `proxyUrl` as the first arg (`callProxy(proxyUrl, { ...preflight... })` and `callProxy(proxyUrl, { ...fetch... })`).

Finally, forward `mode`/`proxyUrl` through `mountBrokerHost` (anchor `export function mountBrokerHost(iframe, opts) {` — `opts` already spreads into `createBrokerHost(opts)`, so no change needed there once `createBrokerHost` accepts them).

### 4.3 `EDIT` `frontend/src/artifact-shell/main.js` — read share-mode island

Anchor:
```js
createApp(ArtifactShellApp, {
    innerDocUrl: shellData.innerDocUrl,
    bookmarkId: shellData.bookmarkId ?? null,
    allowedHosts: shellData.allowedHosts ?? {},
}).mount('#app')
```
Replace with (and add `import { recordShareView } from '../share-recent/recordView'` — 3.19 — with the other top imports):
```js
if (shellData.mode === 'share') {
    // Record this artifact share in the browser's recent list (share host homepage).
    recordShareView({ tokenPath: shellData.tokenPath, kind: 'artifact', title: shellData.title || '' })
}
createApp(ArtifactShellApp, {
    innerDocUrl: shellData.innerDocUrl,
    bookmarkId: shellData.bookmarkId ?? null,
    allowedHosts: shellData.allowedHosts ?? {},
    // Share mode: no bookmark id, no persistence, proxy through the share token.
    mode: shellData.mode === 'share' ? 'share' : 'owner',
    proxyUrl: shellData.mode === 'share' ? `${shellData.tokenPath}/api/proxy/` : undefined,
    snapshotAt: shellData.snapshotAt ?? null,
    tokenPath: shellData.tokenPath ?? null,
}).mount('#app')
```

### 4.4 `EDIT` `frontend/src/artifact-shell/ArtifactShellApp.vue` — share mode + update banner

Add props and pass mode/proxyUrl to the broker; poll `snapshot_at` for the "updated — reload" banner (D7). Anchor the `defineProps`:
```js
const props = defineProps({
    innerDocUrl: { type: String, required: true },
    bookmarkId: { type: Number, default: null },
    allowedHosts: { type: Object, default: () => ({}) },
})
```
Replace with:
```js
const props = defineProps({
    innerDocUrl: { type: String, required: true },
    bookmarkId: { type: Number, default: null },
    allowedHosts: { type: Object, default: () => ({}) },
    mode: { type: String, default: 'owner' },
    proxyUrl: { type: String, default: undefined },
    snapshotAt: { type: [String, null], default: null },
    tokenPath: { type: [String, null], default: null },
})
```
In the `useArtifactBroker` config getter, add `mode`/`proxyUrl` and null the persistence for share mode (anchor the returned object with `documentUrl`, `getBookmarkId`, `allowedHosts`, `persistAllow`):
```js
    () => ({
        documentUrl: new URL(props.innerDocUrl, location.href).href,
        getBookmarkId: () => props.bookmarkId,
        allowedHosts: props.allowedHosts,
        persistAllow: props.mode === 'share' ? undefined : persistAllow,
        mode: props.mode,
        proxyUrl: props.proxyUrl,
    }),
```
Add the update-banner logic (near the bottom of `<script setup>`):
```js
import { ref, onMounted } from 'vue'
const updated = ref(false)
onMounted(() => {
    if (props.mode !== 'share' || !props.tokenPath) return
    setInterval(async () => {
        if (document.hidden) return
        try {
            const m = await (await fetch(`${props.tokenPath}/api/artifact-meta/`, { credentials: 'same-origin' })).json()
            if (m.snapshot_at && m.snapshot_at !== props.snapshotAt) updated.value = true
        } catch { /* ignore */ }
    }, 30000)
})
```
And render the banner above the iframe:
```html
<template>
    <div v-if="updated" class="share-update-banner">
        This artifact was updated — <a href="#" @click.prevent="location.reload()">Reload</a>
    </div>
    <iframe ... />
    <ArtifactBrokerPrompt v-if="mode !== 'share'" :prompt="brokerPrompt" @decision="onBrokerDecision" />
</template>
```
(Keep the prompt component in owner mode only — share mode never prompts. `v-if="mode !== 'share'"` guards it.)

Rebuild rule: editing `artifact-shell/*` / `artifact-broker/*` ⇒ `cd frontend && npm run build`.

### 4.5 Manual E2E (artifact shares)

- Static HTML artifact with **no allowed hosts**: renders; its cross-origin fetches fail gracefully (no prompt).
- HTML artifact whose host the owner allowed *Forever*: that fetch succeeds through `/share/<t>/api/proxy/`; a non-listed host returns `blocked/not_allowed` → failed fetch.
- Snapshot isolation: mutate the source dir → viewer sees nothing until **Propagate**; after propagate, the open viewer's banner appears within ~30 s; reload shows the new content.
- PDF / image / audio / video served directly; `md` / `mmd` rendered via `ShareDocApp`.

---

## Phase 5 — Live updates (session shares, `mode="live"`)

### 5.1 `NEW` `src/twicc/share/consumer.py`

```python
"""Dedicated, server-filtered WS consumer for live session shares (design §10,
O4). Joins the global ``updates`` group but forwards only this share's session
(and, when allowed, its subagents), re-filtered by the display ceiling / frozen
line, re-serialized through the public meta. The viewer never sees the firehose."""

from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from twicc.core.services.share_tokens import aresolve_share, password_fingerprint
from twicc.share.display import display_ceiling
from twicc.share.resolver import SHARE_GRANTS_SESSION_KEY

logger = logging.getLogger(__name__)

WS_CLOSE_SHARE_UNAVAILABLE = 4404


class ShareConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        token = self.scope["url_route"]["kwargs"]["token"]
        share = await aresolve_share(token)
        if share is None or not share.is_active() or share.kind != "session":
            await self.close(code=WS_CLOSE_SHARE_UNAVAILABLE)
            return
        # Snapshot shares never stream.
        opts = share.options or {}
        if opts.get("mode") != "live":
            await self.close(code=WS_CLOSE_SHARE_UNAVAILABLE)
            return
        # Per-link password grant (same fingerprint check as HTTP).
        if share.password_hash:
            session = self.scope.get("session")
            grants = await sync_to_async(lambda: session.get(SHARE_GRANTS_SESSION_KEY, {}))() if session else {}
            if grants.get(share.id) != password_fingerprint(share.password_hash):
                await self.close(code=WS_CLOSE_SHARE_UNAVAILABLE)
                return

        self.share_id = share.id
        self.session_id = share.session_id
        self.max_display_mode = opts.get("max_display_mode", "normal")
        self.include_subagents = opts.get("include_subagents", True)
        self.ceiling = display_ceiling(self.max_display_mode)
        self.descendant_ids = await self._load_descendants(share)
        await self.channel_layer.group_add("updates", self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        try:
            await self.channel_layer.group_discard("updates", self.channel_name)
        except Exception:
            pass

    async def _load_descendants(self, share) -> set[str]:
        if not self.include_subagents:
            return set()
        from twicc.core.models import Session

        ids = await sync_to_async(list)(
            Session.objects.filter(spawn_root_id=self.session_id).values_list("id", flat=True)
        )
        # Also cover direct children not stamped with spawn_root (belt + braces).
        child = await sync_to_async(list)(
            Session.objects.filter(parent_session_id=self.session_id).values_list("id", flat=True)
        )
        return set(ids) | set(child)

    def _visible(self, item: dict) -> bool:
        dl = item.get("display_level")
        if self.ceiling >= 3:
            return True
        return dl is not None and dl <= self.ceiling

    async def broadcast(self, event):
        """Server-side filter: forward only this share's traffic."""
        data = event["data"]
        mtype = data.get("type")

        if mtype == "session_items_added":
            sid = data.get("session_id")
            is_root = sid == self.session_id
            is_sub = self.include_subagents and sid in self.descendant_ids
            if not (is_root or is_sub):
                return
            items = [it for it in (data.get("items") or []) if self._visible(it)]
            if not items:
                return
            await self.send_json({
                "type": "share_items_added",
                "session_id": sid,
                "items": items,
            })
            return

        if mtype == "agent_link_created" and self.include_subagents:
            if data.get("parent_session_id") == self.session_id:
                self.descendant_ids.add(data.get("agent_session_id"))
            return

        if mtype == "session_updated":
            session = data.get("session") or {}
            if session.get("id") != self.session_id:
                return
            meta = await self._public_meta()
            if meta is not None:
                await self.send_json({"type": "share_meta", "meta": meta})
            return

        if mtype in ("share_updated", "share_removed"):
            # This share was revoked / expired / options-changed → close it out.
            sid = data.get("share_id") or (data.get("share") or {}).get("id")
            if sid == self.share_id:
                status = (data.get("share") or {}).get("status")
                if mtype == "share_removed" or status in ("revoked", "expired") \
                        or (data.get("share") or {}).get("options", {}).get("mode") == "snapshot":
                    await self.send_json({"type": "share_closed"})
                    await self.close(code=WS_CLOSE_SHARE_UNAVAILABLE)
            return

    async def _public_meta(self):
        from twicc.core.models import Share
        from twicc.core.serializers import serialize_share_public_meta

        share = await sync_to_async(
            lambda: Share.objects.select_related("session").filter(id=self.share_id).first()
        )()
        if share is None or not share.is_active():
            return None
        return await sync_to_async(serialize_share_public_meta)(share)
```

### 5.2 `EDIT` `src/twicc/asgi.py` — register the share WS route

Anchor:
```python
websocket_urlpatterns = [
    # Terminal with session context
    path("ws/terminal/<str:project_id>/<str:session_id>/<int:terminal_index>/", terminal_application),
    path("ws/terminal/<str:project_id>/<int:terminal_index>/", terminal_application),
    path("ws/terminal/<int:terminal_index>/", terminal_application),
    path("ws/", WSConsumer.as_asgi()),
]
```
Add the share route (import at the top: `from twicc.share.consumer import ShareConsumer`) — place it **before** `ws/` so it doesn't get shadowed:
```python
websocket_urlpatterns = [
    path("ws/terminal/<str:project_id>/<str:session_id>/<int:terminal_index>/", terminal_application),
    path("ws/terminal/<str:project_id>/<int:terminal_index>/", terminal_application),
    path("ws/terminal/<int:terminal_index>/", terminal_application),
    path("ws/share/<str:token>/", ShareConsumer.as_asgi()),
    path("ws/", WSConsumer.as_asgi()),
]
```

### 5.3 Frontend

`shims/shareLive.js` (already written in 3.11) connects when `meta.mode === 'live'`; `ShareSessionApp` feeds `share_items_added` into `store.addSessionItems`, refreshes meta on `share_meta`, and shows the revoked banner on `share_closed`. Snapshot shares never connect.

### 5.4 Test

Open a live session share; send a message in the owner UI; the new user + assistant lines appear in the viewer without reload. Revoke the share → the viewer's `share_closed` banner appears and no further items load. A `DEBUG_ONLY` line (with `max_display_mode: normal`) never reaches the viewer's socket.

---

## Phase 6 — Owner management (REST, store, UI)

### 6.1 `NEW` `src/twicc/share/owner_views.py`

Password-gated REST (under `/api/`, so `PasswordAuthMiddleware` protects it). All mutations delegate to `share_mutation.py`.

```python
"""Owner-side share management REST (design §11). Under /api/ — password-gated."""

from __future__ import annotations

from datetime import datetime

import orjson
from asgiref.sync import sync_to_async
from django.http import Http404, HttpResponseNotAllowed, JsonResponse

from twicc.core.serializers import serialize_share
from twicc.core.services import share_mutation


def _err_response(result):
    return JsonResponse({"errors": [e._asdict() for e in (result.errors or [])]}, status=400)


async def _load(share_id):
    from twicc.core.models import Share

    share = await sync_to_async(
        lambda: Share.objects.select_related("session", "artifact_bookmark").filter(id=share_id).first()
    )()
    if share is None:
        raise Http404("Share not found")
    return share


async def shares_list(request):
    """GET /api/shares/ — all shares. POST /api/shares/ — create."""
    from twicc.core.models import Share

    if request.method == "GET":
        shares = await sync_to_async(list)(
            Share.objects.select_related("session", "artifact_bookmark").all()
        )
        return JsonResponse({"shares": [serialize_share(s) for s in shares]})
    if request.method == "POST":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        # Sharing requires a dedicated share host (design §12): with shareBaseUrl
        # unset, /share/ 404s everywhere, so a fresh link would be dead. Refuse up
        # front — the UI already gates on this; this is the server-side backstop.
        from twicc.synced_settings import read_synced_settings
        if not (read_synced_settings().get("shareBaseUrl") or "").strip():
            return JsonResponse(
                {"error": "share_host_unset",
                 "reason": "Configure a share host in Settings → Sharing first."},
                status=400,
            )
        payload = {
            "kind_target": data.get("kind"),
            "session_id": data.get("session_id"),
            "bookmark_id": data.get("bookmark_id"),
            "label": data.get("label") or "",
            "options": data.get("options") or {},
            "password": data.get("password") or None,
            "expires_at": data.get("expires_at"),
            "notify_on_view": bool(data.get("notify_on_view", False)),
        }
        result = await share_mutation.create_share_from_payload(payload)
        if not result.success:
            return _err_response(result)
        share = await _load(result.share_id)
        return JsonResponse(serialize_share(share), status=201)
    return HttpResponseNotAllowed(["GET", "POST"])


async def share_detail(request, share_id):
    """GET / PATCH / DELETE /api/shares/<id>/."""
    share = await _load(share_id)
    if request.method == "GET":
        return JsonResponse(serialize_share(share))
    if request.method == "PATCH":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        fields = {}
        for k in ("label", "notify_on_view", "options", "password"):
            if k in data:
                fields[k] = data[k]
        if "expires_at" in data:
            raw = data["expires_at"]
            fields["expires_at"] = datetime.fromisoformat(raw) if raw else None
        result = await share_mutation.patch_share(share, fields)
        if not result.success:
            return _err_response(result)
        return JsonResponse(serialize_share(await _load(share_id)))
    if request.method == "DELETE":
        await share_mutation.delete_share(share)
        return JsonResponse({"ok": True})
    return HttpResponseNotAllowed(["GET", "PATCH", "DELETE"])


async def share_revoke(request, share_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    await share_mutation.revoke_share(await _load(share_id), revoked=True)
    return JsonResponse(serialize_share(await _load(share_id)))


async def share_unrevoke(request, share_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    await share_mutation.revoke_share(await _load(share_id), revoked=False)
    return JsonResponse(serialize_share(await _load(share_id)))


async def share_propagate(request, share_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    result = await share_mutation.propagate_share(await _load(share_id))
    if not result.success:
        return _err_response(result)
    return JsonResponse(serialize_share(await _load(share_id)))


async def share_accesses(request, share_id):
    """GET /api/shares/<id>/accesses/ — the pruned recent-views log."""
    from twicc.core.models import ShareAccess

    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    await _load(share_id)  # 404 if unknown
    rows = await sync_to_async(list)(
        ShareAccess.objects.filter(share_id=share_id).order_by("-at")[:200]
    )
    return JsonResponse({"accesses": [
        {"at": r.at.isoformat(), "ip": r.ip, "user_agent": r.user_agent} for r in rows
    ]})
```

### 6.2 `EDIT` `src/twicc/urls.py` — owner REST routes

Add the import: `from .share import owner_views as share_owner_views`. Insert (with the other `/api/` routes, before the SPA catch-all):
```python
    path("api/shares/", share_owner_views.shares_list),
    path("api/shares/<str:share_id>/", share_owner_views.share_detail),
    path("api/shares/<str:share_id>/revoke/", share_owner_views.share_revoke),
    path("api/shares/<str:share_id>/unrevoke/", share_owner_views.share_unrevoke),
    path("api/shares/<str:share_id>/propagate/", share_owner_views.share_propagate),
    path("api/shares/<str:share_id>/accesses/", share_owner_views.share_accesses),
```

### 6.3 `EDIT` `src/twicc/asgi.py` — seed shares on WS connect

Mirror the `artifact_bookmarks_updated` connect-burst (anchor that block). After it, add:
```python
        if self._should_send("shares_updated"):
            from twicc.core.models import Share
            from twicc.core.serializers import serialize_share
            shares = await sync_to_async(list)(
                Share.objects.select_related("session", "artifact_bookmark").all()
            )
            await self.send_json({
                "type": "shares_updated",
                "shares": [serialize_share(s) for s in shares],
            })
```

### 6.4 `NEW` `frontend/src/stores/shares.js`

```js
import { defineStore } from 'pinia'
import { apiFetch } from '../utils/api'

export const useSharesStore = defineStore('shares', {
    state: () => ({ shares: {} }),   // id -> serialized share
    getters: {
        list: (s) => Object.values(s.shares).sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')),
        forSession: (s) => (sessionId) => Object.values(s.shares).filter(x => x.kind === 'session' && x.session_id === sessionId),
        forBookmark: (s) => (bookmarkId) => Object.values(s.shares).filter(x => x.kind === 'artifact' && x.bookmark_id === bookmarkId),
        activeCountForSession: (s) => (sessionId) =>
            Object.values(s.shares).filter(x => x.kind === 'session' && x.session_id === sessionId && x.status === 'active').length,
        activeCountForBookmark: (s) => (bookmarkId) =>
            Object.values(s.shares).filter(x => x.kind === 'artifact' && x.bookmark_id === bookmarkId && x.status === 'active').length,
    },
    actions: {
        setShares(list) { const next = {}; for (const s of list || []) next[s.id] = s; this.shares = next },
        upsertShare(share) { this.shares[share.id] = share },
        removeShare(id) { delete this.shares[id] },
        async loadShares() {
            const res = await apiFetch('/api/shares/')
            if (res.ok) this.setShares((await res.json()).shares)
        },
        async createShare(body) {
            const res = await apiFetch('/api/shares/', {
                method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
            })
            if (!res.ok) throw await res.json().catch(() => ({ error: 'create failed' }))
            const share = await res.json(); this.upsertShare(share); return share
        },
        async patchShare(id, fields) {
            const res = await apiFetch(`/api/shares/${id}/`, {
                method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(fields),
            })
            if (!res.ok) throw await res.json().catch(() => ({ error: 'update failed' }))
            const share = await res.json(); this.upsertShare(share); return share
        },
        async revokeShare(id, revoked = true) {
            const res = await apiFetch(`/api/shares/${id}/${revoked ? 'revoke' : 'unrevoke'}/`, { method: 'POST' })
            if (res.ok) this.upsertShare(await res.json())
        },
        async propagateShare(id) {
            const res = await apiFetch(`/api/shares/${id}/propagate/`, { method: 'POST' })
            if (res.ok) this.upsertShare(await res.json())
        },
        async deleteShare(id) {
            const res = await apiFetch(`/api/shares/${id}/`, { method: 'DELETE' })
            if (res.ok) this.removeShare(id)
        },
        async fetchAccesses(id) {
            const res = await apiFetch(`/api/shares/${id}/accesses/`)
            return res.ok ? (await res.json()).accesses : []
        },
    },
})
```

### 6.5 `EDIT` `frontend/src/composables/useWebSocket.js` — share WS handlers

Next to the `artifact_bookmark_*` cases:
```js
            case 'shares_updated': {
                const { useSharesStore } = await import('../stores/shares')
                useSharesStore().setShares(msg.shares || [])
                break
            }
            case 'share_updated': {
                const { useSharesStore } = await import('../stores/shares')
                useSharesStore().upsertShare(msg.share)
                break
            }
            case 'share_removed': {
                const { useSharesStore } = await import('../stores/shares')
                useSharesStore().removeShare(msg.share_id)
                break
            }
```
> Use the lazy `await import` form (the file already does this pattern for bookmarks in some branches) to avoid a store↔composable static cycle.

### 6.6 `NEW` `frontend/src/utils/shareUrl.js`

```js
import { useSettingsStore } from '../stores/settings'

/** Absolute share URL from a serialized share's url_path. Requires the
 *  `shareBaseUrl` setting — sharing is served only on the dedicated share host and
 *  has no fallback origin (§12). Returns null when it isn't configured; callers gate
 *  the Share UI on `settings.getShareBaseUrl` (empty ⇒ Share entry points disabled). */
export function shareAbsoluteUrl(share) {
    const settings = useSettingsStore()
    const base = (settings.getShareBaseUrl || '').replace(/\/+$/, '')
    if (!base) return null
    return base + share.url_path
}
```

### 6.7 `NEW` `frontend/src/components/share/ShareDialog.vue`

Create/edit dialog. Follows `ProjectEditDialog.vue` patterns (form id, submit-outside via `setAttribute('form', ...)`, `@wa-after-show` focus, `trim`, `wa-callout` errors, `--width` clamp, and **bubbling guards** on nested `wa-select`/`wa-switch`/`wa-hide` with `.self`).

```vue
<script setup>
import { ref, reactive, computed, watch, nextTick } from 'vue'
import { useSharesStore } from '../../stores/shares'
import { shareAbsoluteUrl } from '../../utils/shareUrl'
import { toast } from '../../composables/useToast'

const props = defineProps({
    open: Boolean,
    kind: { type: String, required: true },         // 'session' | 'artifact'
    sessionId: { type: String, default: null },
    bookmarkId: { type: Number, default: null },
    allowedHosts: { type: Object, default: () => ({}) },  // artifact: hosts viewers reach
    defaultTitle: { type: String, default: '' },     // real session title / bookmark name (placeholder)
    edit: { type: Object, default: null },          // existing serialized share when editing
})
const emit = defineEmits(['close'])
const shares = useSharesStore()

const form = reactive({
    label: '', display_title: '', password: '', expires_at: '', notify_on_view: false,
    // session options
    mode: 'live', max_display_mode: 'normal', include_subagents: true,
    show_costs: false, show_timestamps: true, show_title: true,
})
const error = ref('')
const createdUrl = ref('')
const dialogRef = ref(null)
const formId = 'share-dialog-form'

watch(() => props.open, (o) => { if (o) reset() })
function reset() {
    error.value = ''; createdUrl.value = ''
    const e = props.edit
    Object.assign(form, {
        label: e?.label || '', display_title: e?.options?.display_title || '',
        password: '', expires_at: e?.expires_at || '',
        notify_on_view: e?.notify_on_view || false,
        mode: e?.options?.mode || 'live',
        max_display_mode: e?.options?.max_display_mode || 'normal',
        include_subagents: e?.options?.include_subagents ?? true,
        show_costs: e?.options?.show_costs ?? false,
        show_timestamps: e?.options?.show_timestamps ?? true,
        show_title: e?.options?.show_title ?? true,
    })
}

const isSession = computed(() => props.kind === 'session')
const allowedHostList = computed(() => Object.keys(props.allowedHosts || {}))

function sessionOptions() {
    return {
        mode: form.mode, max_display_mode: form.max_display_mode,
        include_subagents: form.include_subagents, show_costs: form.show_costs,
        show_timestamps: form.show_timestamps, show_title: form.show_title,
    }
}

// Options sent for both kinds: session config (session only) + the optional public
// title override. Empty title → omitted → viewers see the real session title / name.
function buildOptions() {
    const opts = isSession.value ? sessionOptions() : {}
    const t = form.display_title.trim()
    if (t) opts.display_title = t
    return opts
}

async function handleSave() {
    error.value = ''
    try {
        if (props.edit) {
            const fields = { label: form.label.trim(), notify_on_view: form.notify_on_view, options: buildOptions() }
            if (form.password) fields.password = form.password
            fields.expires_at = form.expires_at || null
            const share = await shares.patchShare(props.edit.id, fields)
            createdUrl.value = shareAbsoluteUrl(share)
        } else {
            const body = {
                kind: props.kind, label: form.label.trim(),
                password: form.password || null, expires_at: form.expires_at || null,
                notify_on_view: form.notify_on_view, options: buildOptions(),
            }
            if (isSession.value) body.session_id = props.sessionId
            else body.bookmark_id = props.bookmarkId
            const share = await shares.createShare(body)
            createdUrl.value = shareAbsoluteUrl(share)
        }
    } catch (e) {
        error.value = (e?.errors?.[0]?.message) || e?.error || 'Failed to save share'
    }
}

function copyUrl() { navigator.clipboard.writeText(createdUrl.value); toast.success('Share URL copied') }

function onAfterShow() {
    nextTick(() => {
        const submit = dialogRef.value?.querySelector(`button[type="submit"]`)
        submit?.setAttribute('form', formId)
        dialogRef.value?.querySelector('#share-label-input')?.focus()
    })
}
// Guard bubbling wa-hide from nested wa-select/wa-switch (only the dialog's own closes).
function onHide(e) { if (e.target === dialogRef.value) emit('close') }
</script>

<template>
    <wa-dialog ref="dialogRef" :open="open" :label="edit ? 'Edit share' : 'Create share'"
               style="--width: min(560px, calc(100vw - 2rem))"
               @wa-after-show="onAfterShow" @wa-hide="onHide">
        <form :id="formId" @submit.prevent="handleSave">
            <wa-callout v-if="error" variant="danger">{{ error }}</wa-callout>

            <wa-callout variant="warning">
                Anyone with the link can read this {{ isSession ? 'transcript' : 'artifact' }} as-is,
                including file paths, commands and output. There is no redaction.
            </wa-callout>

            <label>Label (private)
                <wa-input id="share-label-input" :value="form.label"
                          @input="form.label = $event.target.value" placeholder="e.g. for Alice"></wa-input>
            </label>

            <label>Title (shown to viewers)
                <wa-input :value="form.display_title" @input="form.display_title = $event.target.value"
                          :placeholder="defaultTitle || 'Default title'"></wa-input>
            </label>

            <template v-if="isSession">
                <label>Snapshot / live
                    <wa-select :value="form.mode" @change.stop="form.mode = $event.target.value">
                        <wa-option value="live">Live (follows the session)</wa-option>
                        <wa-option value="snapshot">Snapshot (frozen now)</wa-option>
                    </wa-select>
                </label>
                <label>Max detail
                    <wa-select :value="form.max_display_mode" @change.stop="form.max_display_mode = $event.target.value">
                        <wa-option value="conversation">Conversation</wa-option>
                        <wa-option value="simplified">Simplified</wa-option>
                        <wa-option value="normal">Normal</wa-option>
                        <wa-option value="debug">Debug (raw JSON)</wa-option>
                    </wa-select>
                </label>
                <wa-switch :checked="form.include_subagents" @change.stop="form.include_subagents = $event.target.checked">Include subagents</wa-switch>
                <wa-switch :checked="form.show_costs" @change.stop="form.show_costs = $event.target.checked">Show costs</wa-switch>
                <wa-switch :checked="form.show_timestamps" @change.stop="form.show_timestamps = $event.target.checked">Show timestamps</wa-switch>
                <wa-switch :checked="form.show_title" @change.stop="form.show_title = $event.target.checked">Show title</wa-switch>
            </template>

            <template v-else>
                <wa-callout v-if="allowedHostList.length" variant="neutral">
                    Viewers will be able to reach these hosts (already allowed on this artifact):
                    <ul><li v-for="h in allowedHostList" :key="h"><code>{{ h }}</code></li></ul>
                </wa-callout>
            </template>

            <label>Password (optional)
                <wa-input type="password" :value="form.password"
                          @input="form.password = $event.target.value"
                          :placeholder="edit?.has_password ? 'unchanged — type to replace' : 'no password'"></wa-input>
            </label>
            <label>Expires (optional)
                <wa-input type="datetime-local" :value="form.expires_at"
                          @input="form.expires_at = $event.target.value"></wa-input>
            </label>
            <wa-switch :checked="form.notify_on_view" @change.stop="form.notify_on_view = $event.target.checked">Notify me when viewed</wa-switch>

            <div v-if="createdUrl" class="share-url">
                <wa-input readonly :value="createdUrl"></wa-input>
                <wa-button @click.stop="copyUrl"><wa-icon slot="start" name="copy"></wa-icon>Copy</wa-button>
            </div>
        </form>
        <div slot="footer">
            <wa-button @click="emit('close')">Close</wa-button>
            <wa-button type="submit" variant="brand" :form="formId">{{ edit ? 'Save' : 'Create link' }}</wa-button>
        </div>
    </wa-dialog>
</template>
```

### 6.8 `NEW` `frontend/src/components/share/ShareListPanel.vue`

Reusable list of shares (URL copy, status chip, view count, last view, edit/revoke/delete, and — for artifact shares — the **outdated** badge + **Propagate** action).

```vue
<script setup>
import { computed } from 'vue'
import { useSharesStore } from '../../stores/shares'
import { shareAbsoluteUrl } from '../../utils/shareUrl'
import { toast } from '../../composables/useToast'

const props = defineProps({ shares: { type: Array, required: true } })
const emit = defineEmits(['edit'])
const store = useSharesStore()

function isOutdated(s) {
    return s.kind === 'artifact' && s.source_updated_at && s.options?.snapshot_at
        && s.source_updated_at > s.options.snapshot_at
}
function copy(s) { navigator.clipboard.writeText(shareAbsoluteUrl(s)); toast.success('Share URL copied') }
async function del(s) {
    if (!confirm('Delete this share link? It cannot be undone.')) return
    await store.deleteShare(s.id)
}
</script>

<template>
    <div class="share-list">
        <div v-for="s in shares" :key="s.id" class="share-row">
            <div class="share-row-main">
                <wa-tag size="small" :variant="s.status === 'active' ? 'success' : (s.status === 'expired' ? 'warning' : 'neutral')">
                    {{ s.status }}
                </wa-tag>
                <span class="share-label">{{ s.label || '(no label)' }}</span>
                <wa-tag v-if="s.has_password" size="small" variant="neutral"><wa-icon name="lock"></wa-icon></wa-tag>
                <wa-tag v-if="isOutdated(s)" size="small" variant="warning">outdated</wa-tag>
                <span class="share-views">{{ s.view_count }} views</span>
            </div>
            <div class="share-row-actions">
                <wa-button size="small" appearance="plain" @click="copy(s)"><wa-icon name="copy"></wa-icon></wa-button>
                <wa-button v-if="isOutdated(s)" size="small" variant="warning" @click="store.propagateShare(s.id)">Propagate</wa-button>
                <wa-button size="small" appearance="plain" @click="emit('edit', s)"><wa-icon name="pen"></wa-icon></wa-button>
                <wa-button v-if="s.status !== 'revoked'" size="small" appearance="plain" @click="store.revokeShare(s.id, true)">Revoke</wa-button>
                <wa-button v-else size="small" appearance="plain" @click="store.revokeShare(s.id, false)">Unrevoke</wa-button>
                <wa-button size="small" appearance="plain" variant="danger" @click="del(s)"><wa-icon name="trash"></wa-icon></wa-button>
            </div>
        </div>
        <p v-if="!shares.length" class="share-empty">No share links yet.</p>
    </div>
</template>
```

### 6.9 `NEW` `frontend/src/components/share/ShareManagerDialog.vue`

Global list grouped by target; opened from the command palette + a `SettingsPopover` row. Hosts a `ShareListPanel` over `store.list` and a `ShareDialog` for edits.

```vue
<script setup>
import { ref } from 'vue'
import { useSharesStore } from '../../stores/shares'
import ShareListPanel from './ShareListPanel.vue'
import ShareDialog from './ShareDialog.vue'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['close'])
const store = useSharesStore()
const editing = ref(null)
</script>

<template>
    <wa-dialog :open="open" label="Shared links" style="--width: min(720px, calc(100vw - 2rem))" @wa-hide="emit('close')">
        <ShareListPanel :shares="store.list" @edit="editing = $event" />
        <ShareDialog v-if="editing" :open="!!editing" :kind="editing.kind"
                     :session-id="editing.session_id" :bookmark-id="editing.bookmark_id"
                     :allowed-hosts="editing.allowed_hosts || {}"
                     :default-title="editing.target_title || editing.target_name || ''"
                     :edit="editing" @close="editing = null" />
        <div slot="footer"><wa-button @click="emit('close')">Close</wa-button></div>
    </wa-dialog>
</template>
```

### 6.10 Entry points (`EDIT`s)

- **Share-host gating (cross-cutting):** every Share entry point is disabled when `settings.getShareBaseUrl` is empty (sharing has no configured host — §12). A tiny shared helper/getter `sharingEnabled = computed(() => !!settings.getShareBaseUrl)` drives the disabled state; the disabled control's tooltip/hint links to Settings → Sharing ("Configure a share host to create links"). `ShareDialog`'s submit is likewise disabled with the same hint. The store's `createShare` surfaces the server `share_host_unset` 400 as this same message (backstop).
- **`SessionHeader.vue`**: add a Share button in the header action row that opens `ShareDialog` (kind `session`, `sessionId`, `defaultTitle` = the session's title), plus a "shared" badge state when `sharesStore.activeCountForSession(sessionId) > 0`. Import `useSharesStore` + `ShareDialog`; hold a local `showShareDialog` ref.
- **`ArtifactBookmarkList.vue`** row action + **`ArtifactBookmarkDialog.vue`** section + **`FilePane.vue`** bookmark affordance: a "Share…" action opening `ShareDialog` (kind `artifact`, `bookmarkId`, `allowedHosts` from the bookmark, `defaultTitle` = the bookmark name). When the file is not yet bookmarked (FilePane), "Share…" first offers to create the bookmark, then opens the dialog with the new id.
- **Bookmark-delete guard**: in the bookmark delete paths, when `sharesStore.activeCountForBookmark(id) > 0`, prompt with the count (CASCADE will kill them).
- **Badges**: session rows/header + bookmark rows show a small share indicator when actively shared (cheap: the store indexes by target).

### 6.11 `EDIT` — new synced setting `shareBaseUrl` (Settings → Sharing)

The share host is **required** and settings-driven: no `shareBaseUrl` ⇒ no sharing (the gate 404s `/share/` — Phase 7 — and the Share UI is disabled). It gets its own **Sharing** section in the Settings panel with an Apply button, mirroring `publicBaseUrl` (which is committed via a discrete Apply, not on every keystroke — see the `publicBaseUrl*` block in `SettingsPopover.vue` at :595-761 / :1043 and the input at :1165).

- **`src/twicc/synced_settings.py`** (anchor `"publicBaseUrl": "",`): add below it `"shareBaseUrl": "",`.
- **`frontend/src/constants.js`** (anchor `'externalNotificationTargets', 'publicBaseUrl',`): add `'shareBaseUrl'` to the synced-keys list.
- **`frontend/src/stores/settings.js`**:
  - state (anchor `publicBaseUrl: null,`): add `shareBaseUrl: null,`
  - validators (anchor `publicBaseUrl: (v) => typeof v === 'string',`): add `shareBaseUrl: (v) => typeof v === 'string',`
  - getter (anchor `getPublicBaseUrl: (state) => state.publicBaseUrl,`): add `getShareBaseUrl: (state) => state.shareBaseUrl,`
  - a `setShareBaseUrl` mirroring `setPublicBaseUrl` (trim + strip trailing slashes)
  - the apply/persist blocks (anchor `publicBaseUrl: store.publicBaseUrl,` at ~:1077, and the apply site that reads `publicBaseUrl`): add the `shareBaseUrl` line alongside.
- **`SettingsPopover.vue`** — add a dedicated **Sharing** section (its own group, not folded into Notifications), mirroring the `publicBaseUrl` input+Apply machinery: local `shareBaseUrlInput` ref, `shareBaseUrlNormalized`/`shareBaseUrlModified` computeds, `shareBaseUrlApplyIcon`, an apply handler calling `store.setShareBaseUrl`, seeded on open (:1043) and on `@wa-after-show`. Copy the pattern verbatim from the `publicBaseUrl*` block (:595-761, :1165). Hint: "Dedicated share host — a hostname distinct from this app, pointing at the same port. Required to create share links; a different port on the same hostname is not enough."
  - **Client-side validation** in the apply handler: reject a value whose hostname equals `window.location.hostname` (the working origin) with a `wa-callout variant="danger"` "The share host must be a different hostname from this app." Empty is allowed (disables sharing). Accept a bare hostname or a full URL — normalise via `new URL(v.includes('//') ? v : 'https://' + v)` to read `.hostname`; reject an unparseable value.
  - Add a "Shared links" row in this section that opens `ShareManagerDialog`.

### 6.12 `EDIT` — register new WA elements + command palette

- Any new `wa-*` used only by the new SPA components (all already imported in `main.js`: dialog, input, select, option, switch, callout, tag, button, icon) — verify none are missing; add if so.
- Command palette: add a "Manage shared links" entry opening `ShareManagerDialog` (no keyboard shortcut by default — so no `shortcutGroups` change needed per the house rule; if a shortcut is later added, update `SettingsPopover.vue` `shortcutGroups` + palette + tooltip together).

---

## Phase 7 — Mandatory dedicated share origin (O3)

Sharing is served **only** on the configured share host and **never** on the working origin (design §12). One always-installed Host-header gate on the single listener reads the `shareBaseUrl` synced setting live — no dedicated port, no env var. A share host is a hostname distinct from the working origin (cookies aren't port-scoped), pointing at the **same** local port.

### 7.1 `NEW` `src/twicc/share/asgi_filter.py`

```python
"""ASGI gate enforcing the mandatory dedicated share origin (design §12).

Sharing is served ONLY on the configured share host — the hostname of the
``shareBaseUrl`` synced setting — and NEVER on the working origin. The gate reads
that setting LIVE per request (the way ``external_notifications.py`` reads
``publicBaseUrl``), so an Apply in Settings → Sharing takes effect with no restart:

  request Host == share host   → ShareOnlyApp: only /share/… (+ public share assets),
                                  everything else 404; WS only for ws/share/… .
  request Host != share host   → the full app, but /share/… + /_twicc/share/… → 404,
                                  and ws/share/… closed.
  share host unset (empty)     → /share/… 404s everywhere (sharing disabled).

Rationale: the public share surface must never share an origin with the
authenticated working app. Cookie isolation needs a distinct *hostname* (cookies
are not port-scoped), so the share host is a different hostname pointing at the
same local port. ``/mcp`` (pre-Django ``http_router``) is absent from the
allow-list below, so it is never reachable on the share host.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# Served on the share host. NOTE: /_twicc/artifact-shell/ and the broker shim are
# shared with the working app's own artifact preview, so they are allowed on the
# share host but must NOT be 404'd on the working origin — only /share/ and
# /_twicc/share/ are share-exclusive.
_SHARE_ONLY_PREFIXES = (
    "/share/",
    "/_twicc/share/",
    "/_twicc/artifact-shell/",
    "/_twicc/artifact-broker-shim.js",
)
# Share-exclusive: 404'd on any non-share origin.
_SHARE_EXCLUSIVE_PREFIXES = ("/share/", "/_twicc/share/")


def _share_only_allowed(path: str) -> bool:
    return any(path.startswith(p) for p in _SHARE_ONLY_PREFIXES) or path == "/favicon.ico"


def _live_share_host() -> str:
    """Current share hostname from the synced settings (lower-case, no scheme/port)."""
    from twicc.synced_settings import read_synced_settings  # cached in-memory dict
    raw = (read_synced_settings().get("shareBaseUrl") or "").strip()
    if not raw:
        return ""
    host = urlsplit(raw if "//" in raw else "//" + raw).hostname or ""
    return host.lower()


def _request_host(scope) -> str:
    for name, value in scope.get("headers") or ():
        if name == b"host":
            return value.decode("latin1").split(":", 1)[0].lower()
    return ""


async def _reply_404(send):
    await send({"type": "http.response.start", "status": 404,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"Not found"})


async def _reply_204(send):
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


class ShareOnlyApp:
    """Wrap an ASGI app, exposing ONLY the share surface (used on the share host)."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        path = scope.get("path", "")
        if stype == "http":
            if path == "/favicon.ico":
                return await _reply_204(send)
            if not _share_only_allowed(path):
                return await _reply_404(send)
            return await self.inner(scope, receive, send)
        if stype == "websocket":
            if not path.startswith("/ws/share/"):
                await send({"type": "websocket.close", "code": 4404})
                return
            return await self.inner(scope, receive, send)
        # lifespan et al. pass through.
        return await self.inner(scope, receive, send)


class ShareHostGate:
    """Route by the live ``shareBaseUrl`` host. On the share host, serve share-only;
    on every other origin, serve the full app but hide the share surface (404)."""

    def __init__(self, full_app, share_only_app):
        self.full_app = full_app
        self.share_only_app = share_only_app

    async def __call__(self, scope, receive, send):
        stype = scope.get("type")
        if stype not in ("http", "websocket"):
            return await self.full_app(scope, receive, send)
        share_host = _live_share_host()
        if share_host and _request_host(scope) == share_host:
            return await self.share_only_app(scope, receive, send)
        # Non-share origin: full app, but the share surface is invisible here.
        path = scope.get("path", "")
        if stype == "http" and any(path.startswith(p) for p in _SHARE_EXCLUSIVE_PREFIXES):
            return await _reply_404(send)
        if stype == "websocket" and path.startswith("/ws/share/"):
            await send({"type": "websocket.close", "code": 4404})
            return
        return await self.full_app(scope, receive, send)
```

### 7.2 `EDIT` `src/twicc/asgi.py` — install the mandatory share-host gate

At the very end, after `application = BlackNoise(...)` and `application.add(...)`, add:
```python
# Mandatory dedicated share origin (design §12): /share/ is served ONLY on the
# configured share host (the shareBaseUrl hostname) and NEVER on the working
# origin. The gate reads shareBaseUrl LIVE, so an Apply in Settings → Sharing takes
# effect on the next request with no restart. Wrapped ABOVE BlackNoise so the share
# host never reaches the /static/ mount it doesn't use.
from twicc.share.asgi_filter import ShareHostGate, ShareOnlyApp  # noqa: E402
application = ShareHostGate(application, ShareOnlyApp(application))
```
Unconditional: with `shareBaseUrl` unset the gate simply 404s `/share/` everywhere (sharing disabled), so there is nothing to feature-flag.

### 7.3 README

Add a "Sharing / dedicated origin" recipe: sharing requires a **second hostname**, distinct from the working origin, pointing at the **same** local port (e.g. a second Cloudflare Tunnel hostname → the same service). Set it in Settings → Sharing (`shareBaseUrl`). The working origin never serves `/share/`; the share host serves only `/share/` and can carry its own provider-side access rules (e.g. Cloudflare Access). A different *port* on the same hostname is intentionally not enough — cookies aren't port-scoped, so it wouldn't isolate the working session cookie.

---

## Phase 8 — View tracking & notifications

### 8.1 `NEW` `src/twicc/share/view_tracking.py`

> Forward-referenced by Phase 2 (`session_views.share_session_page` / `artifact_views.share_artifact_page` call `note_view`). Create this file as part of Phase 2's dependencies OR land Phase 8 before running Phase 2's page path. `note_view` is import-local in those views, so the surface only executes at request time.

```python
"""Batched share view tracking (design §13). ``note_view`` records an in-memory
touch on the page path; a 30s flush task persists counters + ``ShareAccess`` rows,
prunes, broadcasts ``share_updated``, and fires the optional external notification.
Same coalescing philosophy as ``auth.tokens`` last-used flushing."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 30
# share_id -> list[(at_iso, ip, user_agent)]
_pending: dict[str, list[tuple[str, str, str]]] = {}
# share_id -> label snapshot (for the notification copy), captured at note time.
_labels: dict[str, str] = {}
_MAX_ACCESS_ROWS = 500


def _client_ip(request) -> str:
    from twicc.auth.views import _get_client_ip
    return _get_client_ip(request)


def note_view(share, request) -> None:
    """Record a page view in memory (no I/O). Called after the password check."""
    at = datetime.now(tz=timezone.utc).isoformat()
    ua = (request.headers.get("User-Agent") or "")[:255]
    _pending.setdefault(share.id, []).append((at, _client_ip(request), ua))
    _labels[share.id] = share.label or ""


def _drain() -> dict[str, list[tuple[str, str, str]]]:
    snapshot = {k: v for k, v in _pending.items() if v}
    _pending.clear()
    return snapshot


def _persist(snapshot: dict[str, list[tuple[str, str, str]]]) -> list[str]:
    """Blocking DB work — run via ``asyncio.to_thread``. Returns share ids updated."""
    from django.db.models import Max
    from twicc.core.models import Share, ShareAccess

    updated: list[str] = []
    for share_id, views in snapshot.items():
        share = Share.objects.filter(id=share_id).first()
        if share is None:
            continue
        share.view_count = (share.view_count or 0) + len(views)
        last_iso = max(v[0] for v in views)
        share.last_viewed_at = datetime.fromisoformat(last_iso)
        share.save(update_fields=["view_count", "last_viewed_at", "updated_at"])
        ShareAccess.objects.bulk_create([
            ShareAccess(share_id=share_id, ip=ip[:64], user_agent=ua) for (_at, ip, ua) in views
        ])
        # Prune to the newest _MAX_ACCESS_ROWS.
        count = ShareAccess.objects.filter(share_id=share_id).count()
        if count > _MAX_ACCESS_ROWS:
            cutoff = ShareAccess.objects.filter(share_id=share_id).order_by("-at") \
                .values_list("id", flat=True)[_MAX_ACCESS_ROWS:_MAX_ACCESS_ROWS + 1].first()
            if cutoff is not None:
                threshold = ShareAccess.objects.filter(id=cutoff).values_list("at", flat=True).first()
                ShareAccess.objects.filter(share_id=share_id, at__lte=threshold).delete()
        updated.append(share_id)
    return updated


# Notification throttle: share_id -> (last_sent_monotonic, suppressed_count)
_notify_state: dict[str, tuple[float, int]] = {}
_NOTIFY_THROTTLE_SECONDS = 3600


async def _maybe_notify(share_id: str, view_count: int) -> None:
    """Fire a 'share viewed' external notification (first view, then ≤1/hour)."""
    import time

    from asgiref.sync import sync_to_async
    from twicc.core.models import Share
    from twicc.external_notifications import _send  # reuse the Apprise send path
    from twicc.synced_settings import read_synced_settings

    share = await sync_to_async(lambda: Share.objects.filter(id=share_id).first())()
    if share is None or not share.notify_on_view:
        return
    settings = await sync_to_async(read_synced_settings)()
    targets = [t for t in settings.get("externalNotificationTargets") or []
               if isinstance(t, dict) and t.get("enabled") and t.get("url") and t.get("tested") is True]
    if not targets:
        return
    now = time.monotonic()
    last, suppressed = _notify_state.get(share_id, (0.0, 0))
    if last and now - last < _NOTIFY_THROTTLE_SECONDS:
        _notify_state[share_id] = (last, suppressed + 1)
        return
    extra = f" ({suppressed} more views since the last alert)" if suppressed else ""
    _notify_state[share_id] = (now, 0)
    label = share.label or share.id
    await _send([t["url"] for t in targets], "Share viewed",
                f"Your share '{label}' was viewed.{extra}")


async def start_share_view_flush_task(stop_event: asyncio.Event) -> None:
    """Flush pending share views every _FLUSH_INTERVAL s. Started in run_server."""
    from twicc.core.services.share_mutation import broadcast_share_updated

    logger.info("Share view-tracking flush task started (every %ss)", _FLUSH_INTERVAL)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_FLUSH_INTERVAL)
        except asyncio.TimeoutError:
            pass
        else:
            break
        snapshot = _drain()
        if not snapshot:
            continue
        try:
            from asgiref.sync import sync_to_async
            from twicc.core.models import Share

            updated = await asyncio.to_thread(_persist, snapshot)
            for share_id in updated:
                share = await sync_to_async(
                    lambda sid=share_id: Share.objects.select_related("session", "artifact_bookmark").filter(id=sid).first()
                )()
                if share is not None:
                    await broadcast_share_updated(share)
                    await _maybe_notify(share_id, share.view_count)
        except Exception:  # noqa: BLE001 — keep the loop alive
            for share_id, views in snapshot.items():
                _pending.setdefault(share_id, []).extend(views)
            logger.warning("Share view flush failed (re-queued)", exc_info=True)
```

### 8.2 `EDIT` `src/twicc/cli/run.py` — start the flush task

Add the import (anchor `from twicc.auth.tokens import start_last_used_flush_task`):
```python
from twicc.share.view_tracking import start_share_view_flush_task  # noqa: E402
```
Create the task next to `last_used_flush_task` (anchor that line):
```python
    last_used_flush_task = asyncio.create_task(start_last_used_flush_task(shutdown_event))
    share_view_flush_task = asyncio.create_task(start_share_view_flush_task(shutdown_event))
```
Cancel it in the shutdown path (anchor the token flush cancel):
```python
        logger.info("Stopping token last-used flush task...")
        await _cancel_task(last_used_flush_task, "Token last-used flush task")

        logger.info("Stopping share view flush task...")
        await _cancel_task(share_view_flush_task, "Share view flush task")
```

### 8.3 Owner UI — recent views

`ShareListPanel` already renders `view_count` / `last_viewed_at` (WS-synced via `share_updated`). Add a per-share expandable "Recent views" that calls `store.fetchAccesses(id)` and lists `at` / `ip` / `user_agent`.

---

## Phase 9 — CLI, RPC, docs (sharing is human-only — no skill, no MCP tools)

### 9.1 `NEW` `src/twicc/cli/share.py` (read commands — direct DB)

```python
"""``twicc share list`` / ``show`` — read-only, direct DB (works with the server
down). Prints full URLs from the shareBaseUrl synced setting; when it is unset,
prints the relative ``/share/<token>/`` path with a note (sharing has no configured
host — links only resolve on the dedicated share origin, §12)."""

from twicc.cli._output import emit_error, emit_json


def _base_url() -> str:
    from twicc.synced_settings import read_synced_settings
    return (read_synced_settings().get("shareBaseUrl") or "").strip().rstrip("/")


def list_main(*, kind: str | None = None, session: str | None = None,
              project: str | None = None, include_revoked: bool = False,
              limit: int = 50, offset: int = 0) -> None:
    import django
    django.setup()

    from twicc.core.models import Share
    from twicc.core.serializers import serialize_share

    qs = Share.objects.select_related("session", "artifact_bookmark").all()
    if kind is not None:
        qs = qs.filter(kind=kind)
    if session is not None:
        qs = qs.filter(session_id=session)
    if project is not None:
        from twicc.projects import project_scope_ids
        qs = qs.filter(session__project_id__in=project_scope_ids(project))
    rows = list(qs[offset:offset + limit])
    base = _base_url()
    out = []
    for s in rows:
        if include_revoked or s.status() != "revoked":
            data = serialize_share(s)
            data["url"] = (base + data["url_path"]) if base else data["url_path"]
            out.append(data)
    emit_json(out)


def show_main(share_id: str) -> None:
    import django
    django.setup()

    from twicc.core.models import Share
    from twicc.core.serializers import serialize_share

    s = Share.objects.select_related("session", "artifact_bookmark").filter(id=share_id).first()
    if s is None:
        emit_error(f"Error: share {share_id!r} not found.", code=1)
    data = serialize_share(s)
    base = _base_url()
    data["url"] = (base + data["url_path"]) if base else data["url_path"]
    emit_json(data)
```

### 9.2 `NEW` `src/twicc/cli/share_mutation.py` (write commands — drop-request)

Mirrors `cli/artifacts_mutation.py` (`_run_drop` heartbeat + poll). One function per verb building the `share:*` payload:

```python
"""``twicc share create/update/revoke/unrevoke/delete/propagate`` — drop-request
glue (server resolves + broadcasts). Same plumbing as artifacts_mutation._run_drop."""

from __future__ import annotations

import typer

from twicc.cli.artifacts_mutation import _run_drop  # reuse the heartbeat + poll helper


def run_create_session(*, session_id: str, label: str, password: str | None,
                       expires_at: str | None, mode: str, options: dict, timeout: int) -> None:
    _run_drop(
        {"kind_target": "session", "session_id": session_id, "label": label,
         "password": password, "expires_at": expires_at,
         "options": {**options, "mode": mode}},
        kind="share:create", success_status="created", timeout=timeout,
    )


def run_create_artifact(*, bookmark_id: int, label: str, password: str | None,
                        expires_at: str | None, options: dict, timeout: int) -> None:
    _run_drop(
        {"kind_target": "artifact", "bookmark_id": bookmark_id, "label": label,
         "password": password, "expires_at": expires_at, "options": options},
        kind="share:create", success_status="created", timeout=timeout,
    )


def run_update(*, share_id: str, fields: dict, timeout: int) -> None:
    _run_drop({"share_id": share_id, "fields": fields}, kind="share:update",
              success_status="updated", timeout=timeout)


def run_simple(*, share_id: str, kind: str, success: str, timeout: int) -> None:
    _run_drop({"share_id": share_id}, kind=kind, success_status=success, timeout=timeout)
```
> `_run_drop` is defined in `cli/artifacts_mutation.py` and importable. If preferred, hoist it into a small shared `cli/_drop_request/run.py`; not required.

### 9.3 `EDIT` `src/twicc/cli/__init__.py` — register the `share` app

Mirror the `artifacts_app` block:
```python
share_app = typer.Typer(name="share", help="List / show shares (read). Manage share links (create/revoke/…).", invoke_without_command=True)
app.add_typer(share_app)


@share_app.callback(invoke_without_command=True)
def _share_default(
    ctx: typer.Context,
    kind: str = typer.Option(None, "--kind", help="Filter by kind: session | artifact."),
    session: str = typer.Option(None, "--session", help="Filter by session id."),
    project: str = typer.Option(None, "--project", help="Filter by project (worktrees included)."),
    include_revoked: bool = typer.Option(False, "--include-revoked", help="Include revoked shares."),
    limit: int = typer.Option(50), offset: int = typer.Option(0),
) -> None:
    """List shares as JSON (default action; read-only, direct DB)."""
    if ctx.invoked_subcommand is not None:
        return
    from twicc.cli.share import list_main
    list_main(kind=kind, session=session,
              project=derive_project_id(project)[0] if project else None,
              include_revoked=include_revoked, limit=limit, offset=offset)


@share_app.command(name="show")
def _share_show(share_id: str = typer.Argument(help="Share id (shr_…).")) -> None:
    """Show one share as JSON (read-only)."""
    from twicc.cli.share import show_main
    show_main(share_id)


# ── Mutation commands (human-only: no skill, no MCP tool — O5) ──────────────
share_create_app = typer.Typer(name="create", help="Create a share link.", invoke_without_command=True)
share_app.add_typer(share_create_app)


@share_create_app.command(name="session")
def _share_create_session(
    session_id: str = typer.Argument(...),
    label: str = typer.Option("", "--label"),
    password: str = typer.Option(None, "--password"),
    expires: str = typer.Option(None, "--expires", help="ISO 8601."),
    live: bool = typer.Option(True, "--live/--frozen", help="Live-follow or snapshot."),
    max_display: str = typer.Option("normal", "--max-display"),
    include_subagents: bool = typer.Option(True, "--include-subagents/--no-subagents"),
    show_costs: bool = typer.Option(False, "--show-costs/--no-costs"),
    title: str = typer.Option(None, "--title", help="Public title shown to viewers (default: the session title)."),
    timeout: int = typer.Option(30, "--timeout"),
) -> None:
    from twicc.cli.share_mutation import run_create_session
    run_create_session(
        session_id=session_id, label=label, password=password, expires_at=expires,
        mode="live" if live else "snapshot",
        options={"max_display_mode": max_display, "include_subagents": include_subagents,
                 "show_costs": show_costs, "display_title": title or ""},
        timeout=timeout,
    )


@share_create_app.command(name="artifact")
def _share_create_artifact(
    bookmark_id: int = typer.Argument(...),
    label: str = typer.Option("", "--label"),
    password: str = typer.Option(None, "--password"),
    expires: str = typer.Option(None, "--expires"),
    title: str = typer.Option(None, "--title", help="Public title shown to viewers (default: the bookmark name)."),
    timeout: int = typer.Option(30, "--timeout"),
) -> None:
    from twicc.cli.share_mutation import run_create_artifact
    run_create_artifact(bookmark_id=bookmark_id, label=label, password=password,
                        expires_at=expires, options={"display_title": title or ""}, timeout=timeout)


@share_app.command(name="revoke")
def _share_revoke(share_id: str = typer.Argument(...), timeout: int = typer.Option(30)) -> None:
    from twicc.cli.share_mutation import run_simple
    run_simple(share_id=share_id, kind="share:revoke", success="updated", timeout=timeout)


@share_app.command(name="unrevoke")
def _share_unrevoke(share_id: str = typer.Argument(...), timeout: int = typer.Option(30)) -> None:
    from twicc.cli.share_mutation import run_simple
    run_simple(share_id=share_id, kind="share:unrevoke", success="updated", timeout=timeout)


@share_app.command(name="delete")
def _share_delete(share_id: str = typer.Argument(...), timeout: int = typer.Option(30)) -> None:
    from twicc.cli.share_mutation import run_simple
    run_simple(share_id=share_id, kind="share:delete", success="deleted", timeout=timeout)


@share_app.command(name="propagate")
def _share_propagate(share_id: str = typer.Argument(...), timeout: int = typer.Option(30)) -> None:
    from twicc.cli.share_mutation import run_simple
    run_simple(share_id=share_id, kind="share:propagate", success="updated", timeout=timeout)


@share_app.command(name="update")
def _share_update(
    share_id: str = typer.Argument(...),
    label: str = typer.Option(None, "--label"),
    password: str = typer.Option(None, "--password"),
    expires: str = typer.Option(None, "--expires"),
    timeout: int = typer.Option(30),
) -> None:
    from twicc.cli.share_mutation import run_update
    fields = {}
    if label is not None:
        fields["label"] = label
    if password is not None:
        fields["password"] = password
    if expires is not None:
        fields["expires_at"] = expires or None
    run_update(share_id=share_id, fields=fields, timeout=timeout)
```
RPC exposure is automatic (every CLI command → `POST /rpc/<command>`). Read-only-mode sessions can't reach the write path (they're pull-only).

### 9.4 No skill (O5: sharing is human-only)

**Do NOT create a `twicc-share` skill.** Sharing is not surfaced to agents at all — no skill teaches `list`/`show` or anything else, and no plugin `version` bump is needed (no bundle change). The full CLI exists for a human at a terminal only. (The MCP surface is closed in 9.5.)

### 9.5 `EDIT` `src/twicc/mcp/tools.py` — keep share entirely out of the MCP tool surface (O5)

TwiCC's MCP server (`/mcp`) auto-exposes CLI command *roots* as MCP tools via `build_mcp_registry() = build_registry(excluded_roots=MCP_EXCLUDED_ROOTS)`. Exclude the whole `share` root so no `mcp__twicc__share_*` tool exists — reads or mutations.

Anchor:
```python
MCP_EXCLUDED_ROOTS: frozenset[str] = frozenset(
    (set(LOCAL_ONLY_COMMANDS) - {"whoami"}) | {"settings"}
)
```
Replace with:
```python
MCP_EXCLUDED_ROOTS: frozenset[str] = frozenset(
    (set(LOCAL_ONLY_COMMANDS) - {"whoami"}) | {"settings", "share"}
)
```
Consequence: agents are never pointed at sharing (no skill, no MCP tool). A human runs `twicc share …` at a terminal.

> RPC exposure (`/rpc/<command>`) is unaffected and intentional: share commands remain reachable over RPC with a full-scope Bearer token (a held secret = the owner). The cookie-scope read allowlist (`COOKIE_READONLY_COMMANDS`) already excludes them from ambient-cookie callers.

### 9.6 Docs (`EDIT`)

- **`SKILLS-AND-CLI.md`**: new `## Sharing` section (mirror `## Artifacts`), documenting the **full** CLI (repo doc, humans see everything).
- **`CLAUDE.md`** + **`AGENTS.md`** (kept in sync): a short Sharing paragraph in Architecture/Models — `Share` model one-liner, `/share/` public prefix + `/_twicc/share/` bundle, the share bundle is not-HMR'd (rebuild after editing `share-session/*`). Concise, matching neighbouring entries.
- **README**: sharing section + the dedicated-origin recipe (Phase 7.3).
- **CHANGELOG**: proposed only, never written without an explicit ask (house rule).

---

## Phase 10 — Final pass

- `pytest` in the worktree: `cd <worktree> && TWICC_DATA_DIR=$PWD uv run --active pytest tests/test_share_*.py` (the `--active` venv rule; else it runs against main/src).
- `ruff check src/twicc/share src/twicc/core/services/share_*.py` (line-length 120).
- `cd frontend && npm run build` — all five bundles (SPA, shim, artifact-shell, browser-companion, share-session).
- Manual E2E matrix: Phase 3 + Phase 4 checklists + password flow + expiration + revocation + notify-on-view + share-host gate (share host serves only `/share/`; working origin 404s `/share/`; empty `shareBaseUrl` disables sharing; the recent-views homepage at `/share/`).
- Self-review against the design decisions (§18) and the security summary (§15): uniform 404, no owner credentials on share pages, server-side content filtering, allowlist enforced server-side for the share proxy.
- Hand to the user: **migration reminder** (`0124_share` created, apply via devctl restart — never `migrate` by hand), **restart reminder**, and `npm install` only if deps changed (none expected — penpal/interceptors already present).

---

## Phase ordering

Phases 1→2 strictly sequential (2 imports the Phase 8 `view_tracking` at request time — create that file no later than the first Phase 2 page hit). 3/4 depend on 2; 5 on 3; 6 anytime after 2 (URLs need 2–3 to work end-to-end); 7 independent after 2; 8 after 2; 9 last. Each phase is one coherent, commit-sized unit — but do **not** commit without an explicit user ask (house rule).

