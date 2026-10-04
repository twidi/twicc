"""Two ready snapshots must enclose the scan that reports an end."""

from django.db import connection
from django.test.utils import CaptureQueriesContext
import pytest

from tests import test_mcp_events_turns
from tests.mcp_events_helpers import append_assistant
from tests.test_mcp_events_turns import opened, stopped_ticks
from twicc.cli import _wait_reply
from twicc.core.enums import ItemKind
from twicc.core.models import Session
from twicc.mcp.events.runtime import EventsRuntime, read_session_snapshot

pytestmark = pytest.mark.django_db
env = test_mcp_events_turns.env


def test_snapshot_uses_one_primary_key_query(env):
    with CaptureQueriesContext(connection) as queries:
        snapshot = read_session_snapshot(env.session.id)
    assert len(queries) == 1
    assert snapshot.ready and snapshot.history_epoch == 0 and snapshot.last_line == 0
    assert '"core_session"."id" = ' in queries[0]["sql"]


def test_unready_end_preserves_turn_wait_and_timer_until_two_ready_reads(env):
    opened(env)
    Session.objects.filter(pk=env.session.pk).update(compute_version=None)
    stopped_ticks(env)
    wait = env.monitor.wait
    timer = wait.stopped_since
    assert env.emissions == env.writes == []
    assert env.monitor.turn_open and not env.monitor.session_snapshot.ready
    Session.objects.filter(pk=env.session.pk).update(compute_version=env.session.compute_version)
    env.tick()
    assert env.emissions == env.writes == []
    assert env.monitor.wait is wait and wait.stopped_since == timer
    env.tick()
    assert len(env.emissions) == 1 and not env.monitor.turn_open


def test_compute_commits_after_unclassified_scan_does_not_emit_false_end(env, monkeypatch):
    opened(env)
    item = append_assistant(env.session, 1)
    item.kind = None
    item.save(update_fields=["kind"])
    Session.objects.filter(pk=env.session.pk).update(compute_version=None)
    env.runtime._read_monitor_session(env.monitor)
    stopped_ticks(env)
    wait = env.monitor.wait
    scan = wait.step

    def classify_after_scan():
        result = scan()
        assert result["outcome"] == "ended"
        item.kind = ItemKind.ASSISTANT_MESSAGE
        item.save(update_fields=["kind"])
        Session.objects.filter(pk=env.session.pk).update(compute_version=env.session.compute_version)
        return result

    monkeypatch.setattr(wait, "step", classify_after_scan)
    env.tick()
    assert env.emissions == env.writes == [] and env.monitor.wait is wait
    monkeypatch.setattr(wait, "step", scan)
    env.tick()
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "replied"


def test_new_raw_line_then_compute_between_ready_reads_blocks_end(env, monkeypatch):
    opened(env)
    wait = env.monitor.wait
    # Reach an end without consuming it through the runtime.
    wait.step()
    env.clock.advance(_wait_reply.AGENT_FLUSH_SECONDS)
    wait.step()
    scan = wait.step

    def append_after_scan():
        result = scan()
        assert result["outcome"] == "ended"
        item = append_assistant(env.session, 3)
        item.kind = None
        item.save(update_fields=["kind"])
        Session.objects.filter(pk=env.session.pk).update(compute_version=None)
        item.kind = ItemKind.ASSISTANT_MESSAGE
        item.save(update_fields=["kind"])
        Session.objects.filter(pk=env.session.pk).update(compute_version=env.session.compute_version)
        return result

    monkeypatch.setattr(wait, "step", append_after_scan)
    env.tick()
    assert env.emissions == env.writes == [] and env.monitor.turn_open
    assert env.monitor.session_snapshot.last_line == 3
    monkeypatch.setattr(wait, "step", scan)
    env.tick()
    assert env.payloads()[0]["data"]["reply"]["outcome"] == "replied"


def test_readiness_survives_wait_replacement_and_turn_change(env):
    from tests.test_mcp_events_turns import agent

    previous = env.monitor.session_snapshot
    wait = env.monitor.wait
    env.clock.advance(1)
    agent(env)
    env.tick()
    assert env.monitor.wait is not wait
    assert env.monitor.session_snapshot is previous and env.monitor.has_session_snapshot


def test_idle_ticks_only_read_snapshot_at_five_second_backstop(env, monkeypatch):
    from twicc.mcp.events import runtime as runtime_module

    calls = []
    read = runtime_module.read_session_snapshot
    monkeypatch.setattr(runtime_module, "read_session_snapshot", lambda identity: calls.append(identity) or read(identity))
    for _ in range(19):
        env.clock.advance(.25)
        env.runtime._tick()
    assert calls == []
    env.clock.advance(.25)
    env.runtime._tick()
    assert calls == [env.session.id]
    env.runtime._tick()
    assert calls == [env.session.id]


def test_no_previous_read_is_unready_but_missing_session_read_is_ready(env):
    assert not EventsRuntime._ready_end(None, None, False)
    assert EventsRuntime._ready_end(None, None, True)
    env.session.delete()
    env.runtime._read_monitor_session(env.monitor)
    opened(env)
    stopped_ticks(env)
    assert len(env.emissions) == 1
    assert env.monitor.numbering == 0 and not env.monitor.pending_rebase
