"""Deterministic seams for external MCP events tests.

These helpers replace clocks, registry and delivery boundaries. The real
``_SessionWait`` reads real ``SessionItem`` rows; no helper copies its semantics.
"""

import asyncio
from collections import deque
from datetime import UTC, datetime
from threading import Event, Lock
from typing import NamedTuple

import orjson

from twicc.agent.states import AgentInfo, AgentState
from twicc.core.enums import ItemKind, Provider
from twicc.mcp.events import Clock


class FakeClock:
    def __init__(self, epoch=1791097200.0, monotonic=100.0):
        self.epoch_seconds = epoch
        self.monotonic_seconds = monotonic

    def utcnow(self):
        return datetime.fromtimestamp(self.epoch_seconds, UTC)

    def epoch(self):
        return self.epoch_seconds

    def monotonic(self):
        return self.monotonic_seconds

    @property
    def clock(self):
        return Clock(self.utcnow, self.epoch, self.monotonic)

    def advance(self, seconds, *, wall=True, monotonic=True):
        if wall:
            self.epoch_seconds += seconds
        if monotonic:
            self.monotonic_seconds += seconds


class FakeRegistry:
    def __init__(self):
        self.agents = {}

    def get_agent_info(self, session_id):
        return self.agents.get(session_id)

    def get_active_agents(self):
        return list(self.agents.values())

    def set_agent(
        self,
        session_id,
        *,
        state=AgentState.ASSISTANT_TURN,
        previous_state=None,
        at=1791097200.0,
        provider=Provider.CLAUDE_CODE,
        **fields,
    ):
        info = AgentInfo(
            session_id=session_id,
            project_id="events-project",
            provider=provider,
            state=state,
            previous_state=previous_state,
            started_at=at,
            state_changed_at=at,
            last_activity=at,
            **fields,
        )
        self.agents[session_id] = info
        return info

    def remove_agent(self, session_id):
        self.agents.pop(session_id, None)

    def install(self, monkeypatch):
        from twicc.agent import registry

        monkeypatch.setattr(registry, "get_agent_manager_registry", lambda: self)


class TransportResponse(NamedTuple):
    status_code: int
    body: bytes


class FakeTransport:
    """Collect attempts and return controlled results without outbound requests."""

    def __init__(self, responses=()):
        self.responses = deque(responses)
        self.calls = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.entered.set()
        await self.release.wait()
        result = self.responses.popleft() if self.responses else TransportResponse(204, b"")
        if isinstance(result, Exception):
            raise result
        return result


class ControlledVerification:
    """Hold verification across concurrent subscribe writes with one future."""

    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.future = asyncio.get_running_loop().create_future()

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.entered.set()
        return await asyncio.shield(self.future)

    def succeed(self, result=None):
        self.future.set_result(result)

    def fail(self, error):
        self.future.set_exception(error)


class Collector:
    """Collect emissions or writes across threads with an explicit arrival signal."""

    def __init__(self):
        self.items = []
        self.arrived = Event()
        self.lock = Lock()

    def __call__(self, item):
        with self.lock:
            self.items.append(item)
        self.arrived.set()

    def snapshot(self):
        with self.lock:
            return list(self.items)


def assistant_content(provider, text, *, final=True):
    """Provider-native small transcript records, readable by the real detector."""
    if provider == "codex":
        return {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "thread_id": "events-thread",
                "turn_id": "events-turn",
                "item": {
                    "type": "AgentMessage",
                    "id": "events-message",
                    "phase": "final_answer" if final else "commentary",
                    "content": [{"type": "Text", "text": text}],
                },
            },
        }
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "stop_reason": "end_turn" if final else "tool_use",
            "content": [{"type": "text", "text": text}],
        },
    }


def append_assistant(session, line_num, text="Events answer", *, final=True, timestamp=None):
    from twicc.core.models import Session, SessionItem

    item = SessionItem.objects.create(
        session=session,
        line_num=line_num,
        kind=ItemKind.ASSISTANT_MESSAGE,
        content=orjson.dumps(assistant_content(session.provider, text, final=final)).decode(),
        timestamp=timestamp,
    )
    Session.objects.filter(pk=session.pk).update(last_line=line_num)
    return item


def write_transcript(tmp_path, session, records):
    """Create a fully indexed transcript; no watcher or model process runs."""
    from twicc.core.models import Session

    path = tmp_path / f"{session.id}.jsonl"
    body = b"".join(orjson.dumps(record) + b"\n" for record in records)
    path.write_bytes(body)
    Session.objects.filter(pk=session.pk).update(file_path=str(path), last_offset=len(body))
    session.file_path = str(path)
    session.last_offset = len(body)
    return path
