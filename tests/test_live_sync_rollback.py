"""A failed live transaction preserves its one-shot evidence and replay state."""

import copy
import threading

import orjson
import pytest
from django.db import transaction

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionHistoryFact, SessionItem, ToolResultLink
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.codex.compute import CodexSessionCompute


@pytest.fixture
def cache():
    from twicc.providers.enrichment_cache import EnrichmentCache
    return EnrichmentCache()


def test_retry_owns_original_capture_while_new_capture_stays_independent(cache):
    key = ("s", "call")
    cache.put(key, "A")
    first = cache.borrow(key, source_line=1)
    assert first.value == "A"
    assert cache.borrow(key, source_line=2) is None
    writer = threading.Thread(target=cache.put, args=(key, "B"))
    writer.start()
    writer.join(3)
    assert not writer.is_alive()
    cache.rollback(first)
    retry = cache.borrow(key, source_line=1)
    assert retry.value == "A"
    second = cache.borrow(key, source_line=2)
    assert second.value == "B"
    cache.commit(retry)
    cache.commit(second)
    assert cache.borrow(key, source_line=1) is None
    assert cache.borrow(key, source_line=3) is None


def test_commit_old_capture_preserves_replacement(cache):
    key = ("s", "call")
    cache.put(key, "A")
    first = cache.borrow(key, source_line=1)
    cache.put(key, "B")
    cache.commit(first)
    assert cache.borrow(key, source_line=2).value == "B"


@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_clear_invalidates_active_capture_without_late_resurrection(cache, finish):
    key = ("s", "call")
    cache.put(key, "A")
    first = cache.borrow(key, source_line=1)
    cache.clear_session("s")
    cache.put(key, "B")
    getattr(cache, finish)(first)
    assert cache.borrow(key, source_line=2).value == "B"
    assert cache.borrow(key, source_line=1) is None


def test_active_borrow_is_pinned_then_retry_reservation_expires(cache, monkeypatch):
    from twicc.providers import enrichment_cache
    now = [0.0]
    monkeypatch.setattr(enrichment_cache.time, "monotonic", lambda: now[0])
    key = ("s", "call")
    cache.put(key, "A")
    first = cache.borrow(key, source_line=1)
    now[0] = 400
    cache.cleanup_expired()
    assert cache.borrow(key, source_line=2) is None
    cache.rollback(first)
    now[0] = 699
    retry = cache.borrow(key, source_line=1)
    assert retry.value == "A"
    cache.rollback(retry)
    now[0] = 1000
    cache.cleanup_expired()
    assert cache.borrow(key, source_line=1) is None
    assert cache.borrow(key, source_line=2) is None


def _records(provider):
    if provider == Provider.CLAUDE_CODE:
        return [
            {"type": "assistant", "message": {"id": "m", "role": "assistant", "content": [
                {"type": "tool_use", "id": "call", "name": "Edit", "input": {"file_path": "/a"}},
            ]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call", "content": "done"},
            ]}, "toolUseResult": {"filePath": "/a", "newString": "new"}},
        ]
    return [
        {"type": "response_item", "payload": {"type": "function_call", "call_id": "call",
            "name": "apply_patch", "arguments": "{}"}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "FileChange", "id": "call", "status": "completed", "changes": [],
        }}},
    ]


@pytest.fixture(params=[Provider.CLAUDE_CODE, Provider.CODEX])
def live_case(request, transactional_db, tmp_path):
    provider = request.param
    project = Project.objects.create(id="rollback-project")
    session = Session.objects.create(id="rollback-session", project=project, provider=provider)
    path = tmp_path / "history.jsonl"
    records = _records(provider)
    path.write_bytes(b"".join(orjson.dumps(record) + b"\n" for record in records))
    if provider == Provider.CLAUDE_CODE:
        from twicc.providers.claude_code.agent import original_file_cache as module
        compute = ClaudeCodeSessionCompute()
        module.cache_original_file(session.id, "call", "full old file")
    else:
        from twicc.providers.codex.agent import original_files_cache as module
        compute = CodexSessionCompute()
        module.cache_original_files(session.id, "call", {"/a": "full old file"})
    yield compute, session, path, module
    # A committed run consumes captures. Failed attempts must not leak to another test.
    if hasattr(module, "clear_session"):
        module.clear_session(session.id)


def _relation_rows(session):
    """Compare every persisted fact/link field except database identity and session FK."""
    facts = list(SessionHistoryFact.objects.filter(session=session).order_by("line_num", "kind", "key").values(
        "line_num", "kind", "key", "data",
    ))
    links = list(ToolResultLink.objects.filter(session=session).order_by(
        "tool_use_line_num", "tool_result_line_num", "tool_use_id",
    ).values(
        "tool_use_line_num", "tool_result_line_num", "tool_use_id", "tool_name",
        "tool_result_at", "extra", "error",
    ))
    return facts, links


@pytest.mark.parametrize("failure_point", ["first_pass", "second_pass", "late_save"])
def test_live_retry_preserves_diff_facts_links_and_checkpoint(live_case, monkeypatch, failure_point):
    compute, session, path, module = live_case
    baseline = Session.objects.create(
        id="successful-baseline", project=session.project, provider=session.provider, file_path=str(path),
    )
    if session.provider == Provider.CLAUDE_CODE:
        module.cache_original_file(baseline.id, "call", "full old file")
    else:
        module.cache_original_files(baseline.id, "call", {"/a": "full old file"})
    type(compute)().sync_session_items_from_file(baseline, path)
    expected_facts, expected_links = _relation_rows(baseline)
    assert expected_facts
    assert len(expected_links) == 1

    captured = []
    original_enrich = compute.transform_tool_result_with_cache
    def enrich(*args, **kwargs):
        result = original_enrich(*args, **kwargs)
        if result:
            captured.append(result)
        return result
    original_facts = compute.extract_history_facts
    def facts(*args, **kwargs):
        result = original_facts(*args, **kwargs)
        if captured and failure_point == "first_pass":
            raise RuntimeError("injected first pass failure")
        return result
    original_link = compute.create_tool_result_link_live
    def link(*args, **kwargs):
        result = original_link(*args, **kwargs)
        if failure_point == "second_pass":
            assert ToolResultLink.objects.filter(session=session).exists()
            raise RuntimeError("injected second pass failure")
        return result
    original_save = session.save
    def save(*args, **kwargs):
        original_save(*args, **kwargs)
        if failure_point == "late_save":
            raise RuntimeError("injected late save failure")
    with monkeypatch.context() as patch:
        patch.setattr(compute, "transform_tool_result_with_cache", enrich)
        patch.setattr(compute, "create_tool_result_link_live", link)
        patch.setattr(compute, "extract_history_facts", facts)
        patch.setattr(session, "save", save)
        with pytest.raises(RuntimeError, match="injected"):
            compute.sync_session_items_from_file(session, path)
    assert session.last_offset == 0
    assert session.last_line == 0
    assert not SessionItem.objects.filter(session=session).exists()
    assert not SessionHistoryFact.objects.filter(session=session).exists()
    assert not ToolResultLink.objects.filter(session=session).exists()
    compute.sync_session_items_from_file(session, path)
    item = SessionItem.objects.get(session=session, line_num=2)
    assert "full old file" in item.content
    if captured:
        assert orjson.loads(item.content) == orjson.loads(captured[0])
    actual_facts, actual_links = _relation_rows(session)
    assert actual_facts == expected_facts
    assert actual_links == expected_links
    assert session.last_offset == path.stat().st_size
    assert session.last_line == 2
    assert compute.sync_session_items_from_file(session, path)[0] == []
    pop = module.pop_original_file if session.provider == Provider.CLAUDE_CODE else module.pop_original_files
    assert pop(session.id, "call") is None


def test_live_entry_rejects_caller_transaction(live_case):
    compute, session, path, _module = live_case
    with transaction.atomic():
        with pytest.raises(RuntimeError, match="atomic"):
            compute.sync_session_items_from_file(session, path)
    assert not SessionItem.objects.filter(session=session).exists()


@pytest.mark.parametrize("compute", [ClaudeCodeSessionCompute, CodexSessionCompute])
@pytest.mark.django_db(transaction=True)
def test_live_state_snapshot_restores_nested_state_for_only_failed_session(compute):
    compute = compute()
    compute.begin_session_compute("s")
    compute.begin_session_compute("other")
    before = copy.deepcopy(vars(compute))
    with pytest.raises(RuntimeError, match="injected"):
        with compute.live_state_transaction("s"):
            for name, mapping in before.items():
                if isinstance(mapping, dict) and "s" in mapping:
                    getattr(compute, name)["s"] = {"mutated": [1]}
                    getattr(compute, name)["other"] = "unrelated progress"
            raise RuntimeError("injected")
    for name, mapping in before.items():
        if isinstance(mapping, dict) and "s" in mapping:
            restored = getattr(compute, name)["s"]
            expected = mapping["s"]
            if hasattr(expected, "__slots__") and not isinstance(expected, tuple):
                assert {slot: getattr(restored, slot) for slot in expected.__slots__} == {
                    slot: getattr(expected, slot) for slot in expected.__slots__
                }, name
            else:
                assert restored == expected, name
            assert getattr(compute, name)["other"] == "unrelated progress"


@pytest.mark.parametrize("rollback", [False, True])
@pytest.mark.django_db(transaction=True)
def test_raw_history_replacement_invalidates_reservations_only_on_commit(rollback):
    import asyncio
    from twicc.providers.codex.agent import original_files_cache
    from twicc.providers.codex.compute import get_compute
    from twicc.providers.codex.rollout_migration import ReplaceCodexHistoryJob, _apply_replace_codex_history_job

    project = Project.objects.create(id="replace-project")
    session = Session.objects.create(id="replace-session", project=project, provider=Provider.CODEX)
    cache = original_files_cache._cache
    key = (session.id, "call")
    cache.put(key, {"/a": "old history"})
    reserved = cache.borrow(key, source_line=1)
    cache.rollback(reserved)
    compute = get_compute()
    compute._process_owners[session.id] = {123: "old-owner"}
    loop = asyncio.new_event_loop()
    try:
        job = ReplaceCodexHistoryJob(Provider.CODEX, session.id, [(1, "{}")], 3, 1, 1.0, loop.create_future())
        with transaction.atomic():
            _apply_replace_codex_history_job(job)
            assert cache.borrow(key, source_line=1).value == {"/a": "old history"}
            if rollback:
                transaction.set_rollback(True)
        if rollback:
            assert cache.borrow(key, source_line=1).value == {"/a": "old history"}
            assert compute._process_owners[session.id] == {123: "old-owner"}
            assert not session.items.exists()
        else:
            cache.rollback(reserved)
            assert cache.borrow(key, source_line=1) is None
            assert session.id not in compute._process_owners
            assert list(session.items.values_list("content", flat=True)) == ["{}"]
    finally:
        loop.close()
        cache.clear_session(session.id)
        compute.end_session_compute(session.id)


@pytest.mark.django_db(transaction=True)
def test_nested_replay_mutation_and_new_session_state_are_restored():
    compute = CodexSessionCompute()
    compute._process_owners["s"] = {7: "owner"}
    compute._ended_processes["s"] = {8: 123.0}
    with pytest.raises(RuntimeError, match="injected"):
        with compute.live_state_transaction("s"):
            compute._process_owners["s"].pop(7)
            compute._ended_processes["s"][9] = 124.0
            compute._prev_total_tokens["s"] = 100
            raise RuntimeError("injected")
    assert compute._process_owners["s"] == {7: "owner"}
    assert compute._ended_processes["s"] == {8: 123.0}
    assert "s" not in compute._prev_total_tokens


@pytest.mark.django_db(transaction=True)
def test_failed_relation_does_not_publish_done_cache_or_remove_cached_prompt():
    from twicc.providers.compute_base import (
        AGENTS_LINKS_DONE_CACHE, AGENTS_PROMPT_CACHE, cache_agent_prompt, mark_agent_link_done,
    )
    key = ("rollback-cache-parent", "rollback-cache-child")
    cache_agent_prompt(*key, "original prompt")
    try:
        with pytest.raises(RuntimeError, match="injected"):
            with CodexSessionCompute().live_state_transaction(key[0]):
                mark_agent_link_done(*key)
                raise RuntimeError("injected")
        assert key not in AGENTS_LINKS_DONE_CACHE
        assert AGENTS_PROMPT_CACHE[key] == "original prompt"
    finally:
        AGENTS_LINKS_DONE_CACHE.discard(key)
        AGENTS_PROMPT_CACHE.pop(key, None)


def test_commit_failure_restores_enrichment_and_caller_checkpoint(live_case, monkeypatch):
    from django.db import connection, DatabaseError
    compute, session, path, _module = live_case
    def fail_commit():
        raise DatabaseError("injected commit failure")
    with monkeypatch.context() as patch:
        patch.setattr(connection, "commit", fail_commit)
        with pytest.raises(DatabaseError, match="injected commit"):
            compute.sync_session_items_from_file(session, path)
    assert session.last_offset == 0
    assert not session.items.exists()
    compute.sync_session_items_from_file(session, path)
    assert "full old file" in SessionItem.objects.get(session=session, line_num=2).content
    assert session.last_offset == path.stat().st_size


@pytest.mark.django_db(transaction=True)
def test_failed_billing_pass_retries_token_baseline_and_message_counter(tmp_path, monkeypatch):
    project = Project.objects.create(id="billing-project")
    session = Session.objects.create(id="billing-session", project=project, provider=Provider.CODEX)
    path = tmp_path / "billing.jsonl"
    records = [
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "UserMessage", "id": "prompt", "content": [{"type": "text", "text": "hello"}],
        }}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"total_tokens": 150},
            "last_token_usage": {"input_tokens": 100, "output_tokens": 50,
                                 "cached_input_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 150},
        }}},
    ]
    path.write_bytes(b"".join(orjson.dumps(record) + b"\n" for record in records))
    compute = CodexSessionCompute()
    compute._prev_total_tokens[session.id] = 10
    save = session.save
    def fail_after_save(*args, **kwargs):
        save(*args, **kwargs)
        raise RuntimeError("injected after billing")
    with monkeypatch.context() as patch:
        patch.setattr(session, "save", fail_after_save)
        with pytest.raises(RuntimeError, match="injected"):
            compute.sync_session_items_from_file(session, path)
    assert compute._prev_total_tokens[session.id] == 10
    assert session.user_message_count == 0
    compute.sync_session_items_from_file(session, path)
    assert compute._prev_total_tokens[session.id] == 150
    assert SessionItem.objects.get(session=session, line_num=2).context_usage == 150
    assert session.user_message_count == 1
    assert session.last_offset == path.stat().st_size


@pytest.mark.django_db(transaction=True)
def test_rollback_only_transaction_cannot_return_success(cache):
    compute = CodexSessionCompute()
    key = ("rollback-only", "call")
    cache.put(key, "evidence")
    with pytest.raises(transaction.TransactionManagementError, match="rollback"):
        with compute.live_state_transaction(key[0]):
            assert compute.borrow_enrichment(cache, key[0], key[1], 1) == "evidence"
            compute._prev_total_tokens[key[0]] = 42
            transaction.set_rollback(True)
    assert key[0] not in compute._prev_total_tokens
    assert cache.borrow(key, source_line=1).value == "evidence"
