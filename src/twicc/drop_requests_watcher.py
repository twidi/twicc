"""Watcher for CLI-dropped request files.

Watches ``<data_dir>/drop-requests/`` for new ``<request_uuid>.json`` files
dropped by the TwiCC CLI (``create-session``, ``send-message``,
``send-messages``, ``update-session``, ``update-sessions``, ``process stop``,
...). Reads the ``payload.kind``,
dispatches to the matching service in ``twicc.core.services.*``, and
writes a ``<request_uuid>.status.json`` file the CLI polls. Cleanup of
both files is the CLI's responsibility in the nominal case; this watcher
only handles dead-letter cleanup at boot (see spec §5.5).
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
from pathlib import Path

from datetime import datetime, UTC

import orjson
from watchfiles import Change, awatch

from twicc.paths import get_drop_requests_dir


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()

logger = logging.getLogger(__name__)

DROP_SUFFIX = ".json"
STATUS_SUFFIX = ".status.json"
TMP_SUFFIX = ".tmp"


# kind → (module_path, attr_name, success_status)
#
# Lazy by design: the watcher boots before Django is fully wired, and
# every service module pulls in Django models / channel layer. Importing
# them inside the dispatch keeps startup fast and avoids cycles.
_KIND_HANDLERS: dict[str, tuple[str, str, str]] = {
    "session:create": (
        "twicc.core.services.session_creation",
        "create_session_from_payload",
        "created",
    ),
    "session:send_message": (
        "twicc.core.services.send_message",
        "send_message_to_session_from_payload",
        "sent",
    ),
    "session:update_settings": (
        "twicc.core.services.session_update",
        "update_session_settings_from_payload",
        "updated",
    ),
    "session:update_title": (
        "twicc.core.services.session_update",
        "update_session_title_from_payload",
        "updated",
    ),
    "session:update_archived": (
        "twicc.core.services.session_update",
        "update_session_archived_from_payload",
        "updated",
    ),
    "session:update_pinned": (
        "twicc.core.services.session_update",
        "update_session_pinned_from_payload",
        "updated",
    ),
    "session:update_mute_on_user_turn": (
        "twicc.core.services.session_update",
        "update_session_mute_on_user_turn_from_payload",
        "updated",
    ),
    "session:update_hidden": (
        "twicc.core.services.session_update",
        "update_session_hidden_from_payload",
        "updated",
    ),
    "session:update_annotations": (
        "twicc.core.services.session_update",
        "update_session_annotations_from_payload",
        "updated",
    ),
    "session:pending_requests": (
        "twicc.core.services.pending_question",
        "read_pending_requests_from_payload",
        "fetched",
    ),
    "session:answer_pending_question": (
        "twicc.core.services.pending_question",
        "answer_pending_question_from_payload",
        "updated",
    ),
    "process:stop": (
        "twicc.core.services.process_kill",
        "kill_session_process_from_payload",
        "stopped",
    ),
    "workspace:create": (
        "twicc.core.services.workspace_mutation",
        "create_workspace_from_payload",
        "created",
    ),
    "workspace:update": (
        "twicc.core.services.workspace_mutation",
        "update_workspace_from_payload",
        "updated",
    ),
    "workspace:delete": (
        "twicc.core.services.workspace_mutation",
        "delete_workspace_from_payload",
        "deleted",
    ),
    "project:create": (
        "twicc.core.services.project_mutation",
        "create_project_from_payload",
        "created",
    ),
    "project:update": (
        "twicc.core.services.project_mutation",
        "update_project_from_payload",
        "updated",
    ),
    "project:update_settings": (
        "twicc.core.services.project_mutation",
        "update_project_agent_settings_from_payload",
        "updated",
    ),
    "project:trust": (
        "twicc.core.services.project_mutation",
        "decide_project_trust_from_payload",
        "updated",
    ),
    "artifact_bookmark:upsert": (
        "twicc.core.services.artifact_bookmark_mutation",
        "upsert_artifact_bookmark_from_payload",
        "updated",
    ),
    "artifact_bookmark:delete": (
        "twicc.core.services.artifact_bookmark_mutation",
        "delete_artifact_bookmark_from_payload",
        "deleted",
    ),
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
    "settings:update": (
        "twicc.core.services.settings_mutation",
        "update_synced_settings_from_payload",
        "updated",
    ),
    "settings:notification_test": (
        "twicc.core.services.settings_mutation",
        "notification_test_from_payload",
        "updated",
    ),
    "peer:send": (
        "twicc.core.services.peer_messages",
        "send_peer_message_from_payload",
        "sent",
    ),
}


# Identifier fields the watcher copies from a service Result onto the
# status payload (when present and non-None). Session-flavoured results
# expose ``session_id``/``provider``/``project_id``; workspace-flavoured
# results expose ``workspace_id``. Adding a new entry is enough to
# support a new family of services.
_RESULT_ID_FIELDS: tuple[str, ...] = (
    "session_id",
    "provider",
    "project_id",
    "workspace_id",
    "bookmark_id",
    "share_id",
    # Peer sends. The remote delivery state travels via
    # ``status_extra={"peer_status": ...}`` — NEVER add "status" here (it
    # would clobber the transport status and break the CLI exit mapping).
    "message_id",
    "peer_id",
)


# Per-status timestamp field. The CLI relies on these for its wording.
_STATUS_TIME_FIELDS: dict[str, str] = {
    "received": "received_at",
    "created": "created_at",
    "sent": "sent_at",
    "updated": "updated_at",
    "stopped": "stopped_at",
    "deleted": "deleted_at",
    "fetched": "fetched_at",
    "rejected": "rejected_at",
    "failed": "failed_at",
}


def stamp_status_times(data: dict) -> dict:
    """Stamp the per-status timestamp field (same contract as the status file)."""
    field = _STATUS_TIME_FIELDS.get(data.get("status"))
    if field:
        data.setdefault(field, _iso_now())
    return data


async def execute_drop_payload(payload: dict, kind: str | None) -> dict:
    """Route ``payload`` to its kind service and return the final status dict.

    Exactly the dict the watcher would persist as ``<uuid>.status.json``
    (timestamps included, ``request_uuid`` NOT included — the transport layer
    owns that key). Never raises: service exceptions become ``failed``.
    """
    handler = _KIND_HANDLERS.get(kind) if kind else None
    if handler is None:
        return stamp_status_times({"status": "failed", "error": f"Unknown payload kind: {kind!r}"})

    module_path, attr_name, success_status = handler
    service = getattr(importlib.import_module(module_path), attr_name)
    try:
        result = await service(payload)
    except Exception as e:  # noqa: BLE001 — mirror the watcher's catch-all
        logger.exception("[drop-request] service raised for kind %s", kind)
        return stamp_status_times({"status": "failed", "error": f"{type(e).__name__}: {e}"})

    if result.success:
        status_data: dict = {"status": success_status}
        for attr in _RESULT_ID_FIELDS:
            value = getattr(result, attr, None)
            if value is not None:
                status_data[attr] = value
        extra = getattr(result, "status_extra", None)
        if isinstance(extra, dict):
            status_data.update(extra)
        return stamp_status_times(status_data)
    return stamp_status_times({
        "status": "rejected",
        "errors": [e._asdict() for e in (result.errors or [])],
    })


class DropRequestsWatcher:
    def __init__(self) -> None:
        self.directory = get_drop_requests_dir()
        self._in_flight: set[str] = set()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Start awatch FIRST to avoid missing files dropped during the boot scan
        watch_task = asyncio.ensure_future(self._watch_loop())
        await self._scan_existing()
        await self._cleanup_orphan_status_files()
        try:
            await watch_task
        except asyncio.CancelledError:  # noqa: TRY203 — explicit: cancellation propagates
            raise

    async def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Watch loop
    # ------------------------------------------------------------------
    async def _watch_loop(self) -> None:
        async for changes in awatch(self.directory, stop_event=self._stop):
            for change_type, raw_path in changes:
                p = Path(raw_path)
                if change_type != Change.added:
                    continue
                if p.name.endswith(STATUS_SUFFIX) or p.name.endswith(TMP_SUFFIX):
                    continue
                if p.suffix != DROP_SUFFIX:
                    continue
                if p.stem in self._in_flight:
                    continue
                asyncio.ensure_future(self._process_file(p))

    # ------------------------------------------------------------------
    # Boot scan
    # ------------------------------------------------------------------
    async def _scan_existing(self) -> None:
        for p in sorted(self.directory.glob(f"*{DROP_SUFFIX}")):
            if p.name.endswith(STATUS_SUFFIX) or p.name.endswith(TMP_SUFFIX):
                continue
            if p.stem in self._in_flight:
                continue
            status_path = self.directory / f"{p.stem}{STATUS_SUFFIX}"
            if status_path.exists():
                # CLI crashed before deleting both files. Request already
                # processed (created / sent / updated / rejected / failed) —
                # just clean up.
                logger.info("[DropRequestsWatcher] boot cleanup drop+status %s", p.stem)
                p.unlink(missing_ok=True)
                status_path.unlink(missing_ok=True)
            else:
                # Drop file orphaned by a server restart — process normally, no
                # timing check (cf. spec §5.5).
                logger.info("[DropRequestsWatcher] boot processes drop %s", p.stem)
                asyncio.ensure_future(self._process_file(p))

    async def _cleanup_orphan_status_files(self) -> None:
        for p in sorted(self.directory.glob(f"*{STATUS_SUFFIX}")):
            request_uuid = p.name[:-len(STATUS_SUFFIX)]
            drop_path = self.directory / f"{request_uuid}{DROP_SUFFIX}"
            if not drop_path.exists():
                logger.info("[DropRequestsWatcher] boot cleanup orphan status %s", request_uuid)
                p.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Per-file processing
    # ------------------------------------------------------------------
    async def _process_file(self, path: Path) -> None:
        request_uuid = path.stem
        self._in_flight.add(request_uuid)
        try:
            try:
                content = await asyncio.to_thread(path.read_bytes)
                data = await asyncio.to_thread(orjson.loads, content)
            except Exception as e:
                logger.exception("[DropRequestsWatcher] parse failed for %s", request_uuid)
                await self._write_status(request_uuid, {
                    "status": "failed",
                    "error": f"Could not parse drop-file: {e}",
                })
                return  # CLI will delete both drop + status files

            await self._write_status(request_uuid, {"status": "received"})
            logger.info("[DropRequestsWatcher] received %s", request_uuid)

            payload = data.get("payload") or {}
            kind = payload.get("kind")

            status_data = await execute_drop_payload(payload, kind)
            if status_data["status"] == "failed":
                logger.warning("[DropRequestsWatcher] failed %s: %s",
                               request_uuid, status_data.get("error"))
            elif status_data["status"] == "rejected":
                logger.warning("[DropRequestsWatcher] rejected %s: %s",
                               request_uuid, status_data.get("errors"))
            else:
                log_id = (
                    status_data.get("session_id")
                    or status_data.get("workspace_id")
                    or "?"
                )
                logger.info("[DropRequestsWatcher] %s %s -> %s",
                            status_data["status"], request_uuid, log_id)
            await self._write_status(request_uuid, status_data)
            # No unlink here: the CLI cleans up its own drop-file once it
            # observes the final status. The watcher only deletes during the
            # boot scan, for files left behind by a crashed CLI
            # (cf. _scan_existing).
        finally:
            self._in_flight.discard(request_uuid)

    # ------------------------------------------------------------------
    # Status file writer (atomic via tmp + rename)
    # ------------------------------------------------------------------
    async def _write_status(self, request_uuid: str, data: dict) -> None:
        # Merge timestamps. The CLI relies on them for the wording. Idempotent
        # via setdefault: execute_drop_payload already stamps its own results.
        data.setdefault("request_uuid", request_uuid)
        stamp_status_times(data)

        path = self.directory / f"{request_uuid}{STATUS_SUFFIX}"
        tmp = path.with_suffix(path.suffix + TMP_SUFFIX)
        await asyncio.to_thread(tmp.write_bytes, orjson.dumps(data))
        await asyncio.to_thread(os.replace, tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass


_watcher_instance: DropRequestsWatcher | None = None


def get_drop_requests_watcher() -> DropRequestsWatcher:
    global _watcher_instance
    if _watcher_instance is None:
        _watcher_instance = DropRequestsWatcher()
    return _watcher_instance
