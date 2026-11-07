"""
Read/write workspaces from/to workspaces.json in the data directory.

Workspaces group projects into named collections with optional layout and
filter configuration. They are stored as a simple JSON object in
<data_dir>/workspaces.json.

In addition to the low-level :func:`read_workspaces` helper, this module hosts
the validation helpers shared by the UI and the CLI, the ID slugifier, and the
atomic create/update/delete operations called by
:mod:`twicc.core.services.workspace_mutation`. Every write goes through the
cross-process :func:`twicc.atomic_json.locked_json_file` lock, so the backend's
writers and the ``twicc`` CLI (a separate process) never clobber one another.
"""

import logging
import re
from typing import NamedTuple

import orjson
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer

from twicc.core.ui_messages import ui_message_text
from twicc.atomic_json import locked_json_file
from twicc.paths import get_workspaces_path

logger = logging.getLogger(__name__)


MAX_WORKSPACE_NAME_LENGTH = 20

# Same character class as the frontend's `_generateId`
# (frontend/src/stores/workspaces.js): keep alphanumeric, underscore, hyphen
# in the slug; everything else collapses to a hyphen.
_SLUG_INVALID_CHARS_RE = re.compile(r"[^a-z0-9_-]")
_SLUG_REPEATED_HYPHENS_RE = re.compile(r"-{2,}")
_SLUG_TRIM_HYPHENS_RE = re.compile(r"^-|-$")

# Accepts CSS hex colors (#rgb, #rrggbb, #rrggbbaa) — same format produced
# by the UI's `<wa-color-picker>`. Other CSS color forms (named, rgb(),
# hsl()) are rejected from the CLI to keep the validation rule simple and
# unambiguous; users wanting a fancier color can still edit through the UI.
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


# The read-modify-write cycle on workspaces.json is serialised by the
# cross-process ``flock`` each op takes via ``locked_json_file`` (and the WS
# whole-blob handler via ``file_lock``). It covers BOTH the backend's writers
# (auto-add from the watcher/views, the UI whole-blob update) AND the ``twicc``
# CLI atomic ops running in a separate process — so neither side reads a stale
# snapshot and clobbers the other. (Replaces a former in-process
# ``asyncio.Lock`` that did not see the CLI process.)


class WorkspaceMutationError(NamedTuple):
    """One structured error returned by a workspace mutation.

    ``field`` is the public name the caller used (e.g. ``"--name"``,
    ``"WORKSPACE_ID"``, ``"--add-project"``); ``code`` is the machine-
    readable token the CLI / UI can switch on (``invalid_name``,
    ``duplicate_name``, ``workspace_not_found``, ...).
    """
    field: str
    code: str
    message: str


class WorkspaceMutationResult(NamedTuple):
    """Outcome of a workspace mutation (create / update / delete).

    ``success=True`` → ``workspace_id`` is set; ``workspace`` carries the
    final state for create / update (None for delete).
    ``success=False`` → ``errors`` is a non-empty list and the other
    fields may be None.
    """
    success: bool
    workspace_id: str | None
    workspace: dict | None
    errors: list[WorkspaceMutationError] | None


# ---------------------------------------------------------------------------
# Low-level IO
# ---------------------------------------------------------------------------


def read_workspaces() -> dict:
    """Read workspaces from workspaces.json.

    Returns an empty dict if the file doesn't exist or is invalid.
    """
    path = get_workspaces_path()
    try:
        data = orjson.loads(path.read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return {}
    for ws in data.get("workspaces", []):
        if isinstance(ws, dict):
            migrate_workspace_browser_urls(ws)
    return data


def match_pattern(directory: str, pattern: str) -> bool:
    """Check if a directory path matches a pattern using ``*`` as wildcard.

    If the pattern contains no ``*``, it is treated as a directory prefix
    (``/some/path`` behaves like ``/some/path/*``).
    """
    effective = pattern if "*" in pattern else pattern.rstrip("/") + "/*"
    regex = re.compile("^" + ".*".join(re.escape(part) for part in effective.split("*")) + "$", re.IGNORECASE)
    return regex.search(directory) is not None


# ---------------------------------------------------------------------------
# Validation helpers (pure, no IO)
# ---------------------------------------------------------------------------


def validate_workspace_name(
    name: str,
    *,
    existing_workspaces: list[dict],
    current_id: str | None = None,
    field: str = "name",
) -> list[WorkspaceMutationError]:
    """Validate a workspace name: trimmed, non-empty, ≤ MAX length, unique.

    ``existing_workspaces`` is the current contents of ``workspaces.json``
    (the ``"workspaces"`` list). ``current_id`` is the id of the workspace
    being renamed (excluded from the uniqueness check); leave None for
    creation. ``field`` is echoed back in the returned errors so callers
    can route per-flag messages (``"--name"`` vs ``"NAME"``, etc.).
    """
    trimmed = (name or "").strip()
    if not trimmed:
        return [WorkspaceMutationError(field, "invalid_name",
                                       ui_message_text("Name cannot be empty (or whitespace only).", 'backend.workspaces.name_required'))]
    if len(trimmed) > MAX_WORKSPACE_NAME_LENGTH:
        return [WorkspaceMutationError(field, "invalid_name",
                                       ui_message_text(f"Name must be ≤ {MAX_WORKSPACE_NAME_LENGTH} characters "
                                       f"(got {len(trimmed)}).", 'backend.workspaces.name_length', {"maximum": MAX_WORKSPACE_NAME_LENGTH, "length": len(trimmed)}))]
    lower = trimmed.lower()
    for ws in existing_workspaces:
        if current_id is not None and ws.get("id") == current_id:
            continue
        if (ws.get("name") or "").strip().lower() == lower:
            return [WorkspaceMutationError(field, "duplicate_name",
                                           ui_message_text(f"A workspace named {trimmed!r} already exists "
                                           f"(id: {ws.get('id')!r}).", 'backend.workspaces.duplicate_name', {"name": repr(trimmed), "id": repr(ws.get("id"))}))]
    return []


def validate_color(color: str | None, *, field: str = "color") -> list[WorkspaceMutationError]:
    """Validate a CSS color value (hex only). ``None`` and empty are OK."""
    if color is None or color == "":
        return []
    if not _HEX_COLOR_RE.match(color):
        return [WorkspaceMutationError(field, "invalid_color",
                                       ui_message_text(f"Color must be a CSS hex value (#rgb, #rrggbb, or "
                                       f"#rrggbbaa); got {color!r}.", 'backend.workspaces.invalid_color', {"color": repr(color)}))]
    return []


def normalize_browser_url(value: str | None) -> str | None:
    """Trim a Browser-pane URL; empty collapses to ``None`` (= clear/inherit)."""
    if value is None:
        return None
    return value.strip() or None


def validate_browser_url(url: str | None, *, field: str = "browser_url") -> list[WorkspaceMutationError]:
    """Validate an already-normalized Browser-pane URL: http(s) only, ≤ 2000 chars.

    ``None`` is OK (= clear). One home for the rule — shared by the project
    PUT, the session PATCH, the workspace/project mutation services, and the
    CLI commands, like ``validate_color``.
    """
    if url is None:
        return []
    if not url.startswith(("http://", "https://")):
        return [WorkspaceMutationError(field, "invalid_value",
                                       ui_message_text(f"{field} must be an http(s) URL.", 'backend.workspaces.url_required', {"field": field}))]
    if len(url) > 2000:
        return [WorkspaceMutationError(field, "invalid_value",
                                       ui_message_text(f"{field} must be 2000 characters or less.", 'backend.workspaces.url_length', {"field": field}))]
    return []


MAX_BROWSER_URL_LABEL_LENGTH = 100


def normalize_browser_url_entries(
    value,
    *,
    field: str = "browser_urls",
) -> tuple[list[dict] | None, list[WorkspaceMutationError]]:
    """Validate + canonicalize a full saved-browser-URLs list.

    The shared shape for ``Project.browser_urls`` and a workspace's
    ``browserUrls``: a list of ``{"url": str, "label"?: str, "default"?: true}``
    entries — URLs unique within the list, at most one entry flagged
    ``default``. Returns ``(canonical_list, [])`` on success or
    ``(None, errors)``; canonical entries are sparse (``label`` omitted when
    empty, ``default`` omitted when false).
    """
    if not isinstance(value, list):
        return None, [WorkspaceMutationError(field, "invalid_value",
                                             ui_message_text(f"{field} must be a list of entries.", 'backend.workspaces.entries_required', {"field": field}))]
    canonical: list[dict] = []
    seen_urls: set[str] = set()
    defaults = 0
    for i, entry in enumerate(value):
        where = f"{field}[{i}]"
        if not isinstance(entry, dict):
            return None, [WorkspaceMutationError(field, "invalid_value",
                                                 ui_message_text(f"{where} must be an object.", 'backend.workspaces.entry_object_required', {"field": where}))]
        url = entry.get("url")
        if not isinstance(url, str):
            return None, [WorkspaceMutationError(field, "invalid_value",
                                                 ui_message_text(f"{where}.url must be a string.", 'backend.workspaces.entry_url_string', {"field": where}))]
        url = normalize_browser_url(url)
        if url is None:
            return None, [WorkspaceMutationError(field, "invalid_value",
                                                 ui_message_text(f"{where}.url cannot be empty.", 'backend.workspaces.entry_url_required', {"field": where}))]
        url_errors = validate_browser_url(url, field=f"{where}.url")
        if url_errors:
            return None, url_errors
        if url in seen_urls:
            return None, [WorkspaceMutationError(field, "duplicate_url",
                                                 ui_message_text(f"{field} lists {url!r} more than once.", 'backend.workspaces.duplicate_url', {"field": field, "url": repr(url)}))]
        seen_urls.add(url)

        label = entry.get("label")
        if label is not None and not isinstance(label, str):
            return None, [WorkspaceMutationError(field, "invalid_value",
                                                 ui_message_text(f"{where}.label must be a string or null.", 'backend.workspaces.entry_label_string', {"field": where}))]
        label = (label or "").strip() or None
        if label is not None and len(label) > MAX_BROWSER_URL_LABEL_LENGTH:
            return None, [WorkspaceMutationError(
                field, "invalid_value",
                ui_message_text(f"{where}.label must be ≤ {MAX_BROWSER_URL_LABEL_LENGTH} characters "
                f"(got {len(label)}).", 'backend.workspaces.entry_label_length', {"field": where, "maximum": MAX_BROWSER_URL_LABEL_LENGTH, "length": len(label)}))]

        default = entry.get("default", False)
        if not isinstance(default, bool):
            return None, [WorkspaceMutationError(field, "invalid_value",
                                                 ui_message_text(f"{where}.default must be a boolean.", 'backend.workspaces.entry_default_boolean', {"field": where}))]
        if default:
            defaults += 1
            if defaults > 1:
                return None, [WorkspaceMutationError(field, "multiple_defaults",
                                                     ui_message_text(f"{field} flags more than one entry as default.", 'backend.workspaces.multiple_defaults', {"field": field}))]

        item: dict = {"url": url}
        if label is not None:
            item["label"] = label
        if default:
            item["default"] = True
        canonical.append(item)
    return canonical, []


def add_browser_url_entry(
    entries: list[dict],
    url: str,
    *,
    label: str | None = None,
    set_default: bool = False,
) -> list[dict]:
    """Return a new canonical list with ``url`` added (or updated in place).

    ``entries`` is assumed canonical and ``url`` already normalized/validated.
    An already-listed URL is not duplicated: its label is updated when one is
    given, and ``set_default`` moves the default flag to it. The first URL of
    an empty list always becomes the default. Idempotent.
    """
    updated = [dict(e) for e in entries]
    make_default = set_default or not updated
    existing = next((e for e in updated if e.get("url") == url), None)
    if existing is None:
        existing = {"url": url}
        updated.append(existing)
    if label is not None and label.strip():
        existing["label"] = label.strip()
    if make_default:
        for e in updated:
            e.pop("default", None)
        existing["default"] = True
    return updated


def remove_browser_url_entry(entries: list[dict], url: str) -> list[dict]:
    """Return a new list without ``url`` (idempotent — absent URL is a no-op).

    The default flag is not re-assigned when the default entry is removed:
    consumers fall back to the first entry when none is flagged.
    """
    return [dict(e) for e in entries if e.get("url") != url]


def set_default_browser_url_entry(entries: list[dict], url: str) -> tuple[list[dict], bool]:
    """Return ``(new_list, found)`` with the default flag moved to ``url``."""
    updated = [dict(e) for e in entries]
    target = next((e for e in updated if e.get("url") == url), None)
    if target is None:
        return updated, False
    for e in updated:
        e.pop("default", None)
    target["default"] = True
    return updated, True


def clean_browser_url_ops(payload: dict) -> tuple[dict, list[WorkspaceMutationError]]:
    """Validate the saved-browser-URL op fields of an update payload.

    Shared by the project and workspace ``update`` drop-file glues. Reads
    ``add_browser_url`` (``{"url", "label"?, "set_default"?}``),
    ``remove_browser_url``, ``set_default_browser_url`` and
    ``clear_browser_urls`` from ``payload`` and returns ``(ops, errors)`` —
    ``ops`` maps the same four names to normalized values ready for the
    atomic ops (all-None/False when absent).
    """
    ops = {
        "add_browser_url": None,
        "remove_browser_url": None,
        "set_default_browser_url": None,
        "clear_browser_urls": False,
    }
    errors: list[WorkspaceMutationError] = []

    clear = payload.get("clear_browser_urls", False)
    if not isinstance(clear, bool):
        errors.append(WorkspaceMutationError("clear_browser_urls", "invalid_payload",
                                             ui_message_text("clear_browser_urls must be a boolean.", 'backend.workspaces.clear_urls_boolean')))
    else:
        ops["clear_browser_urls"] = clear

    def _clean_url(field: str, flag: str) -> str | None:
        raw = payload.get(field)
        if raw is None:
            return None
        if not isinstance(raw, str):
            errors.append(WorkspaceMutationError(field, "invalid_payload",
                                                 ui_message_text(f"{field} must be a string or null.", 'backend.workspaces.string_or_null', {"field": field})))
            return None
        url = normalize_browser_url(raw)
        if url is None:
            errors.append(WorkspaceMutationError(flag, "invalid_value",
                                                 ui_message_text(f"{flag} cannot be empty.", 'backend.workspaces.flag_required', {"field": flag})))
            return None
        errors.extend(validate_browser_url(url, field=flag))
        return url

    remove_url = _clean_url("remove_browser_url", "--remove-browser-url")
    if remove_url is not None:
        ops["remove_browser_url"] = remove_url

    set_default_url = _clean_url("set_default_browser_url", "--set-default-browser-url")
    if set_default_url is not None:
        ops["set_default_browser_url"] = set_default_url

    add = payload.get("add_browser_url")
    if add is not None:
        if not isinstance(add, dict):
            errors.append(WorkspaceMutationError("add_browser_url", "invalid_payload",
                                                 ui_message_text("add_browser_url must be an object or null.", 'backend.workspaces.add_url_object')))
        else:
            url = add.get("url")
            if not isinstance(url, str):
                errors.append(WorkspaceMutationError("add_browser_url", "invalid_payload",
                                                     ui_message_text("add_browser_url.url must be a string.", 'backend.workspaces.add_url_string')))
            else:
                url = normalize_browser_url(url)
                if url is None:
                    errors.append(WorkspaceMutationError("--add-browser-url", "invalid_value",
                                                         ui_message_text("--add-browser-url cannot be empty.", 'backend.workspaces.add_url_required')))
                else:
                    errors.extend(validate_browser_url(url, field="--add-browser-url"))
            label = add.get("label")
            if label is not None and not isinstance(label, str):
                errors.append(WorkspaceMutationError("add_browser_url", "invalid_payload",
                                                     ui_message_text("add_browser_url.label must be a string or null.", 'backend.workspaces.add_label_string')))
            else:
                label = (label or "").strip() or None
                if label is not None and len(label) > MAX_BROWSER_URL_LABEL_LENGTH:
                    errors.append(WorkspaceMutationError(
                        "--browser-url-label", "invalid_value",
                        ui_message_text(f"--browser-url-label must be ≤ {MAX_BROWSER_URL_LABEL_LENGTH} "
                        f"characters (got {len(label)}).", 'backend.workspaces.browser_label_length', {"maximum": MAX_BROWSER_URL_LABEL_LENGTH, "length": len(label)})))
            set_default = add.get("set_default", False)
            if not isinstance(set_default, bool):
                errors.append(WorkspaceMutationError("add_browser_url", "invalid_payload",
                                                     ui_message_text("add_browser_url.set_default must be a boolean.", 'backend.workspaces.add_default_boolean')))
            if not errors:
                ops["add_browser_url"] = {"url": url, "label": label, "set_default": set_default}

    return ops, errors


def migrate_workspace_browser_urls(ws: dict) -> bool:
    """In-place legacy ``browserUrl`` (single string) → ``browserUrls`` (entry list).

    Returns True when the workspace dict was modified. Runs on every read
    (:func:`read_workspaces`) and inside the atomic ops before they touch the
    list, so pre-migration files keep working without a one-shot rewrite.
    """
    if "browserUrl" not in ws:
        return False
    legacy = ws.pop("browserUrl")
    if "browserUrls" not in ws:
        url = normalize_browser_url(legacy) if isinstance(legacy, str) else None
        ws["browserUrls"] = [{"url": url, "default": True}] if url else []
    return True


def validate_pattern(pattern: str, *, field: str = "pattern") -> list[WorkspaceMutationError]:
    """Validate an auto-add directory pattern: trimmed, non-empty."""
    trimmed = (pattern or "").strip()
    if not trimmed:
        return [WorkspaceMutationError(field, "invalid_pattern",
                                       ui_message_text("Pattern cannot be empty (or whitespace only).", 'backend.workspaces.pattern_required'))]
    return []


def slugify_workspace_id(name: str, *, existing_ids: set[str]) -> str:
    """Generate a workspace ID slug from a name, appending -2/-3/... on collision.

    Mirrors the frontend's ``_generateId`` (frontend/src/stores/workspaces.js)
    byte-for-byte so the same name produces the same id on both sides:
    lowercase, replace non-alphanumeric with ``-``, collapse repeated
    hyphens, trim leading/trailing hyphens. Falls back to ``"workspace"``
    if nothing survives.
    """
    base = name.lower()
    base = _SLUG_INVALID_CHARS_RE.sub("-", base)
    base = _SLUG_REPEATED_HYPHENS_RE.sub("-", base)
    base = _SLUG_TRIM_HYPHENS_RE.sub("", base)
    if not base:
        base = "workspace"
    if base not in existing_ids:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing_ids:
        suffix += 1
    return f"{base}-{suffix}"


# ---------------------------------------------------------------------------
# Existing background helper (untouched API)
# ---------------------------------------------------------------------------


async def auto_add_project_to_workspaces(project_id: str, directory: str) -> None:
    """Auto-add a newly detected project to workspaces whose patterns match
    its directory.

    The read-modify-write on ``workspaces.json`` runs inside the
    cross-process ``locked_json_file`` lock so a concurrent
    ``_handle_update_workspaces`` (whole-blob writes from the UI) or a CLI
    workspace op can't clobber the appended project_id (and vice versa). If at
    least one workspace was modified, broadcasts ``workspaces_updated`` outside
    the lock.

    Idempotent: a workspace that already lists the project is skipped, no
    write, no broadcast.
    """
    def _read_modify_write() -> list[dict] | None:
        with locked_json_file(get_workspaces_path(), default={}) as txn:
            workspaces = txn.data.get("workspaces", [])
            modified = False
            for ws in workspaces:
                patterns = ws.get("autoProjectPatterns", [])
                if not patterns or project_id in ws.get("projectIds", []):
                    continue
                if any(match_pattern(directory, p) for p in patterns):
                    ws.setdefault("projectIds", []).append(project_id)
                    modified = True
                    logger.info("Auto-added project %s to workspace %r",
                                project_id, ws.get("name", ws.get("id")))
            if not modified:
                return None
            txn.write()
            return workspaces

    workspaces = await sync_to_async(_read_modify_write)()
    if workspaces is None:
        return

    await _broadcast_workspaces_updated(workspaces)


async def add_project_to_workspaces(project_id: str, workspace_ids: list[str]) -> None:
    """Add a project to an explicit list of workspaces (by id).

    The symmetric companion to :func:`auto_add_project_to_workspaces`: that
    one adds a project to workspaces whose *patterns* match its directory,
    this one to the workspaces the user *explicitly* picked (e.g. from the
    project edit dialog at creation time). Both append under the cross-process
    ``locked_json_file`` lock so neither clobbers the other — nor a concurrent
    whole-blob UI write, nor a CLI op. Doing the explicit add server-side (instead of a
    frontend whole-blob write right after the project is created) is what
    keeps it from racing with, and overwriting, the auto-add that
    ``register_project`` runs at creation.

    Idempotent: a workspace that already lists the project, or an id that
    matches no workspace, is silently skipped. Broadcasts
    ``workspaces_updated`` once, outside the lock, only if something changed.
    """
    wanted = [wid for wid in dict.fromkeys(workspace_ids) if wid]
    if not wanted:
        return

    def _read_modify_write() -> list[dict] | None:
        with locked_json_file(get_workspaces_path(), default={}) as txn:
            workspaces = txn.data.get("workspaces", [])
            wanted_set = set(wanted)
            modified = False
            for ws in workspaces:
                if ws.get("id") not in wanted_set:
                    continue
                if project_id in ws.get("projectIds", []):
                    continue
                ws.setdefault("projectIds", []).append(project_id)
                modified = True
                logger.info("Added project %s to workspace %r (explicit)",
                            project_id, ws.get("name", ws.get("id")))
            if not modified:
                return None
            txn.write()
            return workspaces

    workspaces = await sync_to_async(_read_modify_write)()
    if workspaces is None:
        return

    await _broadcast_workspaces_updated(workspaces)


# ---------------------------------------------------------------------------
# Atomic create / update / delete (CLI / future-WS entry points)
# ---------------------------------------------------------------------------


async def create_workspace_atomic(
    *,
    name: str,
    color: str | None = None,
    project_ids: list[str] | None = None,
    auto_project_patterns: list[str] | None = None,
    archived: bool = False,
    browser_urls: list[dict] | None = None,
) -> WorkspaceMutationResult:
    """Atomically create a new workspace. Returns the new workspace dict.

    ``browser_urls`` is assumed already canonicalized through
    :func:`normalize_browser_url_entries`.

    The full create flow runs under the cross-process ``locked_json_file``
    lock so the name uniqueness check and the slug collision check see the
    same snapshot used for the write. After the lock is released, broadcasts
    the full new workspaces list as ``workspaces_updated``.

    Validation errors (empty/too-long name, duplicate name, invalid color,
    invalid pattern) are returned as a failed ``WorkspaceMutationResult``
    and never raise.
    """
    project_ids = list(project_ids or [])
    auto_project_patterns = list(auto_project_patterns or [])

    def _read_validate_write() -> WorkspaceMutationResult:
        with locked_json_file(get_workspaces_path(), default={}) as txn:
            workspaces = txn.data.setdefault("workspaces", [])

            errors: list[WorkspaceMutationError] = []
            errors.extend(validate_workspace_name(name, existing_workspaces=workspaces))
            errors.extend(validate_color(color))
            for p in auto_project_patterns:
                errors.extend(validate_pattern(p))
            if errors:
                return WorkspaceMutationResult(False, None, None, errors)

            trimmed_name = name.strip()
            existing_ids = {ws.get("id") for ws in workspaces if ws.get("id")}
            ws_id = slugify_workspace_id(trimmed_name, existing_ids=existing_ids)

            # Dedupe while preserving order (append semantics).
            deduped_projects: list[str] = []
            seen_projects: set[str] = set()
            for pid in project_ids:
                if pid not in seen_projects:
                    deduped_projects.append(pid)
                    seen_projects.add(pid)

            deduped_patterns: list[str] = []
            seen_patterns: set[str] = set()
            for p in auto_project_patterns:
                trimmed_p = p.strip()
                if trimmed_p not in seen_patterns:
                    deduped_patterns.append(trimmed_p)
                    seen_patterns.add(trimmed_p)

            ws = {
                "id": ws_id,
                "name": trimmed_name,
                "archived": bool(archived),
                "projectIds": deduped_projects,
                "color": color if color else None,
                "autoProjectPatterns": deduped_patterns,
                "browserUrls": list(browser_urls or []),
            }
            workspaces.append(ws)
            txn.write()
            return WorkspaceMutationResult(True, ws_id, ws, None)

    result = await sync_to_async(_read_validate_write)()

    if result.success:
        # Re-read inside the broadcast path so the payload always reflects
        # the latest on-disk state (cheap, and immune to a parallel auto-add
        # broadcast slipping in between).
        await _broadcast_after_write()
    return result


async def update_workspace_atomic(
    workspace_id: str,
    *,
    new_name: str | None = None,
    color: str | None = None,
    unset_color: bool = False,
    add_projects: list[str] | None = None,
    remove_projects: list[str] | None = None,
    add_patterns: list[str] | None = None,
    remove_patterns: list[str] | None = None,
    archived: bool | None = None,
    add_browser_url: dict | None = None,
    remove_browser_url: str | None = None,
    set_default_browser_url: str | None = None,
    clear_browser_urls: bool = False,
) -> WorkspaceMutationResult:
    """Atomically apply a patch to an existing workspace.

    A ``None`` (or empty list) for any keyword leaves the corresponding
    field untouched. ``unset_color=True`` and ``color=<value>`` are
    mutually exclusive — the caller must enforce that constraint before
    calling (this function trusts its arguments and would silently let the
    unset win).

    Saved browser URLs are patched through ops on the ``browserUrls`` entry
    list, applied in order remove → add → set-default:

    - ``add_browser_url``: ``{"url": str, "label": str|None, "set_default": bool}``
      with the URL already normalized/validated (idempotent on a listed URL).
    - ``remove_browser_url`` / ``clear_browser_urls``: idempotent removals.
    - ``set_default_browser_url``: fails with ``url_not_found`` when the URL
      is not in the list.

    Add/remove on project_ids and patterns are idempotent (silently skip
    duplicates / absentees), matching the auto-add helper's semantics.
    """
    add_projects = list(add_projects or [])
    remove_projects = list(remove_projects or [])
    add_patterns = list(add_patterns or [])
    remove_patterns = list(remove_patterns or [])

    def _read_validate_write() -> WorkspaceMutationResult:
        with locked_json_file(get_workspaces_path(), default={}) as txn:
            workspaces = txn.data.setdefault("workspaces", [])
            ws = next((w for w in workspaces if w.get("id") == workspace_id), None)
            if ws is None:
                return WorkspaceMutationResult(False, None, None, [
                    WorkspaceMutationError("WORKSPACE_ID", "workspace_not_found",
                                           ui_message_text(f"Workspace {workspace_id!r} not found.", 'backend.workspaces.id_missing', {"id": repr(workspace_id)})),
                ])

            errors: list[WorkspaceMutationError] = []
            if new_name is not None:
                errors.extend(validate_workspace_name(
                    new_name,
                    existing_workspaces=workspaces,
                    current_id=workspace_id,
                    field="--name",
                ))
            if not unset_color and color is not None:
                errors.extend(validate_color(color, field="--color"))
            for p in add_patterns:
                errors.extend(validate_pattern(p, field="--add-pattern"))
            if errors:
                return WorkspaceMutationResult(False, workspace_id, None, errors)

            if new_name is not None:
                ws["name"] = new_name.strip()
            if unset_color:
                ws["color"] = None
            elif color is not None:
                ws["color"] = color
            if archived is not None:
                ws["archived"] = bool(archived)

            if clear_browser_urls or remove_browser_url or add_browser_url or set_default_browser_url:
                migrate_workspace_browser_urls(ws)
                entries = list(ws.get("browserUrls") or [])
                if clear_browser_urls:
                    entries = []
                if remove_browser_url:
                    entries = remove_browser_url_entry(entries, remove_browser_url)
                if add_browser_url:
                    entries = add_browser_url_entry(
                        entries,
                        add_browser_url["url"],
                        label=add_browser_url.get("label"),
                        set_default=bool(add_browser_url.get("set_default")),
                    )
                if set_default_browser_url:
                    entries, found = set_default_browser_url_entry(entries, set_default_browser_url)
                    if not found:
                        return WorkspaceMutationResult(False, workspace_id, None, [
                            WorkspaceMutationError(
                                "--set-default-browser-url", "url_not_found",
                                ui_message_text(f"URL {set_default_browser_url!r} is not in the workspace's "
                                "saved browser URLs.", 'backend.workspaces.saved_url_missing', {"url": repr(set_default_browser_url)})),
                        ])
                ws["browserUrls"] = entries

            if add_projects or remove_projects:
                current_projects = list(ws.get("projectIds", []))
                seen = set(current_projects)
                for pid in add_projects:
                    if pid not in seen:
                        current_projects.append(pid)
                        seen.add(pid)
                to_remove = set(remove_projects)
                current_projects = [pid for pid in current_projects if pid not in to_remove]
                ws["projectIds"] = current_projects

            if add_patterns or remove_patterns:
                current_patterns = list(ws.get("autoProjectPatterns", []))
                seen_p = set(current_patterns)
                for p in add_patterns:
                    trimmed_p = p.strip()
                    if trimmed_p not in seen_p:
                        current_patterns.append(trimmed_p)
                        seen_p.add(trimmed_p)
                to_remove_p = set(remove_patterns)
                current_patterns = [p for p in current_patterns if p not in to_remove_p]
                ws["autoProjectPatterns"] = current_patterns

            txn.write()
            return WorkspaceMutationResult(True, workspace_id, ws, None)

    result = await sync_to_async(_read_validate_write)()

    if result.success:
        await _broadcast_after_write()
    return result


async def delete_workspace_atomic(workspace_id: str) -> WorkspaceMutationResult:
    """Atomically remove a workspace from the list.

    Projects referenced by the workspace are not affected (they continue
    to exist in DB and in any other workspace listing them).
    """
    def _read_validate_write() -> WorkspaceMutationResult:
        with locked_json_file(get_workspaces_path(), default={}) as txn:
            workspaces = txn.data.setdefault("workspaces", [])
            idx = next((i for i, w in enumerate(workspaces) if w.get("id") == workspace_id), None)
            if idx is None:
                return WorkspaceMutationResult(False, None, None, [
                    WorkspaceMutationError("WORKSPACE_ID", "workspace_not_found",
                                           ui_message_text(f"Workspace {workspace_id!r} not found.", 'backend.workspaces.id_missing', {"id": repr(workspace_id)})),
                ])
            del workspaces[idx]
            txn.write()
            return WorkspaceMutationResult(True, workspace_id, None, None)

    result = await sync_to_async(_read_validate_write)()

    if result.success:
        await _broadcast_after_write()
    return result


# ---------------------------------------------------------------------------
# Broadcast helpers
# ---------------------------------------------------------------------------


async def _broadcast_after_write() -> None:
    """Re-read workspaces.json from disk and broadcast the full list.

    Called by every atomic op after the lock is released. Re-reading on
    the broadcast path keeps the payload aligned with on-disk state even
    if another writer (auto-add, WS whole-blob) slipped in between, so
    UI clients always converge to the same snapshot.
    """
    data = await sync_to_async(read_workspaces)()
    workspaces = data.get("workspaces", [])
    await _broadcast_workspaces_updated(workspaces)


async def _broadcast_workspaces_updated(workspaces: list[dict]) -> None:
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "workspaces_updated", "workspaces": workspaces},
    })
