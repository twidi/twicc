"""JSON formatting of the drop-request final result.

The ``twicc`` CLI speaks JSON by default on every structured command, so
these helpers always emit JSON through :func:`twicc.cli._output.emit_json`.
There is no text / progress mode anymore: a drop-request command stays
silent until it emits a single final JSON object (the watcher's status
payload, a validation error, a rejection, or a timeout). Errors that are
not part of that payload (heartbeat down, bad arguments) stay on stderr
as plain text in the calling command.
"""

from __future__ import annotations

from twicc.cli._output import emit_json


def emit_validation_errors(errors) -> None:
    emit_json({
        "status": "validation_error",
        "errors": [e._asdict() for e in errors],
    })


# Field projection per result kind. The watcher only writes an id field
# when the underlying service Result populated it (see
# ``_RESULT_ID_FIELDS`` in ``drop_requests_watcher.py``), so we
# dispatch by the id field that actually appears in the status payload.
_SESSION_ID_FIELDS = ("session_id", "provider", "project_id")
_WORKSPACE_ID_FIELDS = ("workspace_id",)
_PROJECT_ID_FIELDS = ("project_id",)
_ARTIFACT_BOOKMARK_ID_FIELDS = ("bookmark_id", "session_id", "project_id")
# Peer sends: ``peer_status`` is the remote delivery state the service put in
# ``status_extra`` ("pending" until the remote user resolves the message).
_PEER_SEND_ID_FIELDS = ("message_id", "peer_id", "peer_status")
_SHARE_ID_FIELDS = ("share_id",)
# The ``fetched`` read payload: what ``session <ID> pending-request`` publishes.
_READ_FIELDS = ("session_id", "provider", "agent_state", "pending_requests")

# Present on some results, absent on others; never invented as ``null``.
_OPTIONAL_FIELDS = ("last_line",)


def build_final(outcome, *, request_uuid: str, timeout: int) -> dict:
    """Return the final JSON dict for one drop-request outcome.

    Single source of truth for the per-request result shape. :func:`emit_final`
    prints it (the singular ``update-session`` / ``send-message`` path); the
    ``update-sessions`` batch runner reuses it verbatim so each per-id entry is
    byte-for-byte what the singular command would have emitted.
    """
    if outcome.status in ("created", "sent", "updated", "stopped", "deleted"):
        d = outcome.data
        # Dispatch by which id field is set. ``share_id`` is share-mutation only.
        # ``bookmark_id`` is artifact-bookmark only (and also carries
        # session_id/project_id, so it must be checked first); ``workspace_id``
        # is workspace-only; ``session_id`` is session-only; ``project_id``
        # alone (without ``session_id``) means the result describes a project
        # mutation.
        if "message_id" in d:
            id_fields = _PEER_SEND_ID_FIELDS
        elif "share_id" in d:
            id_fields = _SHARE_ID_FIELDS
        elif "bookmark_id" in d:
            id_fields = _ARTIFACT_BOOKMARK_ID_FIELDS
        elif "workspace_id" in d:
            id_fields = _WORKSPACE_ID_FIELDS
        elif "session_id" in d:
            id_fields = _SESSION_ID_FIELDS
        else:
            id_fields = _PROJECT_ID_FIELDS

        payload = {"status": outcome.status, "request_uuid": request_uuid}
        for field in id_fields:
            payload[field] = d.get(field)
        for field in _OPTIONAL_FIELDS:
            # Copied only when the service produced one, so a command that has
            # no use for it does not grow a null key. ``last_line`` is the
            # transcript cursor ``send-message`` hands to ``--wait-reply``.
            if field in d:
                payload[field] = d[field]
        return payload
    if outcome.status == "fetched":
        # The one read outcome. ``execute_drop_payload`` has already flattened
        # the id fields, the service's ``status_extra`` and the per-status
        # timestamp into one dict, so there is nothing left to merge here — only
        # to project. A whitelist, not a blocklist: ``project_id`` and
        # ``fetched_at`` are real keys of ``outcome.data`` and neither belongs to
        # the published payload.
        d = outcome.data
        payload = {"status": "fetched", "request_uuid": request_uuid}
        for field in _READ_FIELDS:
            payload[field] = d.get(field)
        return payload
    if outcome.status == "rejected":
        d = outcome.data
        return {
            "status": "rejected",
            "errors": d.get("errors", []),
            "request_uuid": request_uuid,
        }
    if outcome.status == "failed":
        d = outcome.data
        return {
            "status": "failed",
            "error": d.get("error"),
            "request_uuid": request_uuid,
        }
    # timeout
    if outcome.received_seen:
        msg = (f"Request was received but server did not respond within "
               f"{timeout}s. Check server logs.")
    else:
        msg = f"No confirmation from server after {timeout}s."
    return {
        "status": "timeout",
        "received_seen": outcome.received_seen,
        "message": msg,
        "request_uuid": request_uuid,
    }


def emit_final(outcome, *, request_uuid: str, timeout: int) -> None:
    emit_json(build_final(outcome, request_uuid=request_uuid, timeout=timeout))
