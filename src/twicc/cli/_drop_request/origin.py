"""The origin of THIS CLI process (design 2026-10-14, sections 5-6).

Resolved once per process through the existing PID-ancestry lookup (``resolve_current_session``): a hit
means an agent session runs the CLI (``agent`` / ``cli`` / its session id), no hit means a human
(``user`` / ``cli``). Used only in a real CLI process; the backend never walks its own ancestry.
"""

from __future__ import annotations

import logging

from twicc.request_origin import RequestOrigin, agent_cli_origin, human_cli_origin

logger = logging.getLogger(__name__)

_cached: RequestOrigin | None = None


def reset_cli_origin_cache() -> None:
    """Forget the resolved origin (tests only: a real process resolves once)."""
    global _cached
    _cached = None


def cli_origin() -> RequestOrigin:
    """The origin of this CLI process, resolved on first use. Never raises."""
    global _cached
    if _cached is None:
        _cached = _resolve()
    return _cached


def _resolve() -> RequestOrigin:
    try:
        import django
        from django.apps import apps

        if not apps.ready:
            django.setup()
        from twicc.cli._drop_request.whoami import resolve_current_session

        session = resolve_current_session()
    except Exception:
        logger.debug("CLI origin: the caller lookup failed; recorded as a human call", exc_info=True)
        return human_cli_origin()
    return human_cli_origin() if session is None else agent_cli_origin(session.id)
