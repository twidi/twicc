"""Bounded, process-local records of automatic title provider pushes."""

import time
from typing import NamedTuple


AUTOMATIC_TITLE_ECHO_TTL_SECONDS = 600


class _AutomaticTitleEcho(NamedTuple):
    title: str
    recorded_at: float


_automatic_title_echoes: dict[str, _AutomaticTitleEcho] = {}


def _prune_expired_echoes(now: float) -> None:
    expired = [
        session_id
        for session_id, record in _automatic_title_echoes.items()
        if now - record.recorded_at >= AUTOMATIC_TITLE_ECHO_TTL_SECONDS
    ]
    for session_id in expired:
        _automatic_title_echoes.pop(session_id, None)


def record_automatic_title_push(session_id: str, title: str) -> None:
    """Record the latest automatic push before its provider write starts."""
    now = time.monotonic()
    _prune_expired_echoes(now)
    _automatic_title_echoes[session_id] = _AutomaticTitleEcho(title, now)


def should_skip_automatic_title_echo(
    session_id: str,
    provider_title: str,
    *,
    title: str | None,
    title_origin: str,
) -> bool:
    """Consume a matching echo; skip it only when it would replace a user title."""
    _prune_expired_echoes(time.monotonic())
    record = _automatic_title_echoes.get(session_id)
    if record is None or record.title != provider_title:
        return False
    _automatic_title_echoes.pop(session_id)
    return title_origin == "user" and title != provider_title
