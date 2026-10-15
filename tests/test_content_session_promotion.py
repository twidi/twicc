"""Hot promotion and substring readiness stay scoped to the requested session."""

import pytest
from django.db import transaction
from django.utils import timezone

from tests import content_storage_helpers, test_content_storage_admission
from tests.test_content_promotion import START, item, snapshot
from tests.test_content_root_state import session
from twicc import content_maintenance_task as maintenance
from twicc.core.content_storage import lifecycle, state
from twicc.core.models import Session, SessionItem

content_row_factory = content_storage_helpers.content_row_factory
writer_state = test_content_storage_admission.writer_state

pytestmark = pytest.mark.django_db


def native_pair():
    root = session('root', storage_incarnation='root-inc', content_storage_version=1)
    child = session('child', type='subagent', parent_session=root,
                    storage_incarnation='child-inc', content_storage_version=1)
    return root, child


def test_root_promotion_leaves_child_only_content_compressed(content_row_factory):
    root, child = native_pair()
    root_row = content_row_factory('root' * 100, True, session_ids=[root.id])
    child_row = content_row_factory('child' * 100, True, session_ids=[child.id])
    item(root, root_row)
    item(child, child_row)
    assert maintenance.promote_root_slice(snapshot(root), START, 100, 8000000).done
    root_row.refresh_from_db()
    child_row.refresh_from_db()
    root.refresh_from_db()
    assert root_row.codec == 'plain'
    assert child_row.codec == 'zstd'
    assert root.plaintext_verified_epoch == root.content_state_epoch


def test_root_verification_does_not_require_child_extraction(content_row_factory):
    root, child = native_pair()
    row = content_row_factory('root', False, session_ids=[root.id])
    item(root, row)
    Session.objects.filter(pk=child.id).update(content_storage_version=None, storage_incarnation=None)
    SessionItem.objects.create(session=child, line_num=1, content='legacy child')
    with transaction.atomic():
        assert state.verify_plaintext_root(state.snapshot_root_storage(root.id))
        state.require_plaintext_search(root.id)


def test_compressed_child_cannot_use_root_certificate(content_row_factory):
    root, child = native_pair()
    row = content_row_factory('child' * 100, True, session_ids=[child.id])
    item(child, row)
    with transaction.atomic():
        observation = state.snapshot_root_storage(root.id)
        Session.objects.filter(pk=root.id).update(plaintext_verified_epoch=observation.epoch)
        with pytest.raises(state.PlaintextSearchUnavailable) as error:
            state.require_plaintext_search(child.id)
        assert error.value.reason == 'warming'
    row.refresh_from_db()
    assert row.codec == 'zstd'


def test_plain_child_does_not_wait_for_root_certificate(content_row_factory):
    root, child = native_pair()
    row = content_row_factory('child', False, session_ids=[child.id])
    item(child, row)
    with transaction.atomic():
        state.snapshot_root_storage(root.id)
        assert state.require_plaintext_search(child.id).root_id == root.id
    root.refresh_from_db()
    assert root.plaintext_verified_epoch is None


def test_root_shared_content_warms_only_shared_child_references(content_row_factory):
    root, child = native_pair()
    shared = content_row_factory('shared' * 100, True, session_ids=[root.id, child.id])
    own = content_row_factory('child only' * 100, True, session_ids=[child.id])
    item(root, shared)
    item(child, shared, own)
    maintenance.promote_root_slice(snapshot(root), START, 100, 8000000)
    shared.refresh_from_db()
    own.refresh_from_db()
    assert shared.codec == 'plain'
    assert own.codec == 'zstd'


@pytest.mark.django_db(transaction=True)
def test_plain_child_lifecycle_change_fences_without_demand(monkeypatch):
    root, child = native_pair()
    before = snapshot(root)
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kwargs: requests.append(target))
    lifecycle.update_lifecycle(Session.objects.filter(pk=child.id), last_started_at=timezone.now())
    root.refresh_from_db()
    assert root.content_state_epoch > before.epoch
    assert requests == []


def test_explicit_child_promotion_has_no_subtree_or_root_certificate(content_row_factory):
    root, child = native_pair()
    leaf = session('leaf', type='subagent', parent_session=child,
                   storage_incarnation='leaf-inc', content_storage_version=1)
    rows = []
    for owner in (root, child, leaf):
        row = content_row_factory(owner.id * 100, True, session_ids=[owner.id])
        item(owner, row)
        rows.append(row)
    with transaction.atomic():
        observation = state.snapshot_session_promotion(child.id)
    assert maintenance.promote_root_slice(observation, START, 100, 8000000).done
    for row in rows:
        row.refresh_from_db()
    assert [row.codec for row in rows] == ['zstd', 'plain', 'zstd']
    root.refresh_from_db()
    child.refresh_from_db()
    assert root.plaintext_verified_epoch is child.plaintext_verified_epoch is None
    with transaction.atomic():
        state.require_plaintext_search(child.id)


@pytest.mark.parametrize('mutation', ['target_aba', 'root_aba', 'reparent', 'ancestor_move', 'reference'])
def test_target_promotion_rejects_stale_identity_or_native_tree(content_row_factory, mutation):
    root, child = native_pair()
    leaf = session('leaf', type='subagent', parent_session=child,
                   storage_incarnation='leaf-inc', content_storage_version=1)
    row = content_row_factory('leaf data' * 100, True, session_ids=[leaf.id])
    item(leaf, row)
    with transaction.atomic():
        observation = state.snapshot_session_promotion(leaf.id)
    batch = maintenance.prepare_promotion_batch(observation, START)
    if mutation == 'target_aba':
        leaf.delete()
        replacement = session('leaf', type='subagent', parent_session=child,
                              storage_incarnation='replacement-leaf', content_storage_version=1)
        item(replacement, row)
    elif mutation == 'root_aba':
        root.delete()
        replacement_root = session('root', storage_incarnation='replacement-root',
                                   content_state_epoch=observation.epoch, content_storage_version=1)
        replacement_child = session('child', type='subagent', parent_session=replacement_root,
                                    storage_incarnation='child-inc', content_storage_version=1)
        replacement_leaf = session('leaf', type='subagent', parent_session=replacement_child,
                                   storage_incarnation='leaf-inc', content_storage_version=1)
        item(replacement_leaf, row)
        root = replacement_root
    elif mutation in ('reparent', 'ancestor_move'):
        new = session('new', storage_incarnation='new-inc', content_storage_version=1)
        moved = leaf if mutation == 'reparent' else child
        lifecycle.update_lifecycle(Session.objects.filter(pk=moved.id), parent_session_id=new.id)
    else:
        with transaction.atomic():
            state.invalidate_roots([root.id], promotion_targets=[leaf.id])
    assert maintenance.apply_promotion_batch(batch, frozenset(), pass_verified=True) == START
    row.refresh_from_db()
    assert row.codec == 'zstd'
    root.refresh_from_db()
    assert root.plaintext_verified_epoch is None


@pytest.mark.parametrize('slot', ['current', 'original'])
def test_child_contains_checks_both_slots_without_payload_or_wakeup(content_row_factory, slot, monkeypatch):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    root, child = native_pair()
    plain = content_row_factory('plain', False, session_ids=[child.id])
    compressed = content_row_factory('compressed' * 100, True, session_ids=[child.id])
    item(child, compressed if slot == 'current' else plain, compressed if slot == 'original' else None)
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kwargs: requests.append(target))
    with (transaction.atomic(), CaptureQueriesContext(connection) as queries,
          pytest.raises(state.PlaintextSearchUnavailable) as error):
        state.require_plaintext_search(child.id)
    assert error.value.reason == 'warming'
    assert requests == []
    assert all('"payload"' not in query['sql'] for query in queries)
    assert all(not query['sql'].startswith(('UPDATE', 'INSERT')) for query in queries)


@pytest.mark.django_db(transaction=True)
def test_child_reference_binding_fences_without_plaintext_demand(content_row_factory, monkeypatch):
    from twicc.core.content_storage.references import bind_persisted_items
    from twicc.core.content_storage.types import ContentBinding

    root, child = native_pair()
    before = snapshot(root)
    row = content_row_factory('child', False, session_ids=[])
    stored = item(child, row)
    requests = []
    monkeypatch.setattr(lifecycle, 'request_root_promotion', lambda target, **kwargs: requests.append(target))
    with transaction.atomic():
        bind_persisted_items([ContentBinding(stored.id, child.id, row.id, None)])
    root.refresh_from_db()
    assert root.content_state_epoch > before.epoch
    assert requests == []


@pytest.mark.django_db(transaction=True)
def test_coordinator_recovers_only_root_then_handles_explicit_child_resume(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings,
):
    import asyncio
    from twicc.agent.registry import get_agent_manager_registry

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents', list)
    monkeypatch.setattr(maintenance, 'ROW_BUDGET', 1)
    root, child = native_pair()
    sibling = session('sibling', type='subagent', parent_session=root,
                      storage_incarnation='sibling-inc', content_storage_version=1,
                      content_state_epoch=0)
    rows = []
    for owner in (root, child, sibling):
        row = content_row_factory(owner.id * 100, True, session_ids=[owner.id])
        item(owner, row)
        rows.append(row)
    snapshot(root)
    targets = []
    original = maintenance.prepare_promotion_batch

    def prepare(observation, cursor, *args):
        targets.append(getattr(observation, 'session_id', observation.root_id))
        return original(observation, cursor, *args)

    monkeypatch.setattr(maintenance, 'prepare_promotion_batch', prepare)
    phases = []

    async def wait(stop, wake, timeout):
        phases.append(len(targets))
        if len(phases) == 1:
            assert set(targets) == {root.id}

            def resume(active):
                lifecycle.update_lifecycle(Session.objects.filter(pk=child.id), last_started_at=timezone.now())

            await maintenance._apply(resume)
            await asyncio.wait_for(wake.wait(), 1)
        else:
            stop.set()

    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event()))
    assert set(targets) == {root.id, child.id}
    for row in rows:
        row.refresh_from_db()
    assert [row.codec for row in rows] == ['plain', 'plain', 'zstd']
    assert sibling.id not in maintenance.pending_root_page()


def test_plain_root_contains_survives_child_only_epoch_invalidation(content_row_factory):
    root, child = native_pair()
    row = content_row_factory('root data', False, session_ids=[root.id])
    item(root, row)
    with transaction.atomic():
        assert state.verify_plaintext_root(state.snapshot_root_storage(root.id))
    lifecycle.update_lifecycle(Session.objects.filter(pk=child.id), last_started_at=timezone.now())
    root.refresh_from_db()
    assert root.plaintext_verified_epoch == root.content_state_epoch
    with transaction.atomic():
        assert state.require_plaintext_search(root.id).root_id == root.id
    root.refresh_from_db()
    # Lifecycle preservation retains readiness; read validation does not write it.
    assert root.plaintext_verified_epoch == root.content_state_epoch


def test_compressed_root_cannot_use_stale_certificate_fallback(content_row_factory):
    root, _ = native_pair()
    row = content_row_factory('root data' * 100, True, session_ids=[root.id])
    item(root, row)
    with transaction.atomic():
        observation = state.snapshot_root_storage(root.id)
        Session.objects.filter(pk=root.id).update(plaintext_verified_epoch=observation.epoch - 1)
        with pytest.raises(state.PlaintextSearchUnavailable) as error:
            state.require_plaintext_search(root.id)
    assert error.value.reason == 'warming'


@pytest.mark.django_db(transaction=True)
def test_recovery_skips_cold_root_with_active_child_and_advances_to_hot_root(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings,
):
    import asyncio
    from datetime import timedelta
    from types import SimpleNamespace
    from twicc.agent.registry import get_agent_manager_registry

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    monkeypatch.setattr(maintenance, 'TREE_PAGE_SIZE', 1)
    root, child = native_pair()
    Session.objects.filter(pk=root.id).update(archived=True, created_at=timezone.now() - timedelta(days=40))
    hot = session('z-hot', storage_incarnation='hot-inc', content_storage_version=1, content_state_epoch=0)
    rows = []
    for owner in (root, child, hot):
        row = content_row_factory(owner.id * 100, True, session_ids=[owner.id])
        item(owner, row)
        rows.append(row)
    snapshot(root)
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents',
                        lambda: [SimpleNamespace(session_id=child.id)])

    async def wait(stop, wake, timeout):
        stop.set()

    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event()))
    for row in rows:
        row.refresh_from_db()
    assert [row.codec for row in rows] == ['zstd', 'zstd', 'plain']
    # Explicit child demand still uses admitted active-tree protection.
    with transaction.atomic():
        observation = maintenance._snapshot(child.id, frozenset({child.id}))
    batch = maintenance.prepare_promotion_batch(observation, START)
    assert maintenance.apply_promotion_batch(batch, frozenset({child.id}), pass_verified=True).done
    rows[0].refresh_from_db()
    rows[1].refresh_from_db()
    assert rows[0].codec == 'zstd' and rows[1].codec == 'plain'


@pytest.mark.parametrize('command_name', ['content', 'messages'])
@pytest.mark.parametrize('compressed', [False, True])
def test_public_child_contains_uses_target_metadata_while_root_is_unextracted(
    content_row_factory, command_name, compressed, capsysbinary,
):
    import orjson
    import typer
    from tests.test_content_contains_gate import add_message, make_session
    from twicc.cli import session as cli_session

    root = make_session('legacy-root')
    child = make_session('plain-child', type='subagent', parent_session=root,
                         storage_incarnation='child-inc', content_storage_version=1)
    text = orjson.dumps({'type': 'user', 'message': {'role': 'user', 'content': [
        {'type': 'text', 'text': 'needle É'},
    ]}}).decode()
    row = content_row_factory(text, compressed, provider='claude_code', session_ids=[child.id])
    add_message(child, 1, 'needle É', row=row)
    command = getattr(cli_session, command_name)
    if compressed:
        with pytest.raises(typer.Exit):
            command(child.id, contains=['needle'])
        assert 'warming' in capsysbinary.readouterr().err.decode()
        # An ordinary requested-line read remains available and temporary.
        cli_session.content(child.id, range_str='1')
        result = orjson.loads(capsysbinary.readouterr().out)
    else:
        command(child.id, contains=['needle'])
        result = orjson.loads(capsysbinary.readouterr().out)
    assert result['pagination']['total'] == 1
    assert [entry['line_num'] for entry in result['items']] == [1]
    row.refresh_from_db()
    root.refresh_from_db()
    assert row.codec == ('zstd' if compressed else 'plain')
    assert root.content_storage_version is root.storage_incarnation is None
    assert root.plaintext_verified_epoch is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('barrier', ['filter_to_snapshot', 'snapshot_to_apply'])
def test_recovery_rechecks_persisted_hot_policy_across_admission_barriers(
    content_row_factory, writer_state, compute_executor_started, monkeypatch, settings, barrier,
):
    import asyncio
    from datetime import timedelta
    from types import SimpleNamespace
    from twicc.agent.registry import get_agent_manager_registry

    settings.SESSION_DIRS_CLEANUP_ENABLED = False
    root, child = native_pair()
    now = [timezone.now()]
    Session.objects.filter(pk=root.id).update(archived=True, created_at=now[0] - timedelta(days=14))
    row = content_row_factory('root data' * 100, True, session_ids=[root.id])
    item(root, row)
    snapshot(root)
    monkeypatch.setattr(maintenance.timezone, 'now', lambda: now[0])
    monkeypatch.setattr(get_agent_manager_registry(), 'get_active_agents',
                        lambda: [SimpleNamespace(session_id=child.id)])
    crossed = []
    name = 'recovery_promotion_targets' if barrier == 'filter_to_snapshot' else 'prepare_promotion_batch'
    original = getattr(maintenance, name)

    def cross_threshold(*args):
        result = original(*args)
        if not crossed:
            crossed.append(True)
            # Elapsed time changes persisted cold policy without a metadata write or epoch change.
            now[0] += timedelta(days=2)
        return result

    monkeypatch.setattr(maintenance, name, cross_threshold)

    async def wait(stop, wake, timeout):
        stop.set()

    monkeypatch.setattr(maintenance, '_wait', wait)
    asyncio.run(maintenance.start_content_maintenance_task(asyncio.Event()))
    assert crossed == [True]
    row.refresh_from_db()
    root.refresh_from_db()
    assert row.codec == 'zstd'
    assert root.plaintext_verified_epoch is None
