"""Exact turn references remain useful across late indexing and journal cleanup."""

import asyncio
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import orjson
import pytest
from django.utils import timezone

from tests.test_completion_publication import bridge, content_row_factory
from twicc.agent.completion_types import NativeMessageRef
from twicc.agent.states import AgentState
from twicc.core.enums import ItemKind, Provider
from twicc.core.models import LogicalTurn, ProcessRun, Session, SessionItem, TimelineEvent, TimelineKind
from twicc.core.services.timeline_messages import record_indexed_messages, recover_referenced_messages


def refs(event):
    return [NativeMessageRef(**value) for value in event.data["message_refs"] or []]


def linked_texts(event):
    wanted = refs(event)
    messages = list(TimelineEvent.objects.filter(session_id=event.session_id, kind__in=[
        TimelineKind.USER_MESSAGE_SENT, TimelineKind.ASSISTANT_MESSAGE_SENT]))
    return [message.data["text"] for ref in wanted for message in messages
            if ref._asdict() in message.data["source_refs"]]


def index(agent, factory, ref, line, text, *, final=True):
    user = ref.kind in {"claude_uuid", "codex_client"} and not ref.native_id.startswith("answer")
    if agent.provider == Provider.CLAUDE_CODE:
        parsed = {"type": "user" if user else "assistant", "uuid": ref.native_id,
                  "message": {"id": "api-" + ref.native_id, "role": "user" if user else "assistant",
                              "content": [{"type": "text", "text": text}]}}
        if final:
            parsed["message"]["stop_reason"] = "end_turn"
    else:
        parsed = {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": agent.session_id,
                  "turn_id": ref.turn_id or "physical", "item": {"type": "UserMessage" if user else "AgentMessage",
                  "id": "native-" + ref.native_id if user else ref.native_id,
                  **({"clientId": ref.native_id} if user else {"phase": "final_answer" if final else None}),
                  "content": [{"type": "text" if user else "Text", "text": text}]}}}
    SessionItem.objects.create(session_id=agent.session_id, line_num=line,
        kind=ItemKind.USER_MESSAGE if user else ItemKind.ASSISTANT_MESSAGE, timestamp=timezone.now(),
        message_id="api-" + ref.native_id if agent.provider == Provider.CLAUDE_CODE else None,
        content="", current_content=factory(orjson.dumps(parsed).decode(), False))


@pytest.mark.parametrize("provider", [Provider.CODEX, Provider.CLAUDE_CODE])
def test_end_before_indexing_keeps_every_input_and_final(bridge, content_row_factory, provider):
    manager, agent = bridge
    agent.provider = provider
    Session.objects.filter(pk=agent.session_id).update(provider=provider.value)
    ProcessRun.objects.filter(pk=agent.process_run.pk).update(state="user_turn")
    agent.process_run.state = "user_turn"
    make = lambda name: NativeMessageRef("claude_uuid" if provider == Provider.CLAUDE_CODE else (
        "codex_item" if name.startswith("answer") else "codex_client"), name,
        "physical" if provider == Provider.CODEX and name.startswith("answer") else None)
    a, b, answer_b, answer_a = map(make, ["A", "B", "answer-B", "answer-A"])

    token_a, token_b = str(uuid4()), str(uuid4())
    async def scenario():
        agent._completion_input_pending(token_a)
        agent._timeline_reserve_input(token_a, a)
        agent._completion_input_settled(token_a, "accepted")
        agent._completion_activity()
        await manager._persist_process_run_transition(agent, AgentState.ASSISTANT_TURN)
        agent._completion_input_pending(token_b)
        agent._timeline_reserve_input(token_b, b)
        agent._completion_input_settled(token_b, "accepted")
        agent._completion_provider_ended(final_ref=answer_b)
        agent._completion_activity()
        agent._completion_provider_ended(final_ref=answer_a)
        old = agent.completion_controller.terminate("normal", asyncio.get_running_loop().time())
        # A later interval must never enrich an older closure.
        agent._completion_activity()
        agent._timeline_observe_message(make("new-user"), role="user")
        await asyncio.to_thread(manager._store_completion_snapshot_admitted, agent, old, state=AgentState.ASSISTANT_TURN)
        agent._cancel_completion_timer()

    asyncio.run(scenario())
    start = TimelineEvent.objects.filter(kind=TimelineKind.TURN_STARTED).first()
    end = TimelineEvent.objects.get(kind=TimelineKind.TURN_ENDED)
    assert refs(start) == [a]
    assert refs(end) == [a, b, answer_b, answer_a]
    assert TimelineEvent.objects.filter(kind=TimelineKind.USER_MESSAGE_SENT).count() == 0
    for line, (ref, text) in enumerate([(a, "A"), (b, "B"), (answer_b, "reply B"), (answer_a, "reply A")], 1):
        index(agent, content_row_factory, ref, line, text)
    recover_referenced_messages(agent.session_id)
    assert linked_texts(end) == ["A", "B", "reply B", "reply A"]
    before = list(TimelineEvent.objects.filter(kind__in=[TimelineKind.USER_MESSAGE_SENT,
        TimelineKind.ASSISTANT_MESSAGE_SENT]).values_list("data", flat=True))
    LogicalTurn.objects.all().delete()
    agent._timeline_message_refs.clear()
    recover_referenced_messages(agent.session_id)
    assert list(TimelineEvent.objects.filter(kind__in=[TimelineKind.USER_MESSAGE_SENT,
        TimelineKind.ASSISTANT_MESSAGE_SENT]).values_list("data", flat=True)) == before


def test_reserved_identity_without_source_never_reuses_previous_prompt(bridge, content_row_factory):
    manager, agent = bridge
    index(agent, content_row_factory, NativeMessageRef("codex_client", "old", "physical"), 1, "old")
    failed = str(uuid4())
    async def scenario():
        agent._completion_input_pending(failed)
        agent._timeline_reserve_input(failed, NativeMessageRef("codex_client", "missing"))
        agent._completion_activity()
        agent._completion_input_settled(failed, "uncertain")
        closed = agent.completion_controller.terminate("provider_error", asyncio.get_running_loop().time())
        await asyncio.to_thread(manager._store_completion_snapshot_admitted, agent, closed, state=AgentState.USER_TURN)
    asyncio.run(scenario())
    recover_referenced_messages(agent.session_id)
    assert not TimelineEvent.objects.filter(kind=TimelineKind.USER_MESSAGE_SENT).exists()


def test_markerless_final_is_captured_during_logical_hold(bridge, content_row_factory):
    manager, agent = bridge
    agent.provider = Provider.CLAUDE_CODE
    Session.objects.filter(pk=agent.session_id).update(provider="claude_code")
    final = NativeMessageRef("claude_uuid", "answer-proof")
    index(agent, content_row_factory, final, 1, "complete answer", final=False)
    async def scenario():
        agent._completion_activity()
        agent._completion_holds_changed(frozenset({"child"}))
        agent._completion_provider_ended(final_ref=final)
        await manager._persist_completion(agent, agent.completion_controller.snapshot())
    with patch("twicc.providers.claude_code.sessions_watcher.get_watcher") as watcher:
        watcher.return_value.timeline_since = timezone.now() - timedelta(minutes=1)
        asyncio.run(scenario())
    assert TimelineEvent.objects.get(kind=TimelineKind.ASSISTANT_MESSAGE_SENT).data["text"] == "complete answer"
    assert not TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists()


def test_old_closure_restores_new_start_with_new_trigger(bridge):
    manager, agent = bridge
    old_user, new_user = NativeMessageRef("codex_client", "old"), NativeMessageRef("codex_client", "new")
    async def scenario():
        agent._completion_activity()
        agent._timeline_observe_message(old_user, role="user")
        old = agent.completion_controller.terminate("normal", asyncio.get_running_loop().time())
        agent._completion_activity(first_ref=new_user)
        agent._timeline_observe_message(new_user, role="user")
        await asyncio.to_thread(manager._store_completion_snapshot_admitted, agent, old,
                                state=AgentState.ASSISTANT_TURN)
    asyncio.run(scenario())
    end = TimelineEvent.objects.get(kind=TimelineKind.TURN_ENDED)
    start = TimelineEvent.objects.get(kind=TimelineKind.TURN_STARTED)
    assert refs(end) == [old_user]
    assert refs(start) == [new_user]


def test_rollback_retains_references_until_end_commit(bridge):
    manager, agent = bridge
    user = NativeMessageRef("codex_client", "user")
    async def scenario():
        agent._completion_activity(first_ref=user)
        agent._timeline_observe_message(user, role="user")
        closed = agent.completion_controller.terminate("normal", asyncio.get_running_loop().time())
        def rollback():
            with manager._completion_transaction(agent):
                manager._store_completion_snapshot_admitted(agent, closed, state=AgentState.USER_TURN)
                raise RuntimeError("rollback")
        with pytest.raises(RuntimeError, match="rollback"):
            await asyncio.to_thread(rollback)
        assert agent._timeline_refs_for_turn(closed) == [user._asdict()]
        assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists())
        await asyncio.to_thread(manager._store_completion_snapshot_admitted, agent, closed, state=AgentState.USER_TURN)
        assert closed.turn_id not in agent._timeline_message_refs
    asyncio.run(scenario())
    assert refs(TimelineEvent.objects.get(kind=TimelineKind.TURN_ENDED)) == [user]


def test_automatic_start_has_no_later_input_trigger(bridge):
    manager, agent = bridge
    ProcessRun.objects.filter(pk=agent.process_run.pk).update(state="user_turn")
    agent.process_run.state = "user_turn"
    async def scenario():
        agent._completion_activity(first_ref=NativeMessageRef("codex_item", "automatic", "physical"))
        agent._timeline_observe_message(NativeMessageRef("codex_item", "later-input", "physical"), role="user")
        await manager._persist_process_run_transition(agent, AgentState.ASSISTANT_TURN)
    asyncio.run(scenario())
    assert refs(TimelineEvent.objects.get(kind=TimelineKind.TURN_STARTED)) == []


def test_verified_source_replacement_cannot_capture_old_interval(bridge, content_row_factory):
    manager, agent = bridge
    final = NativeMessageRef("codex_item", "answer-old", "physical")
    async def scenario():
        agent._completion_storage_incarnation = "old-source"
        agent._completion_history_epoch = 1
        agent._completion_activity(first_ref=final)
        agent._completion_provider_ended(final_ref=final)
        await manager._persist_completion(agent, agent.completion_controller.snapshot())
        SessionUpdate = lambda: Session.objects.filter(pk=agent.session_id).update(storage_incarnation="new-source", history_epoch=2)
        await asyncio.to_thread(SessionUpdate)
        await asyncio.to_thread(index, agent, content_row_factory, final, 1, "replacement text")
        agent._completion_holds_changed(frozenset({"child"}))
        await manager._persist_completion(agent, agent.completion_controller.snapshot())
    with patch("twicc.providers.codex.sessions_watcher.get_watcher") as watcher:
        watcher.return_value.timeline_since = timezone.now() - timedelta(minutes=1)
        asyncio.run(scenario())
    assert not TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT).exists()


@pytest.mark.parametrize("provider", [Provider.CODEX, Provider.CLAUDE_CODE])
def test_two_ended_intervals_keep_exact_refs_when_indexing_reverses(bridge, content_row_factory, provider):
    manager, agent = bridge
    agent.provider = provider
    Session.objects.filter(pk=agent.session_id).update(provider=provider.value)
    users = [NativeMessageRef("claude_uuid" if provider == Provider.CLAUDE_CODE else "codex_client", name)
             for name in ("old", "current")]
    finals = [NativeMessageRef("claude_uuid" if provider == Provider.CLAUDE_CODE else "codex_item", name,
                              None if provider == Provider.CLAUDE_CODE else "physical")
              for name in ("answer-old", "answer-current")]
    async def scenario():
        for user, final in zip(users, finals):
            agent._completion_activity(first_ref=user)
            agent._timeline_observe_message(user, role="user")
            agent._completion_provider_ended(final_ref=final)
            closed = agent.completion_controller.terminate("normal", asyncio.get_running_loop().time())
            await asyncio.to_thread(manager._store_completion_snapshot_admitted, agent, closed,
                                    state=AgentState.USER_TURN)
    asyncio.run(scenario())
    old, current = TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).order_by("id")
    for ref, line, text in [(users[1], 3, "current"), (finals[1], 4, "current answer"),
                            (users[0], 1, "old"), (finals[0], 2, "old answer")]:
        index(agent, content_row_factory, ref, line, text)
        # A new watcher boundary cannot discard saved live references.
        record_indexed_messages(agent.session_id, new_line_nums=[line],
                                timeline_since=timezone.now() + timedelta(days=1))
    assert linked_texts(old) == ["old", "old answer"]
    assert linked_texts(current) == ["current", "current answer"]
    assert TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).count() == 2


@pytest.mark.parametrize("ending", ["interrupted", "provider_error", "stop"])
@pytest.mark.parametrize("source_first", [True, False])
def test_markerless_hybrid_candidate_without_end_proof_never_captures(bridge, content_row_factory, ending, source_first):
    from tests.test_hybrid_completion import make_agent
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    manager, previous = bridge
    agent = make_agent()
    agent.session_id = previous.session_id
    agent.project_id = previous.project_id
    agent.process_run = previous.process_run
    Session.objects.filter(pk=agent.session_id).update(provider="claude_code")
    ref = NativeMessageRef("claude_uuid", "answer-candidate")
    if source_first:
        index(agent, content_row_factory, ref, 1, "unverified candidate", final=False)
    async def scenario():
        compute = ClaudeCodeSessionCompute()
        events = compute.extract_completion_observations({"type": "assistant", "uuid": ref.native_id,
            "message": {"content": [{"type": "text", "text": "unverified candidate"}]}}, 1)
        agent.observe_jsonl_observations(events)
        if ending == "stop":
            closed = agent.completion_controller.terminate("ended", asyncio.get_running_loop().time())
        else:
            from tests.test_hybrid_completion import observation
            agent.observe_jsonl_observations((observation(ending),))
            closed = agent.completion_controller.snapshot()
        await asyncio.to_thread(manager._store_completion_snapshot_admitted, agent, closed,
                                state=AgentState.USER_TURN)
    with patch("twicc.providers.claude_code.sessions_watcher.get_watcher") as watcher:
        watcher.return_value.timeline_since = timezone.now() - timedelta(minutes=1)
        asyncio.run(scenario())
    if not source_first:
        index(agent, content_row_factory, ref, 1, "unverified candidate", final=False)
    record_indexed_messages(agent.session_id, new_line_nums=[1],
                            timeline_since=timezone.now() - timedelta(minutes=1))
    recover_referenced_messages(agent.session_id)
    assert refs(TimelineEvent.objects.get(kind=TimelineKind.TURN_ENDED)) == []
    assert not TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT).exists()


def test_explicit_hybrid_final_survives_later_unverified_candidate_and_stop(bridge, content_row_factory):
    from tests.test_hybrid_completion import make_agent, observation
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    manager, previous = bridge
    agent = make_agent()
    agent.session_id, agent.project_id, agent.process_run = previous.session_id, previous.project_id, previous.process_run
    Session.objects.filter(pk=agent.session_id).update(provider="claude_code")
    final = NativeMessageRef("claude_uuid", "answer-explicit")
    candidate = NativeMessageRef("claude_uuid", "answer-candidate")
    async def scenario():
        compute = ClaudeCodeSessionCompute()
        for ref, marker in [(final, "end_turn"), (candidate, None)]:
            agent.observe_jsonl_observations(compute.extract_completion_observations({
                "type": "assistant", "uuid": ref.native_id, "message": {
                "content": [{"type": "text", "text": "answer"}], "stop_reason": marker}}, 1))
        agent.observe_jsonl_observations((observation("interrupted"),))
        await asyncio.to_thread(manager._store_completion_snapshot_admitted, agent,
            agent.completion_controller.snapshot(), state=AgentState.USER_TURN)
    asyncio.run(scenario())
    index(agent, content_row_factory, final, 1, "explicit final")
    index(agent, content_row_factory, candidate, 2, "unverified", final=False)
    recover_referenced_messages(agent.session_id)
    end = TimelineEvent.objects.get(kind=TimelineKind.TURN_ENDED)
    assert refs(end) == [final]
    assert linked_texts(end) == ["explicit final"]
    assert TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT).count() == 1


@pytest.mark.parametrize("status", ["failed", "interrupted", "completed"])
@pytest.mark.parametrize("runtime_available", [True, False])
@pytest.mark.parametrize("expired", [True, False])
def test_live_indexing_uses_verified_replacement_finals_during_open_interval(bridge, content_row_factory, status, runtime_available, expired):
    from tests.test_codex_completion import make_agent, run, event
    manager, previous = bridge
    agent = make_agent(ephemeral=False)
    agent.session_id, agent.project_id, agent.process_run = previous.session_id, previous.project_id, previous.process_run
    agent.process_run.state = "user_turn"
    ProcessRun.objects.filter(pk=agent.process_run.pk).update(state="user_turn")
    manager._agents[agent.session_id] = agent
    from types import SimpleNamespace
    agent._codex = SimpleNamespace(pid=None)
    final = NativeMessageRef("codex_item", "answer-replaced", "physical-1")
    async def scenario():
        token = str(uuid4())
        agent._completion_input_pending(token)
        agent._timeline_reserve_input(token, NativeMessageRef("codex_client", token))
        agent._completion_holds_changed(frozenset({"child"}))
        replacement = asyncio.create_task(asyncio.Event().wait())
        agent._turn_task = replacement
        try:
            await run(agent, [event("item/completed", thread_id=agent.session_id, item=dict(
                type="agentMessage", id=final.native_id, phase=None, text="candidate", delivery=None)),
                event("turn/completed", thread_id=agent.session_id, status=status)])
            await manager._persist_process_run_transition(agent, AgentState.ASSISTANT_TURN)
            await manager._persist_completion(agent, agent.completion_controller.snapshot())
        finally:
            replacement.cancel()
            await asyncio.gather(replacement, return_exceptions=True)
            agent._cancel_completion_timer()
            agent._cancel_compaction_timeout()
    asyncio.run(scenario())
    assert TimelineEvent.objects.get(kind=TimelineKind.TURN_STARTED).data["message_refs"]
    turn = LogicalTurn.objects.get(session_id=agent.session_id)
    assert turn.outcome is None and turn.final_refs == [final._asdict()]
    if expired:
        from twicc.logical_turn_cleanup_task import purge_logical_turns
        LogicalTurn.objects.filter(pk=turn.pk).update(opened_at=timezone.now() - timedelta(days=8))
        assert purge_logical_turns(now=timezone.now()) == 1
    index(agent, content_row_factory, final, 1, "candidate", final=False)
    runtime_refs = manager.timeline_verified_message_refs(agent.session_id) if runtime_available else None
    record_indexed_messages(agent.session_id, new_line_nums=[1], verified_final_refs=runtime_refs,
                            timeline_since=timezone.now() - timedelta(minutes=1))
    recover_referenced_messages(agent.session_id)
    messages = list(TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT))
    assert [message.data["text"] for message in messages] == (["candidate"] if status == "completed" and runtime_available else [])


@pytest.mark.parametrize("ending", ["end", "interrupted"])
def test_open_claude_candidate_waits_for_successful_native_end_proof(bridge, content_row_factory, ending):
    from tests.test_hybrid_completion import make_agent, observation
    from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
    manager, previous = bridge
    agent = make_agent()
    agent.session_id, agent.project_id, agent.process_run = previous.session_id, previous.project_id, previous.process_run
    agent.process_run.state = "user_turn"
    ProcessRun.objects.filter(pk=agent.process_run.pk).update(state="user_turn")
    Session.objects.filter(pk=agent.session_id).update(provider="claude_code")
    final = NativeMessageRef("claude_uuid", "answer-candidate")
    async def scenario():
        agent.observe_jsonl_observations((observation("user_message", "user"),))
        await manager._persist_process_run_transition(agent, AgentState.ASSISTANT_TURN)
        events = ClaudeCodeSessionCompute().extract_completion_observations({"type": "assistant",
            "uuid": final.native_id, "message": {"content": [{"type": "text", "text": "candidate"}]}}, 1)
        agent.observe_jsonl_observations(events)
        assert agent.completion_controller.snapshot().final_refs == ()
        await manager._persist_completion(agent, agent.completion_controller.snapshot())
        await asyncio.to_thread(index, agent, content_row_factory, final, 1, "candidate", final=False)
        await asyncio.to_thread(record_indexed_messages, agent.session_id, new_line_nums=[1],
                                timeline_since=timezone.now() - timedelta(minutes=1))
        assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT).exists())
        agent._completion_holds_changed(frozenset({"child"}))
        agent.observe_jsonl_observations((observation(ending),))
        await manager._persist_completion(agent, agent.completion_controller.snapshot())
        agent._cancel_completion_timer()
    with patch("twicc.providers.claude_code.sessions_watcher.get_watcher") as watcher:
        watcher.return_value.timeline_since = timezone.now() - timedelta(minutes=1)
        asyncio.run(scenario())
    messages = list(TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT))
    assert [message.data["text"] for message in messages] == (["candidate"] if ending == "end" else [])
    assert not TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists()


def test_closed_codex_markerless_final_uses_saved_refs_without_runtime(bridge, content_row_factory):
    manager, agent = bridge
    final = NativeMessageRef("codex_item", "answer-closed", "physical")
    async def scenario():
        agent._completion_activity()
        agent._completion_provider_ended(final_ref=final)
        closed = agent.completion_controller.terminate("normal", asyncio.get_running_loop().time())
        await asyncio.to_thread(manager._store_completion_snapshot_admitted, agent, closed,
                                state=AgentState.USER_TURN)
        assert closed.turn_id not in agent._timeline_message_refs
    asyncio.run(scenario())
    index(agent, content_row_factory, final, 1, "verified before logical end", final=False)
    record_indexed_messages(agent.session_id, new_line_nums=[1], timeline_since=timezone.now() + timedelta(days=1))
    end = TimelineEvent.objects.get(kind=TimelineKind.TURN_ENDED)
    assert linked_texts(end) == ["verified before logical end"]


async def purge_open_interval(manager, agent):
    from twicc.logical_turn_cleanup_task import purge_logical_turns

    await manager._persist_completion(agent, agent.completion_controller.snapshot())
    await asyncio.to_thread(lambda: LogicalTurn.objects.update(opened_at=timezone.now() - timedelta(days=8)))
    assert await asyncio.to_thread(purge_logical_turns, now=timezone.now()) == 1
    assert not await asyncio.to_thread(LogicalTurn.objects.exists)


@pytest.mark.parametrize("provider", [Provider.CODEX, Provider.CLAUDE_CODE])
@pytest.mark.parametrize("source_first", [True, False])
def test_verified_markerless_final_after_journal_purge_captures_during_hold(
    bridge, content_row_factory, provider, source_first,
):
    manager, agent = bridge
    agent.provider = provider
    Session.objects.filter(pk=agent.session_id).update(provider=provider.value)
    final = NativeMessageRef("claude_uuid" if provider == Provider.CLAUDE_CODE else "codex_item",
                             "answer-after-expiry", None if provider == Provider.CLAUDE_CODE else "physical")

    async def scenario():
        agent._completion_activity()
        agent._completion_holds_changed(frozenset({"child"}))
        await purge_open_interval(manager, agent)
        if source_first:
            await asyncio.to_thread(index, agent, content_row_factory, final, 1, "verified final after expiry", final=False)
        agent._completion_provider_ended(final_ref=final)
        # Capture must work even though persistence cannot update an expired row.
        assert not await manager._persist_completion(agent, agent.completion_controller.snapshot())
        if source_first:
            assert await asyncio.to_thread(lambda: list(TimelineEvent.objects.filter(
                kind=TimelineKind.ASSISTANT_MESSAGE_SENT).values_list("data__text", flat=True))) == ["verified final after expiry"]
        else:
            await asyncio.to_thread(index, agent, content_row_factory, final, 1, "verified final after expiry", final=False)
        await asyncio.to_thread(record_indexed_messages, agent.session_id, new_line_nums=[1],
            timeline_since=timezone.now() - timedelta(minutes=1),
            verified_final_refs=manager.timeline_verified_message_refs(agent.session_id))
        agent._cancel_completion_timer()

    with patch(f"twicc.providers.{provider.value}.sessions_watcher.get_watcher") as watcher:
        watcher.return_value.timeline_since = timezone.now() - timedelta(minutes=1)
        asyncio.run(scenario())
    assert list(TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT).values_list(
        "data__text", flat=True)) == ["verified final after expiry"]
    assert not LogicalTurn.objects.exists()
    assert not TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists()
    assert agent.completion_controller.is_open


@pytest.mark.parametrize("provider", [Provider.CODEX, Provider.CLAUDE_CODE])
@pytest.mark.parametrize("invalid", ["provider", "incarnation", "epoch", "floor", "old", "current", "nonfinal"])
def test_expired_journal_runtime_proof_preserves_source_boundaries(
    bridge, content_row_factory, provider, invalid,
):
    manager, agent = bridge
    agent.provider = provider
    Session.objects.filter(pk=agent.session_id).update(provider=provider.value, storage_incarnation="original", history_epoch=1)
    agent._completion_storage_incarnation = "original"
    agent._completion_history_epoch = 1
    agent._completion_source_floor = 1 if invalid == "floor" else 0
    final = NativeMessageRef("claude_uuid" if provider == Provider.CLAUDE_CODE else "codex_item",
                             "answer-scoped", None if provider == Provider.CLAUDE_CODE else "physical")

    def source():
        index(agent, content_row_factory, final, 1, "must not capture", final=False)
        if invalid == "provider":
            Session.objects.filter(pk=agent.session_id).update(provider=(
                "claude_code" if provider == Provider.CODEX else "codex"))
        elif invalid == "incarnation":
            Session.objects.filter(pk=agent.session_id).update(storage_incarnation="replacement")
        elif invalid == "epoch":
            Session.objects.filter(pk=agent.session_id).update(history_epoch=2)
        elif invalid == "old":
            SessionItem.objects.update(timestamp=timezone.now() - timedelta(days=1))
        elif invalid in {"current", "nonfinal"}:
            row = SessionItem.objects.get(session_id=agent.session_id, line_num=1)
            from twicc.core.content_storage.readers import effective_current, load_current
            load_current([row])
            parsed = orjson.loads(effective_current(row))
            if provider == Provider.CLAUDE_CODE:
                if invalid == "current":
                    parsed["uuid"] = "replacement-uuid"
                else:
                    parsed["message"]["stop_reason"] = "tool_use"
            elif invalid == "current":
                parsed["payload"]["item"]["id"] = "replacement-item"
            else:
                parsed["payload"]["item"]["phase"] = "commentary"
            SessionItem.objects.filter(pk=row.pk).update(current_content=content_row_factory(orjson.dumps(parsed).decode(), False))

    async def scenario():
        agent._completion_activity()
        agent._completion_holds_changed(frozenset({"child"}))
        await purge_open_interval(manager, agent)
        await asyncio.to_thread(source)
        agent._completion_provider_ended(final_ref=final)
        await manager._persist_completion(agent, agent.completion_controller.snapshot())
        await asyncio.to_thread(record_indexed_messages, agent.session_id, new_line_nums=[1],
            timeline_since=timezone.now() - timedelta(minutes=1),
            verified_final_refs=manager.timeline_verified_message_refs(agent.session_id))
        agent._cancel_completion_timer()

    with patch(f"twicc.providers.{provider.value}.sessions_watcher.get_watcher") as watcher:
        watcher.return_value.timeline_since = timezone.now() - timedelta(minutes=1)
        asyncio.run(scenario())
    assert not LogicalTurn.objects.exists()
    assert not TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT).exists()
    assert not TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists()


@pytest.mark.parametrize("source_floor", [0, 1])
def test_expired_journal_claude_proof_waits_for_exact_terminal_group(bridge, content_row_factory, source_floor):
    manager, agent = bridge
    agent.provider = Provider.CLAUDE_CODE
    Session.objects.filter(pk=agent.session_id).update(provider="claude_code")
    agent._completion_source_floor = source_floor
    terminal = NativeMessageRef("claude_uuid", "answer-terminal")
    group = NativeMessageRef("claude_message", "api-answer-terminal")

    def prefix():
        parsed = {"type": "assistant", "uuid": "answer-prefix", "message": {
            "id": group.native_id, "role": "assistant", "content": [{"type": "text", "text": "prefix"}]}}
        SessionItem.objects.create(session_id=agent.session_id, line_num=1, kind=ItemKind.ASSISTANT_MESSAGE,
            timestamp=timezone.now(), message_id=group.native_id, content="",
            current_content=content_row_factory(orjson.dumps(parsed).decode(), False))

    async def scenario():
        agent._completion_activity()
        agent._completion_holds_changed(frozenset({"child"}))
        await purge_open_interval(manager, agent)
        await asyncio.to_thread(prefix)
        # Neither a group ID nor a terminal UUID that is not indexed proves completion.
        for ref in (group, terminal):
            agent._completion_provider_ended(final_ref=ref)
            await manager._persist_completion(agent, agent.completion_controller.snapshot())
            await asyncio.to_thread(record_indexed_messages, agent.session_id, new_line_nums=[1],
                timeline_since=timezone.now() - timedelta(minutes=1),
                verified_final_refs=manager.timeline_verified_message_refs(agent.session_id))
            assert not await asyncio.to_thread(lambda: TimelineEvent.objects.filter(
                kind=TimelineKind.ASSISTANT_MESSAGE_SENT).exists())
        await asyncio.to_thread(index, agent, content_row_factory, terminal, 2, "terminal", final=False)
        await manager._persist_completion(agent, agent.completion_controller.snapshot())
        expected = ["prefix\nterminal"] if source_floor == 0 else []
        assert await asyncio.to_thread(lambda: list(TimelineEvent.objects.filter(
            kind=TimelineKind.ASSISTANT_MESSAGE_SENT).values_list("data__text", flat=True))) == expected
        await asyncio.to_thread(record_indexed_messages, agent.session_id, new_line_nums=[2],
            timeline_since=timezone.now() - timedelta(minutes=1),
            verified_final_refs=manager.timeline_verified_message_refs(agent.session_id))
        agent._cancel_completion_timer()

    with patch("twicc.providers.claude_code.sessions_watcher.get_watcher") as watcher:
        watcher.return_value.timeline_since = timezone.now() - timedelta(minutes=1)
        asyncio.run(scenario())
    assert list(TimelineEvent.objects.filter(kind=TimelineKind.ASSISTANT_MESSAGE_SENT).values_list(
        "data__text", flat=True)) == (["prefix\nterminal"] if source_floor == 0 else [])
    assert not LogicalTurn.objects.exists()
    assert not TimelineEvent.objects.filter(kind=TimelineKind.TURN_ENDED).exists()
