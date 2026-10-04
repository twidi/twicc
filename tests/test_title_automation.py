"""Automatic checks preserve user choices and complete provider writeback."""

import asyncio
from datetime import datetime, timedelta, timezone
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

from asgiref.sync import async_to_sync
import orjson
import pytest

from twicc import pending_titles, title_echo
from twicc.agent.states import AgentState
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import Project, Session, SessionItem, SessionType
from twicc.providers import db_writer
from twicc.providers.helpers import get_provider_helpers, TitleValidationResult
from twicc.providers.hermetic import HermeticGuardViolation
from twicc.title_cadence import TitleCheckState

pytestmark = pytest.mark.django_db(transaction=True)
NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
OLD = NOW - timedelta(minutes=15)


@pytest.fixture
def env(monkeypatch):
    module = import_module("twicc.core.services.title_automation")
    monkeypatch.setattr(db_writer, "_db_write_lock", asyncio.Lock())
    monkeypatch.setattr(db_writer, "_db_writer_stop_event", asyncio.Event())
    monkeypatch.setattr(pending_titles, "_pending", {})
    monkeypatch.setattr(title_echo, "_automatic_title_echoes", {})
    settings = {"titleGenerationEnabled": True, "titleAutoApply": True,
                "titleSystemPrompt": "Summarize {text}", "titleSuggestionModel": "provider"}
    state = SimpleNamespace(
        module=module, settings=settings, messages=["Subject"] * 7, output="Session titles and provider sync",
        calls=[], effects=[], pushes=[], agents={}, running=True, clock=NOW,
        generation_hook=None, rename_hook=None,
    )
    real_helpers = get_provider_helpers(Provider.CLAUDE_CODE)
    state.read = Mock(side_effect=lambda session_id: list(state.messages))
    state.validate = Mock(side_effect=real_helpers.validate_title)

    async def generate(source, system_prompt, *, current_title=None):
        assert not db_writer.get_db_write_lock().locked()
        state.calls.append((source, system_prompt, current_title))
        if state.generation_hook:
            await state.generation_hook()
        if isinstance(state.output, Exception):
            raise state.output
        return state.output

    async def rename(session_id, title):
        assert not db_writer.get_db_write_lock().locked()
        assert title_echo._automatic_title_echoes[session_id].title == state.output.strip()
        state.pushes.append((session_id, title))
        state.effects.append("rename")
        if state.rename_hook:
            await state.rename_hook(len(state.pushes))

    state.helpers = SimpleNamespace(get_title_messages=state.read, generate_title=generate,
                                    validate_title=state.validate, rename_session=rename)
    def session_helpers(provider):
        assert isinstance(provider, Provider)
        return state.helpers

    monkeypatch.setattr(module, "get_provider_helpers", session_helpers)
    # Exercise the real suggestion router, replacing only its provider boundaries.
    monkeypatch.setattr("twicc.core.services.title_suggestion.get_provider_helpers", lambda provider: state.helpers)
    monkeypatch.setattr("twicc.core.services.title_suggestion.is_provider_running", lambda provider: state.running)
    manager = SimpleNamespace(_agents=state.agents)
    registry = SimpleNamespace(get=lambda provider: manager)
    monkeypatch.setattr(module, "get_agent_manager_registry", lambda: registry)
    monkeypatch.setattr(module, "read_synced_settings", lambda: settings)
    monkeypatch.setattr(module.timezone, "now", lambda: state.clock)

    async def broadcast(session_id):
        assert not db_writer.get_db_write_lock().locked()
        state.effects.append("broadcast")

    def reindex(session_id):
        state.effects.append("reindex")

    monkeypatch.setattr(module, "broadcast_session_updated", broadcast)
    monkeypatch.setattr(module, "request_session_reindex", reindex)
    return state


@pytest.fixture
def session():
    return Session.objects.create(
        id="automatic-title", project=Project.objects.create(id="automation-project"),
        provider="claude_code", title="Current", title_origin="auto",
        user_message_count=7, title_check_count=1, title_checked_at=OLD,
    )


def run(env, session, **kwargs):
    async_to_sync(env.module.check_session_title)(session.id, **kwargs)
    session.refresh_from_db()


def assert_untouched(env, session, count=1, at=OLD):
    assert session.title_check_count == count
    assert session.title_checked_at == at
    assert env.effects == []
    assert env.pushes == []


@pytest.mark.parametrize("fields", [
    {"type": SessionType.SUBAGENT}, {"type": SessionType.SUBAGENT, "id": "workflow:agent"},
    {"archived": True}, {"hidden": True}, {"stale": True}, {"title_origin": ""}, {"title_origin": "user"},
])
def test_ineligible_rows_skip_reads_and_all_writes(env, session, fields):
    # Workflow agents share the SUBAGENT type gate.
    Session.objects.filter(pk=session.pk).update(**fields)
    if "id" in fields:
        session.pk = fields["id"]
    run(env, session)
    assert_untouched(env, session)
    env.read.assert_not_called()
    assert env.calls == []


@pytest.mark.parametrize("key", ["titleGenerationEnabled", "titleAutoApply"])
def test_each_disabled_setting_skips_work(env, session, key):
    env.settings[key] = False
    run(env, session)
    assert_untouched(env, session)
    env.read.assert_not_called()


def test_missing_row_skips_work(env):
    async_to_sync(env.module.check_session_title)("missing")
    env.read.assert_not_called()
    assert env.calls == env.effects == []


def test_pending_title_skips_work(env, session):
    pending_titles.set_pending_title(session.id, "Chosen")
    run(env, session)
    assert_untouched(env, session)
    env.read.assert_not_called()


@pytest.mark.parametrize("count,total,at,closing,reads,generated", [
    (1, 6, OLD, False, False, False), (1, 7, NOW, False, False, False),
    (1, 7, OLD, False, True, True), (None, 0, None, False, False, False),
    (None, 1, None, False, True, True), (None, 1, NOW, False, False, False),
    (None, 1, OLD, False, True, True), (1, 3, NOW, True, False, False),
    (1, 4, NOW, True, True, True), (None, 2, None, True, False, False),
    (None, 3, None, True, True, True), (8, 3, NOW, False, True, False),
    (8, 3, NOW, True, True, False),
])
def test_pre_gate_and_exact_cadence(env, session, count, total, at, closing, reads, generated):
    Session.objects.filter(pk=session.pk).update(
        title_check_count=count, user_message_count=total, title_checked_at=at)
    env.messages = ["Subject"] * total
    run(env, session, closing=closing)
    assert bool(env.read.call_count) is reads
    assert bool(env.calls) is generated
    if not generated:
        assert session.title_checked_at == at
        assert session.title_check_count == (3 if count == 8 else count)
        assert env.effects == []


@pytest.mark.parametrize("agent_state", list(AgentState))
@pytest.mark.parametrize("first", [True, False])
def test_live_hybrid_only_allows_first_check(env, session, agent_state, first):
    Session.objects.filter(pk=session.pk).update(hybrid=True, title_check_count=None if first else 1)
    env.agents[session.id] = SimpleNamespace(is_hybrid=True, state=agent_state)
    run(env, session)
    allowed = first or agent_state == AgentState.DEAD
    assert bool(env.calls) is allowed
    if not allowed:
        assert_untouched(env, session)
        env.read.assert_not_called()


@pytest.mark.parametrize("case", ["legacy-null", "spawned", "archived-closing", "no-agent", "non-hybrid-agent"])
def test_eligible_special_cases(env, session, case):
    if case == "legacy-null":
        Session.objects.filter(pk=session.pk).update(title=None, title_origin="", title_check_count=None)
    elif case == "spawned":
        parent = Session.objects.create(id="creator", project=session.project, file_path="creator.jsonl")
        Session.objects.filter(pk=session.pk).update(spawned_by=parent)
    elif case == "archived-closing":
        Session.objects.filter(pk=session.pk).update(archived=True, title_checked_at=NOW)
    else:
        Session.objects.filter(pk=session.pk).update(hybrid=True)
        if case == "non-hybrid-agent":
            env.agents[session.id] = SimpleNamespace(is_hybrid=False, state=AgentState.USER_TURN)
    run(env, session, closing=case == "archived-closing")
    assert session.title == "Session titles and provider sync"
    assert session.title_origin == "auto"
    assert session.title_check_count == 7
    assert env.effects == ["broadcast", "reindex", "rename"]


@pytest.mark.parametrize("first", [True, False])
@pytest.mark.parametrize("provider", [Provider.CLAUDE_CODE, Provider.CODEX])
def test_current_title_only_after_successful_check(env, session, first, provider):
    Session.objects.filter(pk=session.pk).update(provider=provider)
    if first:
        Session.objects.filter(pk=session.pk).update(title_check_count=None)
    run(env, session)
    assert env.calls[0][2] == (None if first else "Current")
    assert session.title_checked_at == NOW
    assert session.title_check_count == 7
    env.read.assert_called_once_with(session.id)


@pytest.mark.parametrize("output,changed", [("Current", False), (" Current  ", False),
                                          ("current", True), ("Current!", True)])
def test_kept_is_trimmed_exact_comparison(env, session, output, changed):
    env.output = output
    run(env, session)
    assert session.title == output.strip()
    assert session.title_check_count == 7
    assert session.title_checked_at == NOW
    assert len(env.pushes) == int(changed)
    assert env.effects == (["broadcast", "reindex", "rename"] if changed else [])


@pytest.mark.parametrize("failure", ["none", "exception", "timeout", "guard", "rejected", "provider-validation",
                                     "unavailable", "prompt"])
def test_failure_only_moves_attempt_time(env, session, failure):
    env.output = {"none": None, "exception": RuntimeError("failed"), "timeout": TimeoutError("timed out"),
                  "rejected": "First\nSecond"}.get(failure, env.output)
    if failure == "guard":
        env.output = HermeticGuardViolation("test guard")
    elif failure == "provider-validation":
        env.validate.side_effect = lambda title: TitleValidationResult(None, "provider rejected")
    elif failure == "unavailable":
        env.running = False
    elif failure == "prompt":
        env.settings["titleSystemPrompt"] = "Missing placeholder"
    run(env, session)
    assert session.title == "Current"
    assert session.title_origin == "auto"
    assert session.title_check_count == 1
    assert session.title_checked_at == NOW
    assert env.effects == []
    if failure == "prompt":
        env.read.assert_not_called()


def test_first_failure_retries_at_interval_without_losing_messages(env, session):
    Session.objects.filter(pk=session.pk).update(title_check_count=None, title_checked_at=None, user_message_count=1)
    env.messages = ["Subject"]
    env.output = None
    run(env, session)
    assert session.title_check_count is None
    env.calls.clear()
    env.output = "Subject title"
    env.clock = NOW + timedelta(minutes=14, seconds=59)
    run(env, session)
    assert env.calls == []
    env.clock = NOW + timedelta(minutes=15)
    run(env, session)
    assert session.title_check_count == 1
    assert session.title == "Subject title"
    assert env.calls[0][2] is None


@pytest.mark.parametrize("messages", [["/compact"] * 7, ["...", "", "🦀"] * 3, ["Subject"] * 6 + ["/compact"]])
def test_non_relevant_messages_do_not_trigger_generation(env, session, messages):
    env.messages = messages
    run(env, session)
    assert env.calls == env.effects == []
    assert session.title_checked_at == OLD


def test_same_relevant_list_drives_count_and_source(env, session):
    env.messages = ["/compact", "...", "Start", "/rename New title", "End"]
    Session.objects.filter(pk=session.pk).update(title_check_count=None)
    run(env, session)
    assert session.title_check_count == 3
    assert env.calls[0][0] == "[Message 1]\nStart\n\n[Message 2]\n/rename New title\n\n[Message 3]\nEnd"
    env.read.assert_called_once_with(session.id)


def test_real_parser_discards_non_text_rows(env, session, monkeypatch):
    helpers = get_provider_helpers(Provider.CLAUDE_CODE)
    monkeypatch.setattr(env.helpers, "get_title_messages", helpers.get_title_messages)
    for line, content in enumerate(["/compact", [], "...", [{"type": "image", "source": {}}]], 1):
        SessionItem.objects.create(session=session, line_num=line, kind=ItemKind.USER_MESSAGE,
                                   content=orjson.dumps({"type": "user", "message": {"role": "user", "content": content}}).decode())
    run(env, session)
    assert env.calls == env.effects == []
    assert session.title_check_count == 0
    assert session.title_checked_at == OLD


@pytest.mark.parametrize("outcome", ["success", "failure", "rebase"])
@pytest.mark.parametrize("race", ["title", "origin", "aba", "pending", "archive", "hidden", "stale", "subagent", "hybrid", "delete"])
def test_concurrent_changes_discard_all_writes(env, session, monkeypatch, outcome, race):
    if outcome == "failure":
        env.output = None
    elif outcome == "rebase":
        env.messages = ["Subject"] * 3
        Session.objects.filter(pk=session.pk).update(user_message_count=3, title_check_count=8)
    previous_count = 8 if outcome == "rebase" else 1

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause():
            entered.set()
            await release.wait()

        if outcome == "rebase":
            real_apply = env.module._apply_rebase

            async def paused_apply(*args, **kwargs):
                await pause()
                return await real_apply(*args, **kwargs)

            monkeypatch.setattr(env.module, "_apply_rebase", paused_apply)
        else:
            env.generation_hook = pause
        task = asyncio.create_task(env.module.check_session_title(session.id))
        await asyncio.wait_for(entered.wait(), timeout=5)

        async def mutate():
            row = Session.objects.filter(pk=session.pk)
            if race == "delete":
                await row.adelete()
            elif race == "pending":
                pending_titles.set_pending_title(session.id, "Pending")
            elif race == "hybrid":
                await row.aupdate(hybrid=True)
                env.agents[session.id] = SimpleNamespace(is_hybrid=True, state=AgentState.STARTING)
            elif race == "aba":
                await row.aupdate(title="User B", title_origin="user")
                await row.aupdate(title="Current")
            else:
                fields = {"title": {"title": "Different"}, "origin": {"title_origin": "user"},
                          "archive": {"archived": True}, "hidden": {"hidden": True}, "stale": {"stale": True},
                          "subagent": {"type": SessionType.SUBAGENT}}[race]
                await row.aupdate(**fields)

        await db_writer.run_under_db_write_lock(mutate)
        release.set()
        await task

    async_to_sync(scenario)()
    if race != "delete":
        session.refresh_from_db()
        assert_untouched(env, session, previous_count)
        assert session.title == ("Different" if race == "title" else "Current")
    assert env.effects == []


@pytest.mark.parametrize("operation", ["success", "failure", "rebase"])
def test_conditional_update_zero_rows_is_discarded(env, session, monkeypatch, operation):
    from django.db.models.query import QuerySet

    async def no_match(self, **kwargs):
        assert db_writer.get_db_write_lock().locked()
        return 0

    monkeypatch.setattr(QuerySet, "aupdate", no_match)
    if operation == "rebase":
        result = async_to_sync(env.module._apply_rebase)(session, TitleCheckState(0, OLD), closing=False)
        assert result is False
    else:
        result = async_to_sync(env.module._apply_check)(
            session, TitleCheckState(7, NOW), title="New title", now=NOW, closing=False,
            succeeded=operation == "success")
        assert result == "discarded"
    session.refresh_from_db()
    assert_untouched(env, session)


@pytest.mark.parametrize("rename_fails", [True, False])
@pytest.mark.parametrize("changed_during_push", [True, False])
def test_echo_and_corrective_push(env, session, rename_fails, changed_during_push, caplog):
    async def scenario():
        entered, release, correction_entered, correction_release = (asyncio.Event() for _ in range(4))

        async def rename_hook(number):
            if number == 1:
                entered.set()
                await release.wait()
                if rename_fails:
                    raise RuntimeError("rename failed")
            else:
                correction_entered.set()
                await correction_release.wait()

        env.rename_hook = rename_hook
        task = asyncio.create_task(env.module.check_session_title(session.id))
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert not task.done()
        if changed_during_push:
            await db_writer.run_under_db_write_lock(
                lambda: Session.objects.filter(pk=session.pk).aupdate(title="User choice", title_origin="user"))
        release.set()
        if changed_during_push and not rename_fails:
            await asyncio.wait_for(correction_entered.wait(), timeout=5)
            assert not task.done()
            correction_release.set()
        await task

    async_to_sync(scenario)()
    expected = [(session.id, "Session titles and provider sync")]
    if changed_during_push and not rename_fails:
        expected.append((session.id, "User choice"))
    assert env.pushes == expected
    assert title_echo._automatic_title_echoes[session.id].title == "Session titles and provider sync"
    session.refresh_from_db()
    assert session.title_check_count == 7
    assert session.title_checked_at == NOW
    assert session.title == ("User choice" if changed_during_push else "Session titles and provider sync")
    if rename_fails:
        assert "rename" in caplog.text


@pytest.mark.parametrize("effect", ["broadcast", "reindex", "reindex-task", "correction"])
def test_effect_failure_is_logged_without_rolling_back(env, session, monkeypatch, effect, caplog):
    async def fail(*args):
        raise RuntimeError("side effect failed")

    if effect == "broadcast":
        monkeypatch.setattr(env.module, "broadcast_session_updated", fail)
    elif effect == "reindex":
        def fail_sync(*args):
            raise RuntimeError("side effect failed")
        monkeypatch.setattr(env.module, "request_session_reindex", fail_sync)
    elif effect == "reindex-task":
        monkeypatch.setattr(env.module, "request_session_reindex", lambda sid: asyncio.create_task(fail()))
    else:
        async def rename_hook(number):
            if number == 1:
                await db_writer.run_under_db_write_lock(
                    lambda: Session.objects.filter(pk=session.pk).aupdate(title="User", title_origin="user"))
            else:
                await fail()
        env.rename_hook = rename_hook
    run(env, session)
    assert session.title_check_count == 7
    assert session.title_checked_at == NOW
    assert len(env.pushes) == (2 if effect == "correction" else 1)
    assert "side effect failed" in caplog.text
