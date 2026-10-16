"""Lifecycle writes invalidate durable native-tree generations."""
from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from twicc.core.content_storage import lifecycle, state
from twicc.core.models import Project, Session

pytestmark = pytest.mark.django_db


def make_session(name, **kwargs):
    project, _ = Project.objects.get_or_create(id='lifecycle', defaults={'directory': '/lifecycle'})
    return Session.objects.create(id=name, project=project, provider='codex', file_path=name,
                                  content_state_epoch=4, plaintext_verified_epoch=4, **kwargs)


@pytest.mark.parametrize('flag', ['archived', 'hidden'])
def test_one_remaining_flag_keeps_old_root_cold(flag, monkeypatch):
    root = make_session('root', archived=True, hidden=True, created_at=timezone.now() - timedelta(days=31))
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kwargs: requests.append(target))
    with transaction.atomic():
        lifecycle.update_lifecycle(Session.objects.filter(pk=root.id), **{flag: False})
    root.refresh_from_db()
    assert root.is_cold_for_content
    assert root.content_state_epoch == 5
    assert requests == []


def test_clear_last_flag_requests_after_commit(monkeypatch):
    root = make_session('root', archived=True, created_at=timezone.now() - timedelta(days=31))
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kwargs: requests.append(target))
    with pytest.raises(RuntimeError), transaction.atomic():
        lifecycle.update_lifecycle(Session.objects.filter(pk=root.id), archived=False)
        raise RuntimeError('rollback')
    root.refresh_from_db()
    assert root.content_state_epoch == 4
    assert requests == []
    with transaction.atomic():
        lifecycle.update_lifecycle(Session.objects.filter(pk=root.id), archived=False)
        assert requests == []
    root.refresh_from_db()
    assert root.content_state_epoch != root.plaintext_verified_epoch


def test_reparent_invalidates_old_and_new_native_roots_once():
    old = make_session('old')
    new = make_session('new')
    child = make_session('child', type='subagent', parent_session=old)
    lifecycle.update_lifecycle(Session.objects.filter(pk=child.id), parent_session_id=new.id)
    old.refresh_from_db()
    new.refresh_from_db()
    assert old.content_state_epoch == new.content_state_epoch == 5
    assert state.resolve_root_decision(child.id, timezone.now(), frozenset()).root_id == new.id


def test_orchestration_change_does_not_touch_launcher():
    root = make_session('root')
    child = make_session('child', spawned_by=root)
    lifecycle.update_lifecycle(Session.objects.filter(pk=child.id), last_updated_at=timezone.now())
    root.refresh_from_db()
    child.refresh_from_db()
    assert root.content_state_epoch == 4
    assert child.content_state_epoch == 5


def test_noop_keeps_verified_generation():
    root = make_session('root', archived=True)
    lifecycle.update_lifecycle(Session.objects.filter(pk=root.id), archived=True)
    root.refresh_from_db()
    assert root.content_state_epoch == root.plaintext_verified_epoch == 4


def test_active_native_leaf_protects_entire_root():
    root = make_session('root', archived=True, created_at=timezone.now() - timedelta(days=31))
    child = make_session('child', type='subagent', parent_session=root)
    assert state.resolve_root_decision(root.id, timezone.now(), frozenset({child.id})).active


def test_late_parent_and_cycle_reject_old_root_proof():
    root = make_session('root')
    child = make_session('child', type='subagent')
    lifecycle.update_lifecycle(Session.objects.filter(pk=child.id), parent_session_id=root.id)
    root.refresh_from_db()
    assert root.content_state_epoch == 5
    lifecycle.update_lifecycle(Session.objects.filter(pk=root.id), type='subagent', parent_session_id=child.id)
    root.refresh_from_db()
    assert root.content_state_epoch == 6
    assert not state.resolve_root_decision(child.id, timezone.now(), frozenset()).resolved


@pytest.mark.django_db(transaction=True)
def test_real_flag_services_and_postcommit_wakeup(monkeypatch):
    from twicc.core.services.session_visibility import _apply_visibility_flag

    root = make_session('root', hidden=True, created_at=timezone.now() - timedelta(days=20))
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kwargs: requests.append(target))
    _apply_visibility_flag(root.id, False)
    root.refresh_from_db()
    assert root.content_state_epoch == 5
    assert requests == [root.id]


def test_batch_native_root_resolution_loads_actual_metadata(django_assert_num_queries):
    root = make_session('root', archived=True, created_at=timezone.now() - timedelta(days=31))
    children = [make_session(f'child-{i}', type='subagent', parent_session=root) for i in range(8)]
    with django_assert_num_queries(2):
        decisions = state.resolve_root_decisions([child.id for child in children], timezone.now(), frozenset())
    assert all(decision.root_id == root.id and decision.is_cold for decision in decisions.values())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('failure', ['none', 'cancel', 'persist'])
def test_actual_registration_fences_prepared_compression(monkeypatch, failure):
    import asyncio
    import hashlib
    import threading
    from types import SimpleNamespace

    from twicc.agent.base_manager import BaseAgentManager
    from twicc.core.content_storage.codec import encode_cold
    from twicc.core.enums import Provider
    from twicc.core.models import ProcessRun, SessionItem, SessionItemContent
    from twicc.providers import db_writer

    root = make_session('root', archived=True, created_at=timezone.now() - timedelta(days=31))
    text = 'prepared lifecycle compression ' * 100
    row = SessionItemContent.objects.create(sha256=hashlib.sha256(text.encode()).digest(), payload=text.encode(),
                                            codec='plain', raw_size=len(text.encode()), dictionary_provider='codex',
                                            session_ids=[root.id])
    from tests.content_storage_helpers import _public_dictionary

    dictionary = _public_dictionary('codex')
    SessionItem.objects.create(session=root, line_num=1, content='', current_content=row, original_content=row)
    from twicc.core.content_storage.policy import compression_policy_version
    prepared, no_gain = encode_cold(text, dictionary.id, compression_policy_version('codex', dictionary.id))
    assert not no_gain
    started = threading.Event()
    release = threading.Event()
    real_create = ProcessRun.objects.create

    def blocked_create(**kwargs):
        started.set()
        assert release.wait(5)
        if failure == 'persist':
            raise RuntimeError('persist failure')
        return real_create(**kwargs)

    monkeypatch.setattr(ProcessRun.objects, 'create', blocked_create)
    monkeypatch.setattr(db_writer, '_db_write_lock', asyncio.Lock())
    monkeypatch.setattr(db_writer, '_storage_admission_lock', asyncio.Lock())
    monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())
    manager = BaseAgentManager()
    from twicc.agent import registry
    monkeypatch.setattr(registry, 'get_agent_manager_registry', lambda: SimpleNamespace(
        get_active_agents=lambda: [SimpleNamespace(session_id=key) for key in manager._agents],
    ))

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(manager, '_broadcast_session_updated', noop)
    monkeypatch.setattr(manager, '_on_state_change', noop)
    monkeypatch.setattr(manager, '_ensure_timeout_monitor_running', lambda: None)
    agent = SimpleNamespace(session_id=root.id, provider=Provider.CODEX, ephemeral=False,
                            get_pid=lambda: None, start=noop, process_run=None)
    publication = []

    def publish():
        with transaction.atomic():
            decision = state.resolve_root_decision(root.id, timezone.now(), lifecycle.active_session_ids.get())
            if decision.is_cold:
                SessionItemContent.objects.filter(pk=row.id).update(
                    payload=prepared.payload, codec=prepared.codec, dictionary_id=prepared.dictionary_id,
                )
                publication.append(True)
            else:
                publication.append(False)

    async def scenario():
        async def register():
            async with manager._lock:
                await manager._register_and_start(agent, '', resume=True)

        start = asyncio.create_task(register())
        assert await asyncio.to_thread(started.wait, 5)
        assert manager._agents[root.id] is agent
        admitted = db_writer._storage_admission_lock.locked()
        if not admitted:
            release.set()  # Allow cancellation-safe worker cleanup on assertion failure.
        assert admitted, 'Persistent registration must admit before durable writes'

        async def publication_task():
            async with db_writer.storage_admission():
                await db_writer.run_under_db_write_lock(lambda: asyncio.to_thread(publish), content_activity=True)

        task = asyncio.create_task(publication_task())
        await asyncio.sleep(0)
        assert publication == []
        if failure == 'cancel':
            start.cancel()
            await asyncio.sleep(0)
            assert publication == []
        release.set()
        results = await asyncio.wait_for(asyncio.gather(start, task, return_exceptions=True), 5)
        if failure != 'none':
            assert isinstance(results[0], BaseException)
            assert root.id not in manager._agents
        assert publication == [failure == 'persist']

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    root.refresh_from_db()
    if failure != 'persist':
        assert root.content_state_epoch == 5
        row.refresh_from_db()
        assert row.codec == 'plain'


@pytest.mark.django_db(transaction=True)
def test_actual_unarchive_service_keeps_hidden_old_root_cold(monkeypatch):
    import asyncio
    from twicc.core.services.session_update import apply_session_archived_change
    from twicc.providers import db_writer

    root = make_session('root', archived=True, hidden=True, created_at=timezone.now() - timedelta(days=31))
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kwargs: requests.append(target))
    monkeypatch.setattr(db_writer, '_db_write_lock', asyncio.Lock())
    monkeypatch.setattr(db_writer, '_db_writer_stop_event', asyncio.Event())
    asyncio.run(apply_session_archived_change(root, False))
    root.refresh_from_db()
    assert root.is_cold_for_content
    assert root.content_state_epoch == 5
    assert requests == []


def test_full_compute_dates_advance_own_generation_and_verify_plaintext():
    from tests.test_content_extraction import make_session as compute_session, message
    from twicc.core.models import SessionItem
    from twicc.providers.compute_base import BaseSessionCompute

    old = timezone.now() - timedelta(days=60)
    root = compute_session(archived=True, created_at=old, last_updated_at=old,
                           content_state_epoch=4, plaintext_verified_epoch=4)
    SessionItem.objects.create(session=root, line_num=1, content='old contents')
    msg = message(root)
    msg['session_fields']['last_updated_at'] = timezone.now().isoformat()
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'
    root.refresh_from_db()
    assert root.content_state_epoch >= 6  # New binding plus actual lifecycle mutation.
    assert root.plaintext_verified_epoch == root.content_state_epoch


def test_active_ingestion_promotes_reused_compressed_reference():
    import hashlib
    from tests.content_storage_helpers import _public_dictionary
    from twicc.core.content_storage.codec import encode_cold
    from twicc.core.content_storage.ingestion import insert_ingested_items
    from twicc.core.content_storage.policy import compression_policy_version
    from twicc.core.models import SessionItem, SessionItemContent

    root = make_session('root', archived=True, created_at=timezone.now() - timedelta(days=31))
    text = 'active ingestion content ' * 100
    dictionary = _public_dictionary('codex')
    encoded, _ = encode_cold(text, dictionary.id, compression_policy_version('codex', dictionary.id))
    content = SessionItemContent.objects.create(
        sha256=hashlib.sha256(text.encode()).digest(), payload=encoded.payload,
        codec=encoded.codec, dictionary_id=encoded.dictionary_id, raw_size=encoded.raw_size,
        dictionary_provider='codex', session_ids=[],
    )
    token = lifecycle.active_session_ids.set(frozenset({root.id}))
    try:
        with transaction.atomic():
            insert_ingested_items(root, [(SessionItem(session=root, line_num=1, content=text), text)])
    finally:
        lifecycle.active_session_ids.reset(token)
    content.refresh_from_db()
    assert content.codec == 'plain'
    assert bytes(content.payload) == text.encode()


def test_unresolved_full_compute_extracts_plaintext_without_readiness():
    from tests.test_content_extraction import make_session as compute_session, message
    from twicc.core.models import SessionItem
    from twicc.providers.compute_base import BaseSessionCompute

    child = compute_session(type='subagent')
    item = SessionItem.objects.create(session=child, line_num=1, content='unresolved contents')
    msg = message(child)
    msg['session_fields']['last_updated_at'] = timezone.now().isoformat()
    assert BaseSessionCompute.apply_session_complete(msg).outcome == 'applied'
    child.refresh_from_db()
    item.refresh_from_db()
    assert item.current_content.codec == 'plain'
    assert child.plaintext_verified_epoch is None


def test_deferred_update_keyword_never_reaches_query_update(monkeypatch):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    root = make_session('deferred')
    monkeypatch.setattr(lifecycle, 'session_plaintext_metadata', lambda sid: pytest.fail('Unbounded inspection'))
    with CaptureQueriesContext(connection) as queries:
        assert lifecycle.update_lifecycle(Session.objects.filter(pk=root.id),
                                          defer_plaintext_inspection=True, last_updated_at=timezone.now()) == 1
    assert all('defer_plaintext_inspection' not in query['sql'] for query in queries)
    root.refresh_from_db()
    assert root.content_state_epoch == 5
