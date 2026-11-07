"""Ordered Claude evidence and transactional coordinator integration."""
from contextvars import copy_context

import pytest
from django.db import transaction

from twicc.core.enums import Provider
from twicc.core.models import Project, Session
from twicc.providers.claude_code import title_publication as publication
from twicc.providers.claude_code.compute import extract_title_observations
from twicc.providers.claude_code.title_sync import SourceCursor, TitleObservation, TitleSyncCoordinator

pytestmark = pytest.mark.django_db(transaction=True)


def test_closed_commit_callback_context_starts_fresh_stage():
    coordinator = TitleSyncCoordinator()
    inherited = None
    with coordinator.staged_state() as stage:
        coordinator.accept_decision('s', 'first')
        with transaction.atomic():
            transaction.on_commit(stage.commit)
            def capture():
                nonlocal inherited
                inherited = copy_context()
            transaction.on_commit(capture)
    with coordinator.staged_state() as stage:
        latest = coordinator.accept_decision('s', 'latest')
        stage.commit()
    assert inherited.run(coordinator.current_decision, 's') == latest
    def mutate():
        with coordinator.staged_state() as stage:
            decision = coordinator.accept_decision('s', 'next')
            stage.commit()
        return decision
    assert inherited.run(mutate) == coordinator.current_decision('s')


def command(value='requested'):
    return {'type': 'user', 'uuid': 'command', 'message': {'content':
            f'<command-name>/rename</command-name><command-args>{value}</command-args>'}}


def test_effective_result_requires_rename_ancestry_and_actual_title():
    cursor = SourceCursor('s', 'disk', 0, 2)
    result = {'type': 'user', 'uuid': 'result', 'parentUuid': 'command', 'message': {
        'content': '<local-command-stdout>Session renamed to: actual (2)</local-command-stdout>'}}
    assert extract_title_observations(result, cursor, parent=command())[0].title == 'actual (2)'
    assert extract_title_observations(result, cursor) == ()
    result['message']['content'] = '<local-command-stdout>Done</local-command-stdout>'
    assert extract_title_observations(result, cursor, parent=command()) == ()


@pytest.fixture
def managed(monkeypatch):
    coordinator = TitleSyncCoordinator()
    monkeypatch.setattr(publication, '_coordinator', coordinator)
    session = Session.objects.create(id='s', provider=Provider.CLAUDE_CODE,
                                    project=Project.objects.create(id='p'), title='B', title_origin='auto')
    with publication.title_state_transaction():
        publication.attach_claude_process_locked('s', 'process', hybrid=False)
        session.refresh_from_db()
        decision = coordinator.accept_decision('s', 'B')
        coordinator.start_dispatch(decision, SourceCursor('s', session.storage_incarnation, 0, 0))
    return session, coordinator, decision


def observation(session, line, value, kind='title', parent=None, message=None):
    return TitleObservation(SourceCursor('s', session.storage_incarnation, 0, line), 's', kind,
                            value, message, parent)


def test_ack_and_title_update_rollback_together(managed):
    session, coordinator, decision = managed
    with publication.title_state_transaction():
        publication.apply_claude_observations_locked((observation(session, 1, 'B'), observation(session, 2, 'A')))
    with pytest.raises(RuntimeError), publication.title_state_transaction():
        publication.acknowledge_claude_title_locked(decision, True, observation(session, 2, 'A').cursor)
        assert Session.objects.get(pk='s').title == 'A'
        raise RuntimeError('SQL phase failure')
    assert coordinator.current_decision('s') == decision
    assert Session.objects.get(pk='s').title == 'B'
    with publication.title_state_transaction():
        assert publication.acknowledge_claude_title_locked(decision, True, observation(session, 2, 'A').cursor) == ('s',)
    assert Session.objects.get(pk='s').title == 'A'


def test_attachment_baseline_blocks_historical_command(monkeypatch):
    coordinator = TitleSyncCoordinator()
    monkeypatch.setattr(publication, '_coordinator', coordinator)
    session = Session.objects.create(id='s', provider=Provider.CLAUDE_CODE, last_line=10,
                                    project=Project.objects.create(id='p'), title='B', title_origin='user')
    with publication.title_state_transaction():
        publication.attach_claude_process_locked('s', 'p', hybrid=True)
        decision = coordinator.accept_decision('s', 'B')
        assert publication.apply_claude_observations_locked((
            observation(session, 1, 'A', 'rename_command', message='old'),
            observation(session, 2, 'A', 'command_success', parent='old'),
        )) == ()
    assert coordinator.current_decision('s') == decision
    assert Session.objects.get(pk='s').title == 'B'


def test_predecision_command_on_disk_ingested_during_dispatch(managed):
    session, coordinator, _ = managed
    cursor = SourceCursor('s', session.storage_incarnation, session.history_epoch, 5)
    with publication.title_state_transaction():
        assert publication.protect_claude_acceptance_boundary_locked(cursor)
        newer = coordinator.accept_decision('s', 'C')
        Session.objects.filter(pk='s').update(title='C')
    # The file boundary includes this outcome, but ingestion previously lagged.
    with publication.title_state_transaction():
        publication.apply_claude_observations_locked((
            observation(session, 3, 'A', 'rename_command', message='old'),
            observation(session, 4, 'A', 'command_success', parent='old'),
        ))
    assert coordinator.current_decision('s') == newer
    assert Session.objects.get(pk='s').title == 'C'


def test_nested_savepoint_restores_overlay(managed):
    _, coordinator, initial = managed
    with publication.title_state_transaction():
        with pytest.raises(RuntimeError), publication.title_state_transaction():
            coordinator.accept_decision('s', 'bad')
            raise RuntimeError('savepoint rollback')
        assert coordinator.current_decision('s') == initial
    assert coordinator.current_decision('s') == initial


def test_old_replayed_evidence_never_completes_hybrid_waiter(managed, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    session, coordinator, _ = managed
    with publication.title_state_transaction():
        coordinator.attach_process('s', 'hybrid', hybrid=True)
        coordinator.protect_acceptance_boundary(observation(session, 10, 'B').cursor)
    seen = []
    async def scenario():
        agent = SimpleNamespace(on_title_evidence=lambda records: seen.extend(records))
        process = publication.ActiveTitleProcess('hybrid', agent, True, True)
        worker = SimpleNamespace(hooks=SimpleNamespace(resolve_process=lambda sid: process),
                                 _loop=asyncio.get_running_loop(), _closed=False)
        monkeypatch.setattr(publication, '_worker', worker)
        def apply():
            with publication.title_state_transaction():
                publication.apply_claude_observations_locked((
                    observation(session, 2, 'B', 'command_success', parent='old'),
                ))
        await asyncio.to_thread(apply)
        completed = asyncio.Event()
        asyncio.get_running_loop().call_soon(completed.set)
        await completed.wait()
    asyncio.run(scenario())
    assert seen == []


def test_storage_replacement_drops_old_observations(managed):
    session, coordinator, _ = managed
    old = observation(session, 20, 'old')
    Session.objects.filter(pk='s').update(history_epoch=1)
    with publication.title_state_transaction():
        assert publication.apply_claude_observations_locked((old,)) == ()
        assert not publication.protect_claude_acceptance_boundary_locked(old.cursor)
    assert Session.objects.get(pk='s').title == 'B'


def test_foreign_source_cannot_change_managed_target(managed):
    session, _, _ = managed
    other = Session.objects.create(id='other', project=session.project, file_path='other.jsonl', storage_incarnation='foreign', history_epoch=0)
    record = TitleObservation(SourceCursor(other.id, 'foreign', 0, 100), 's', 'title', 'foreign title', None, None)
    with publication.title_state_transaction():
        assert publication.apply_claude_observations_locked((record,)) == ()
    assert Session.objects.get(pk='s').title == 'B'


def test_split_command_parent_reads_existing_storage(managed):
    import orjson
    from twicc.core.models import SessionItem
    from twicc.providers.claude_code.compute import get_compute
    session, _, _ = managed
    SessionItem.objects.create(session=session, line_num=1, content=orjson.dumps(command()).decode())
    result = {'type': 'user', 'uuid': 'result', 'parentUuid': 'command', 'message': {
        'content': '<local-command-stdout>Session renamed to actual</local-command-stdout>'}}
    records = get_compute().extract_provider_title_observations(result, session, 2, {})
    assert len(records) == 1
    assert records[0].kind == 'command_success'
    assert records[0].title == 'actual'


def test_unmanaged_history_applies_only_final_title(monkeypatch):
    from twicc.core.models import TimelineEvent
    from tests.timeline_helpers import mark_known
    monkeypatch.setattr(publication, '_coordinator', TitleSyncCoordinator())
    session = Session.objects.create(id='s', storage_incarnation='disk', project=Project.objects.create(id='p'),
                                    title='start', title_origin='user')
    mark_known('s', 'p')
    with publication.title_state_transaction():
        publication.apply_claude_observations_locked((observation(session, 1, 'middle'), observation(session, 2, 'end')))
    assert Session.objects.get(pk='s').title == 'end'
    assert list(TimelineEvent.objects.filter(kind='title.changed').values_list('data', flat=True)) == [
        {'source': 'agent', 'new': 'end', 'old': None}]


def test_overflow_replay_applies_later_manual_outcome(managed):
    import orjson
    from twicc.core.models import SessionItem
    session, coordinator, _ = managed
    # Force the small buffer to exercise the production replay path deterministically.
    coordinator.EVIDENCE_LIMIT = 2
    rows = [command(str(n)) | {'uuid': f'c{n}'} for n in range(1, 5)]
    rows.append({'type': 'user', 'uuid': 'result', 'parentUuid': 'c4', 'message': {
        'content': '<local-command-stdout>Session renamed to Manual</local-command-stdout>'}})
    for line, raw in enumerate(rows, 1):
        SessionItem.objects.create(session=session, line_num=line, content=orjson.dumps(raw).decode())
    from twicc.providers.claude_code.compute import get_compute
    parents = {}
    commands = tuple(r for n, raw in enumerate(rows[:4], 1)
                     for r in get_compute().extract_provider_title_observations(raw, session, n, parents))
    with publication.title_state_transaction():
        publication.apply_claude_observations_locked(commands)
        result = get_compute().extract_provider_title_observations(rows[-1], session, 5, {})
        assert publication.apply_claude_observations_locked(result) == ('s',)
    assert Session.objects.get(pk='s').title == 'Manual'
    assert coordinator.current_decision('s') is None
    assert coordinator.unresolved_evidence_count('s') <= 2


def test_real_provider_baseline_allows_acknowledged_noop(managed):
    session, coordinator, _ = managed
    with publication.title_state_transaction():
        coordinator.retire_process('s', 'process')
        publication.attach_claude_process_locked('s', 'new', hybrid=False, baseline=observation(session, 0, 'B'))
        decision = coordinator.accept_decision('s', 'B')
        boundary = observation(session, 0, 'B').cursor
        assert coordinator.start_dispatch(decision, boundary)
        assert coordinator.acknowledge(decision, success=True, boundary=boundary).confirmed == (decision.token,)


def test_legacy_cursorless_entry_cannot_bypass_managed_observer(managed, monkeypatch):
    from twicc.providers.claude_code import titles
    from twicc.providers.claude_code.compute import get_compute
    correction = []
    monkeypatch.setattr(titles, 'rename_session_in_jsonl', lambda *args: correction.append(args))
    assert get_compute().apply_session_title('s', 'A') is False
    assert Session.objects.get(pk='s').title == 'B'
    assert correction == []


def test_old_epoch_ack_cannot_apply_buffered_title(managed):
    session, _, decision = managed
    with publication.title_state_transaction():
        publication.apply_claude_observations_locked((observation(session, 1, 'B'), observation(session, 2, 'A')))
    Session.objects.filter(pk='s').update(history_epoch=1)
    with publication.title_state_transaction():
        assert publication.acknowledge_claude_title_locked(decision, True, observation(session, 2, 'A').cursor) == ()
    assert Session.objects.get(pk='s').title == 'B'


def test_silent_nested_rollback_restores_sql_decision_floor_and_protection(managed):
    session, coordinator, original = managed
    with publication.title_state_transaction():
        with publication.title_state_transaction():
            Session.objects.filter(pk='s').update(title='discarded')
            coordinator.protect_acceptance_boundary(observation(session, 10, 'discarded').cursor)
            coordinator.accept_decision('s', 'discarded')
            transaction.set_rollback(True)
        assert Session.objects.get(pk='s').title == 'B'
        assert coordinator.current_decision('s') == original
    assert Session.objects.get(pk='s').title == 'B'
    assert coordinator.current_decision('s') == original
    # An inner floor of ten would reject this echo after the outer commit.
    with publication.title_state_transaction():
        publication.apply_claude_observations_locked((observation(session, 1, 'B'),))
        assert publication.acknowledge_claude_title_locked(original, True, observation(session, 1, 'B').cursor) == ()
    assert coordinator.current_decision('s') is None
    # An inner protection of ten would suppress this explicit outcome.
    with publication.title_state_transaction():
        publication.apply_claude_observations_locked((
            observation(session, 2, 'C', 'rename_command', message='later'),
            observation(session, 3, 'C', 'command_success', parent='later'),
        ))
    assert Session.objects.get(pk='s').title == 'C'
