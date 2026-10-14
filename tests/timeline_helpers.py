# tests/timeline_helpers.py
"""Shared helpers of the timeline tests."""

import asyncio
from types import SimpleNamespace

import pytest

from twicc.core.models import TimelineEvent
from twicc.providers import db_writer
from twicc.request_origin import (
    SYSTEM_ORIGIN, UI_ORIGIN, agent_cli_origin, current_origin, human_cli_origin, mcp_external_origin,
    mcp_session_origin, mcp_token_origin, rpc_origin,
)


def run_with_writer(factory):
    """Run ``await factory()`` with the DB writer (and its write lock) started."""

    async def runner():
        db_writer.start_db_writer()
        try:
            return await factory()
        finally:
            await db_writer.stop_db_writer()

    return asyncio.run(runner())


def kinds(session_id):
    """Kinds recorded for ``session_id``, in order."""
    return list(
        TimelineEvent.objects.filter(session_id=session_id).order_by("id").values_list("kind", flat=True)
    )


#: The mapping table of the spec (section 4): (origin built by the entry path, expected columns).
ORIGIN_MAPPING = [
    pytest.param(UI_ORIGIN, ("user", "ui", None, None), id="web-ui"),
    pytest.param(human_cli_origin(), ("user", "cli", None, None), id="cli-human"),
    pytest.param(agent_cli_origin("sess-A"), ("agent", "cli", "sess-A", None), id="cli-agent"),
    pytest.param(mcp_session_origin("sess-A"), ("agent", "mcp_internal", "sess-A", None), id="mcp-internal-session"),
    pytest.param(mcp_token_origin("tok_1"), ("user", "mcp_internal", "tok_1", None), id="mcp-internal-token"),
    pytest.param(
        mcp_external_origin("conn-1", "ChatGPT"), ("external", "mcp_external", "conn-1", "ChatGPT"),
        id="mcp-external",
    ),
    pytest.param(rpc_origin("tok_1"), ("user", "rpc", "tok_1", None), id="rpc-token"),
    pytest.param(rpc_origin(None), ("user", "rpc", None, None), id="rpc-no-token"),
    pytest.param(SYSTEM_ORIGIN, ("system", None, None, None), id="automatic"),
]


def columns(row):
    """The four origin columns of an event row, in the order of ``RequestOrigin``."""
    return (row.actor, row.via, row.actor_ref, row.actor_name)


#: ``(payload, origin)`` pairs recorded by :func:`recording_service`.
RECORDED: list = []


async def recording_service(payload):
    """A drop-kind handler that records the payload it receives and the origin it runs under."""
    RECORDED.append((dict(payload), current_origin()))
    return SimpleNamespace(success=True, errors=None)
