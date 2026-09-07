"""Process-local creation claims and ephemeral tombstones, without run content.

All mutations run synchronously on the backend event loop. Normal claims live
through admission and the active agent, then disappear. Ephemeral ids remain
readonly until shutdown. Tokens are private Python identity capabilities.
"""

from __future__ import annotations

from typing import NamedTuple

from .exceptions import SendDeliveryError


class Admission(NamedTuple):
    draft_session_id: str
    provider: str
    project_id: str
    owner: object
    ephemeral: bool = True


_known: dict[str, Admission] = {}
_claims: dict[str, Admission] = {}
_pending: dict[str, Admission] = {}
_registered: set[Admission] = set()


def is_known(session_id: str) -> bool:
    return isinstance(session_id, str) and session_id in _known


def is_active_normal(session_id: str) -> bool:
    admission = _claims.get(session_id) if isinstance(session_id, str) else None
    return admission is not None and not admission.ephemeral and admission in _registered


def check_readonly(session_id: str, admission: Admission | None = None, *, creation: bool = False) -> None:
    known = (_known.get(session_id) or _claims.get(session_id)) if isinstance(session_id, str) else None
    if known is None:
        return
    pending = _pending.get(known.draft_session_id) is known
    if known is admission and pending:
        return
    if not known.ephemeral and not pending and not creation:
        return  # An ordinary send to a registered persistent agent remains valid.
    if known.ephemeral:
        raise SendDeliveryError("Ephemeral sessions cannot receive another message.", code="ephemeral_readonly")
    raise SendDeliveryError("A session already owns this creation id.", code="agent_starting")


def reserve(session_id: str, provider: str, project_id: str, *, ephemeral: bool = True) -> Admission:
    check_readonly(session_id, creation=True)
    admission = Admission(session_id, provider, project_id, object(), ephemeral)
    _claims[session_id] = admission
    if ephemeral:
        _known[session_id] = admission
    _pending[session_id] = admission
    return admission


def bind(admission: Admission, canonical_id: str) -> None:
    check_readonly(canonical_id, admission, creation=True)
    _claims[canonical_id] = admission
    if admission.ephemeral:
        _known[canonical_id] = admission


def mark_registered(session_id: str) -> None:
    admission = _claims.get(session_id)
    if admission is not None:
        _registered.add(admission)


def agent_ended(session_id: str) -> None:
    admission = _claims.get(session_id)
    if admission is None:
        return
    _registered.discard(admission)
    if not admission.ephemeral and _pending.get(admission.draft_session_id) is not admission:
        release(admission)


def settle(admission: Admission) -> bool:
    """End admission once; active normal claims and ephemeral ids stay owned."""
    if _pending.get(admission.draft_session_id) is not admission:
        return False
    del _pending[admission.draft_session_id]
    if not admission.ephemeral and admission not in _registered:
        release(admission)
    return True


def release(admission: Admission) -> None:
    """Release a normal claim or a proven persistent-row provisional collision."""
    if _pending.get(admission.draft_session_id) is admission:
        del _pending[admission.draft_session_id]
    _registered.discard(admission)
    for mapping in (_known, _claims):
        for key, value in list(mapping.items()):
            if value is admission:
                del mapping[key]


def pending_snapshot() -> list[dict]:
    return [
        {"draft_session_id": item.draft_session_id, "provider": item.provider, "project_id": item.project_id}
        for item in _pending.values()
        if item.ephemeral
    ]


def clear(provider: str | None = None) -> None:
    for admission in set(_claims.values()) | set(_known.values()):
        if provider is None or admission.provider == provider:
            release(admission)


async def finish(admission: Admission | None, *, failed: bool = False) -> None:
    if admission is None or not settle(admission) or not failed or not admission.ephemeral:
        return
    from channels.layers import get_channel_layer

    layer = get_channel_layer()
    if layer is not None:
        await layer.group_send(
            "updates",
            {
                "type": "broadcast",
                "data": {
                    "type": "ephemeral_admission_failed",
                    "draft_session_id": admission.draft_session_id,
                    "provider": admission.provider,
                    "project_id": admission.project_id,
                    "error": "The ephemeral run could not start.",
                },
            },
        )


def drain_buffers(*session_ids: str) -> None:
    from twicc.pending_agent_settings import pop_pending_agent_settings
    from twicc.pending_session_attributes import pop_pending_session_attributes
    from twicc.pending_titles import pop_pending_title

    for sid in set(session_ids):
        pop_pending_title(sid)
        pop_pending_agent_settings(sid)
        pop_pending_session_attributes(sid)
