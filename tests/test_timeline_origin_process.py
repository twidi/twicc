"""Process events: the stop origin is paired with the stop reason (design section 7; verification 8, 8a, 8d, 8e, 8f, 10)."""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.utils import timezone

from tests.timeline_helpers import columns, run_with_writer
from twicc.agent.base_agent import BaseAgent
from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.stop_origin import StopOriginMixin
from twicc.agent.states import DELIBERATE_STOP_REASONS, AgentState
from twicc.agent.timeline_hooks import (
    ProcessSnapshot, record_process_changes, record_process_ended, record_process_started,
)
from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Project, TimelineEvent
from twicc.providers.helpers import AgentSettings
from twicc.request_origin import (
    DEFAULT_ORIGIN, UI_ORIGIN, agent_cli_origin, mcp_session_origin, origin_scope, system_marker,
)

A = mcp_session_origin("sess-A")
B = agent_cli_origin("sess-B")
SYSTEM_COLUMNS = ("system", None, None, None)


class _Agent(BaseAgent):
    """A minimal concrete agent: the pairing lives on the base class."""

    provider = Provider.CODEX

    async def start(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def send_message(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def stop(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def new_agent():
    return _Agent(session_id="s1", project_id="-p", cwd="/tmp", agent_settings=AgentSettings())


# --- The pairing of reason and origin (no database) ---------------------------------------------------------


def test_a_new_agent_has_no_reason_and_no_origin():
    agent = new_agent()
    assert (agent.kill_reason, agent.stop_origin) == (None, None)


def test_a_new_agent_never_inherits_the_origin_of_a_previous_one():
    old = new_agent()
    with origin_scope(A):
        old.kill_reason = "manual"
    assert new_agent().stop_origin is None


@pytest.mark.parametrize("reason", sorted(DELIBERATE_STOP_REASONS))
def test_a_deliberate_reason_captures_the_carrier_at_assignment(reason):
    agent = new_agent()
    with origin_scope(A):
        agent.kill_reason = reason
    assert (agent.kill_reason, agent.stop_origin) == (reason, A)


def test_a_deliberate_reason_with_an_empty_carrier_captures_the_default():
    agent = new_agent()
    agent.kill_reason = "manual"
    assert agent.stop_origin == DEFAULT_ORIGIN == UI_ORIGIN


@pytest.mark.parametrize("reason", [
    "error", "shutdown", "apply-settings", "switch-hybrid", "cron_restart_timeout", "startup-failed",
    "auth_required", "cli-exit", "ephemeral-done", None,
])
def test_a_non_deliberate_reason_has_no_origin_even_under_a_carrier(reason):
    agent = new_agent()
    with origin_scope(A):
        agent.kill_reason = reason
    assert (agent.kill_reason, agent.stop_origin) == (reason, None)


@pytest.mark.parametrize(("first", "second"), [("manual", "force"), ("manual", "archived"), ("force", "archived")])
def test_overlapping_stops_keep_reason_and_origin_of_the_same_request(first, second):
    agent = new_agent()
    with origin_scope(A):
        agent.kill_reason = first
    with origin_scope(B):
        agent.kill_reason = second  # last writer wins, both fields together
    assert (agent.kill_reason, agent.stop_origin) == (second, B)


@pytest.mark.parametrize("later", ["error", "shutdown", "cron_restart_timeout", "apply-settings"])
def test_a_later_non_request_reason_clears_the_origin(later):
    agent = new_agent()
    with origin_scope(A):
        agent.kill_reason = "manual"
    agent.kill_reason = later
    assert agent.stop_origin is None


def test_agents_built_without_init_still_read_a_reason():
    agent = object.__new__(_Agent)
    assert (agent.kill_reason, agent.stop_origin) == (None, None)


def test_a_deliberate_reason_assigned_from_a_stale_context_captures_that_stale_context():
    # Why no agent task may re-assign a deliberate reason it merely read back: the setter captures the
    # context it runs in, here the one of the request that STARTED the agent. The hybrid liveness
    # fallback used to do exactly that.
    agent = new_agent()

    async def scenario():
        with origin_scope(A):  # the starting request: the agent's task copies this context
            async def agents_own_task():
                await asyncio.sleep(0)
                agent.kill_reason = agent.kill_reason

            task = asyncio.create_task(agents_own_task())
        with origin_scope(B):
            agent.kill_reason = "manual"
        await task

    asyncio.run(scenario())
    assert agent.stop_origin == A


def test_hybrid_liveness_never_reassigns_the_stop_reason():
    from twicc.providers.claude_code.agent.hybrid.agent import HybridClaudeAgent

    source = inspect.getsource(HybridClaudeAgent)
    assert "self.kill_reason = self.kill_reason" not in source
    assert 'if self.kill_reason is None:' in source


# --- The hooks (database) -----------------------------------------------------------------------------------

pytestmark_db = pytest.mark.django_db(transaction=True)


class _Manager(BaseAgentManager):
    provider = Provider.CODEX


class FakeAgent(StopOriginMixin):
    """The attributes the timeline hooks read, with the real reason/origin pairing."""

    provider = Provider.CODEX

    def __init__(self, project, *, with_run=True):
        now = timezone.now()
        self.session_id = "s1"
        self.project_id = project.id
        self.ephemeral = False
        self.pending_requests = ()
        self.error = None
        self.is_hybrid = False
        self.process_run = (
            ProcessRun.objects.create(provider="codex", session_id="s1", started_at=now, last_state_change_at=now)
            if with_run else None
        )
        self.kill_reason = None

    def get_pid(self):
        return 123

    def background_work_snapshot(self):
        return None


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-origin-proc", directory="/tmp/tl-origin-proc")


def rows(*kinds_):
    qs = TimelineEvent.objects.filter(session_id="s1").order_by("id")
    return list(qs.filter(kind__in=kinds_) if kinds_ else qs)


def die(agent):
    run_with_writer(lambda: _Manager()._persist_process_run_transition(agent, AgentState.DEAD))


@pytestmark_db
def test_process_started_takes_the_origin_of_the_starting_request(project):
    with origin_scope(A):
        record_process_started(FakeAgent(project), resume=False)
    assert columns(rows("process.started")[0]) == ("agent", "mcp_internal", "sess-A", None)


@pytestmark_db
def test_process_started_without_any_origin_is_the_default(project):
    record_process_started(FakeAgent(project), resume=False)
    assert columns(rows("process.started")[0]) == ("user", "ui", None, None)


@pytestmark_db
def test_an_automatic_process_started_is_system_never_the_default(project):
    with system_marker():
        record_process_started(FakeAgent(project), resume=True)
    assert columns(rows("process.started")[0]) == SYSTEM_COLUMNS


@pytestmark_db
def test_a_stop_asked_by_b_on_a_session_started_by_a_records_b_from_the_agents_own_task(project):
    agent = FakeAgent(project)
    manager = _Manager()

    async def scenario():
        release = asyncio.Event()
        with origin_scope(A):  # the request that started the session: the agent's task keeps this context
            async def agents_own_task():
                await release.wait()
                await manager._persist_process_run_transition(agent, AgentState.DEAD)

            task = asyncio.create_task(agents_own_task())
        with origin_scope(B):  # the request that asks for the stop
            agent.kill_reason = "manual"
        release.set()
        await task

    run_with_writer(scenario)
    assert columns(rows("process.ended")[0]) == ("agent", "cli", "sess-B", None)


@pytestmark_db
def test_a_stop_from_an_old_cli_with_no_origin_records_the_default_not_the_starter(project):
    agent = FakeAgent(project)
    manager = _Manager()

    async def scenario():
        release = asyncio.Event()
        with origin_scope(A):
            async def agents_own_task():
                await release.wait()
                await manager._persist_process_run_transition(agent, AgentState.DEAD)

            task = asyncio.create_task(agents_own_task())
        with origin_scope(None):  # a drop file with no origin
            agent.kill_reason = "manual"
        release.set()
        await task

    run_with_writer(scenario)
    assert columns(rows("process.ended")[0]) == ("user", "ui", None, None)


@pytestmark_db
def test_an_error_that_overrides_a_pending_stop_reason_makes_the_end_system(project):
    agent = FakeAgent(project)
    with origin_scope(A):
        agent.kill_reason = "manual"
    agent.kill_reason = "error"
    die(agent)
    assert columns(rows("process.ended")[0]) == SYSTEM_COLUMNS


@pytestmark_db
@pytest.mark.parametrize("reason", ["shutdown", "apply-settings", "switch-hybrid", "error", "cron_restart_timeout"])
def test_every_automatic_end_is_system_with_null_fields_whatever_the_carrier(project, reason):
    agent = FakeAgent(project)
    with origin_scope(A):
        agent.kill_reason = reason
        die(agent)
    assert columns(rows("process.ended")[0]) == SYSTEM_COLUMNS


@pytestmark_db
def test_inner_rows_closed_with_the_end_share_its_actor_via_ref_and_name(project):
    agent = FakeAgent(project)
    with origin_scope(B):
        agent.kill_reason = "archived"
    record_process_ended(agent, ProcessSnapshot("assistant_turn", True, None))
    got = rows()
    assert [r.kind for r in got] == ["turn.ended", "awaiting_input.ended", "process.ended"]
    assert {columns(r) for r in got} == {("agent", "cli", "sess-B", None)}


@pytestmark_db
def test_inner_rows_of_an_automatic_end_are_system_too(project):
    agent = FakeAgent(project)
    agent.kill_reason = "shutdown"
    record_process_ended(agent, ProcessSnapshot("assistant_turn", True, None))
    assert {columns(r) for r in rows()} == {SYSTEM_COLUMNS}


@pytestmark_db
def test_an_ended_row_does_not_inherit_the_origin_of_its_started_row(project):
    agent = FakeAgent(project)
    with origin_scope(A):
        record_process_started(agent, resume=False)
        agent.kill_reason = "manual"
    with origin_scope(B):
        agent.kill_reason = "manual"
    record_process_ended(agent, ProcessSnapshot("user_turn", False, None))
    started, ended = rows("process.started", "process.ended")
    assert columns(started) != columns(ended) and columns(ended) == ("agent", "cli", "sess-B", None)


@pytestmark_db
def test_standalone_process_rows_keep_actor_agent_with_null_fields(project):
    agent = FakeAgent(project)
    with origin_scope(A):
        record_process_changes(
            agent, ProcessSnapshot("user_turn", False, None), new_state="assistant_turn", new_awaiting=False,
            new_work=None,
        )
        agent.pending_requests = (SimpleNamespace(request_type="question", tool_name="Ask"),)
        record_process_changes(
            agent, ProcessSnapshot("assistant_turn", False, None), new_state="assistant_turn", new_awaiting=True,
            new_work=None,
        )
        agent.pending_requests = ()
        record_process_changes(  # a natural end: the user answered the request, it is NOT attributed to them
            agent, ProcessSnapshot("assistant_turn", True, None), new_state="assistant_turn", new_awaiting=False,
            new_work=None,
        )
    got = rows()
    assert [r.kind for r in got] == ["turn.started", "awaiting_input.started", "awaiting_input.ended"]
    assert {columns(r) for r in got} == {("agent", None, None, None)}


@pytestmark_db
def test_a_hybrid_switch_kill_is_system_and_the_next_start_takes_the_send_origin(project):
    first = FakeAgent(project)
    with origin_scope(UI_ORIGIN):  # the composer's switch request
        first.kill_reason = "switch-hybrid"
    die(first)
    assert columns(rows("process.ended")[0]) == SYSTEM_COLUMNS
    second = FakeAgent(project, with_run=False)  # a new agent for the same session
    with origin_scope(B):  # the send of an agent through the CLI
        record_process_started(second, resume=True)
    assert columns(rows("process.started")[0]) == ("agent", "cli", "sess-B", None)


@pytestmark_db
def test_an_idle_hybrid_switch_has_no_kill_and_the_start_takes_its_send_origin(project):
    with origin_scope(A):
        record_process_started(FakeAgent(project, with_run=False), resume=True)
    assert [r.kind for r in rows()] == ["process.started"]
    assert columns(rows()[0]) == ("agent", "mcp_internal", "sess-A", None)


@pytestmark_db
def test_hook_fakes_without_a_stop_origin_still_work(project):
    # SimpleNamespace agents (older tests) have a kill_reason but no stop_origin attribute.
    now = timezone.now()
    run = ProcessRun.objects.create(provider="codex", session_id="s1", started_at=now, last_state_change_at=now)
    agent = SimpleNamespace(
        session_id="s1", project_id=project.id, provider=Provider.CODEX, ephemeral=False, process_run=run,
        pending_requests=(), kill_reason="manual", error=None, is_hybrid=False, get_pid=lambda: 1,
        background_work_snapshot=lambda: None, start=AsyncMock(),
    )
    die(agent)
    assert columns(rows("process.ended")[0]) == ("user", "ui", None, None)
