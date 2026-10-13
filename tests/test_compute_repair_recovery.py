"""Bounded fair recovery state and writer admission fences."""
import asyncio
from types import SimpleNamespace

import pytest

from twicc.providers.compute_repair_journal import RepairObservation, RepairSnapshot
from twicc.providers import db_writer


def scheduler(clock=None):
    from twicc.providers.compute_repair_recovery import RepairRecoveryScheduler
    return RepairRecoveryScheduler(clock=clock or (lambda: 0.0))


def row(number, session_id=None, generation=1):
    return RepairSnapshot(RepairObservation(number, session_id or str(number), 'incarnation', generation),
                          frozenset(), frozenset())


def test_missing_scheduler_contract():
    state = scheduler()
    state.begin_sweep(20)
    assert state.page_request() == (0, 20, 16)


def test_excluded_full_page_releases_capacity_and_preserves_cursor():
    state = scheduler()
    state.begin_sweep(17)
    state.page_request()
    state.observe_page(tuple(row(n, 'pending') for n in range(1, 17)), excluded_session_ids=frozenset())
    assert state.page_request() is None
    assert state.next_candidate(frozenset({'pending'})) is None
    assert state.page_request() == (16, 17, 16)
    state.observe_page((row(17),), excluded_session_ids=frozenset({'pending'}))
    assert state.next_candidate(frozenset()).journal_id == 17


def test_entirely_excluded_short_page_completes_sweep():
    now = [0.0]
    state = scheduler(lambda: now[0])
    state.begin_sweep(9)
    state.page_request()
    state.observe_page((row(9, 'pending'),), excluded_session_ids=frozenset({'pending'}))
    assert state.page_request() is None
    state.begin_sweep(10)
    assert state.page_request() is None
    now[0] = 1
    state.begin_sweep(10)
    assert state.page_request() == (0, 10, 16)


def test_failure_backoff_does_not_starve_later_rows_or_cap_failure_keys():
    now = [0.0]
    state = scheduler(lambda: now[0])
    state.begin_sweep(4097)
    for start in range(1, 4097, 16):
        assert state.page_request()[2] == 16
        state.observe_page(tuple(row(n) for n in range(start, start + 16)), excluded_session_ids=frozenset())
        for number in range(start, start + 16):
            observation = state.next_candidate(frozenset())
            assert observation.journal_id == number
            state.mark_admitted(observation)
            state.settle_attempt(observation, 'failed', failed=True)
    state.page_request()
    state.observe_page((row(4097),), excluded_session_ids=frozenset())
    assert state.next_candidate(frozenset()).journal_id == 4097
    state.mark_admitted(row(4097).observation)
    state.settle_attempt(row(4097).observation, 'repaired')
    assert len(state._retries) == 4096
    assert min(record.retry_deadline for record in state._retries.values()) >= 1
    now[0] = .5
    state.begin_sweep(4097)
    assert state.page_request() is None


def test_denied_candidate_remains_and_new_generation_supersedes():
    now = [0.0]
    state = scheduler(lambda: now[0])
    state.begin_sweep(1)
    state.page_request()
    state.observe_page((row(1),), excluded_session_ids=frozenset())
    assert state.next_candidate(frozenset()) == state.next_candidate(frozenset())
    now[0] = 1
    state.begin_sweep(1)
    state.page_request()
    state.observe_page((row(1, generation=2),), excluded_session_ids=frozenset())
    assert state.next_candidate(frozenset()).generation == 2


def test_close_clears_private_state_and_prohibits_retry():
    state = scheduler()
    state.begin_sweep(1)
    state.page_request()
    state.observe_page((row(1),), excluded_session_ids=frozenset())
    observation = state.next_candidate(frozenset())
    state.mark_admitted(observation)
    state.close()
    state.settle_attempt(observation, 'failed', failed=True)
    state.begin_sweep(100)
    assert state.page_request() is None
    assert state.next_candidate(frozenset()) is None
    assert not state._retries


def test_pending_admission_recheck_defers_without_sql(monkeypatch):
    async def scenario():
        state = scheduler()
        state.begin_sweep(1)
        state.page_request()
        state.observe_page((row(1, 'pending'),), excluded_session_ids=frozenset())
        monkeypatch.setattr(db_writer, '_repair_recovery', state, raising=False)
        monkeypatch.setattr(db_writer, '_pending_compute', None)
        async def grant(factory, **kwargs):
            db_writer._pending_compute = SimpleNamespace(message={'session_id': 'pending'})
            await factory()
            return True
        async def forbidden(*args, **kwargs):
            pytest.fail('Excluded target reaches SQL')
        monkeypatch.setattr(db_writer, '_background_turn', grant)
        monkeypatch.setattr(db_writer, 'run_compute_sync', forbidden)
        assert await db_writer._advance_compute_repair()
        assert state.next_candidate(frozenset()) is None
    asyncio.run(scenario())


def test_retry_escalates_to_sixty_seconds_and_generation_clears_it():
    now = [0.0]
    state = scheduler(lambda: now[0])
    observation = row(1).observation
    for attempt in range(10):
        state.settle_attempt(observation, 'failed', failed=True)
        retry = state._retries[1]
        assert retry.retry_deadline - now[0] == min(60, 2 ** attempt)
        now[0] = retry.retry_deadline
    state.begin_sweep(1)
    state.page_request()
    state.observe_page((row(1, generation=2),), excluded_session_ids=frozenset())
    assert not state._retries
    assert state.next_candidate(frozenset()).generation == 2


def test_completed_page_prunes_only_confirmed_missing_failed_identities():
    state = scheduler()
    for number in (1, 2, 17, 100):
        state.settle_attempt(row(number).observation, 'failed', failed=True)
    state.begin_sweep(17)
    state.page_request()
    state.observe_page((row(2),), excluded_session_ids=frozenset())
    assert set(state._retries) == {2}


def test_finite_upper_id_excludes_later_insertions():
    state = scheduler()
    state.begin_sweep(16)
    assert state.page_request() == (0, 16, 16)
    state.observe_page(tuple(row(n) for n in range(1, 17)), excluded_session_ids=frozenset())
    assert state.page_request() is None
    for _ in range(16):
        observation = state.next_candidate(frozenset())
        state.mark_admitted(observation)
        state.settle_attempt(observation, 'repaired')
    assert state.page_request() is None


@pytest.mark.parametrize('stage', ['begin', 'prepare', 'publish', 'activity', 'final', 'terminal'])
def test_all_pending_stages_exclude_all_incarnations(monkeypatch, stage):
    async def scenario():
        state = scheduler()
        state.begin_sweep(2)
        state.page_request()
        state.observe_page((row(1, 'pending'), row(2, 'other')), excluded_session_ids=frozenset())
        monkeypatch.setattr(db_writer, '_repair_recovery', state, raising=False)
        monkeypatch.setattr(db_writer, '_pending_compute', SimpleNamespace(message={'session_id': 'pending'}, stage=stage))
        calls = []
        async def grant(factory, **kwargs):
            await factory()
            return True
        async def run(function, observation):
            calls.append(observation.session_id)
            return 'repaired'
        monkeypatch.setattr(db_writer, '_background_turn', grant)
        monkeypatch.setattr(db_writer, 'run_compute_sync', run)
        assert await db_writer._advance_compute_repair()
        assert calls == ['other']
        assert not state._queued
    asyncio.run(scenario())


def test_foreground_demand_denies_recovery_without_consuming_candidate(monkeypatch):
    from twicc.providers.write_coordinator import WriterCoordinator
    async def scenario():
        state = scheduler()
        state.begin_sweep(1)
        state.page_request()
        state.observe_page((row(1),), excluded_session_ids=frozenset())
        coordinator = WriterCoordinator()
        demand = coordinator.foreground_demand()
        monkeypatch.setattr(db_writer, '_repair_recovery', state)
        monkeypatch.setattr(db_writer, '_write_coordinator', coordinator)
        monkeypatch.setattr(db_writer, '_pending_compute', None)
        try:
            assert not await db_writer._advance_compute_repair()
            assert state.next_candidate(frozenset()) == row(1).observation
        finally:
            demand.release()
    asyncio.run(scenario())


def test_empty_maximum_proves_old_failed_rows_disappeared():
    state = scheduler()
    state.settle_attempt(row(100).observation, 'failed', failed=True)
    state.begin_sweep(0)
    assert not state._retries


@pytest.mark.parametrize('rediscovered_generation', [1, 2])
def test_page_completion_during_admitted_failure_preserves_backoff_and_later_candidates(rediscovered_generation):
    async def scenario():
        now = [0.0]
        state = scheduler(lambda: now[0])
        state.begin_sweep(1)
        state.page_request()
        state.observe_page((row(1),), excluded_session_ids=frozenset())
        observation = state.next_candidate(frozenset())  # Denied grant retains this candidate.
        now[0] = 1.0
        state.begin_sweep(2)
        assert state.page_request() == (0, 2, 15)
        admitted, page_completed = asyncio.Event(), asyncio.Event()

        async def page():
            await admitted.wait()
            state.observe_page((row(1, generation=rediscovered_generation), row(2)),
                               excluded_session_ids=frozenset())
            page_completed.set()

        async def attempt():
            state.mark_admitted(observation)
            admitted.set()
            await page_completed.wait()
            state.settle_attempt(observation, 'failed', failed=True)

        await asyncio.gather(page(), attempt())
        candidate = state.next_candidate(frozenset())
        if rediscovered_generation == 1:
            assert state._retries[1].retry_deadline == 2.0
            assert candidate.journal_id == 2
            state.mark_admitted(candidate)
            state.settle_attempt(candidate, 'repaired')
            assert state.next_candidate(frozenset()) is None
            now[0] = 2.0
            state.begin_sweep(2)
            state.page_request()
            state.observe_page((row(1),), excluded_session_ids=frozenset())
            assert state.next_candidate(frozenset()) == observation
        else:
            assert candidate == row(1, generation=2).observation
            assert not state._retries
            state.mark_admitted(candidate)
            assert state.next_candidate(frozenset()).journal_id == 2
    asyncio.run(scenario())
