"""Publication scheduling uses controlled transports, never transcript writes."""

import asyncio
from types import SimpleNamespace

import pytest

from twicc.providers.claude_code.title_publication import (
    ActiveTitleProcess, PublicationHooks, TitlePublicationWorker,
)
from twicc.providers.claude_code.title_sync import SourceCursor, TitleSyncCoordinator


class Harness:
    def __init__(self, *, ready=True, hybrid=False):
        self.coordinator = TitleSyncCoordinator()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.sent = []
        self.concurrent = 0
        self.maximum = 0
        self.process = ActiveTitleProcess("process", SimpleNamespace(_client=self), ready, hybrid)
        self.boundary = SourceCursor("session", "storage", 0, 1)
        self.failures = 0
        self.ingests = 0
        self.finished = []
        with self.coordinator.staged_state() as stage:
            self.coordinator.attach_process("session", "process", hybrid=hybrid)
            stage.commit()
        self.worker = TitlePublicationWorker(self.coordinator, PublicationHooks(
            resolve_process=lambda sid: self.process,
            prepare_dispatch=self.prepare,
            ingest_available=self.ingest,
            acknowledge=self.acknowledge,
            cancel_dispatch_before_delivery=self.cancel_dispatch_before_delivery,
            finish_process=self.finish,
            reconcile_inactive=self.inactive,
        ))

    def decision(self, title):
        with self.coordinator.staged_state() as stage:
            result = self.coordinator.accept_decision("session", title)
            stage.commit()
        return result

    async def prepare(self, decision, process):
        with self.coordinator.staged_state() as stage:
            accepted = self.coordinator.start_dispatch(decision, self.boundary)
            stage.commit()
        return accepted

    async def ingest(self, sid):
        self.ingests += 1
        return self.boundary

    async def acknowledge(self, decision, success, boundary):
        with self.coordinator.staged_state() as stage:
            self.coordinator.acknowledge(decision, success=success, boundary=boundary)
            stage.commit()

    async def cancel_dispatch_before_delivery(self, decision):
        with self.coordinator.staged_state() as stage:
            self.coordinator.cancel_dispatch_before_delivery(decision)
            stage.commit()

    async def finish(self, sid, token):
        self.finished.append(token)
        with self.coordinator.staged_state() as stage:
            self.coordinator.retire_process(sid, token)
            stage.commit()

    async def inactive(self, sid):
        pass

    @property
    def _query(self):
        return self

    async def _send_control_request(self, request, *, timeout):
        self.sent.append(request["title"])
        self.concurrent += 1
        self.maximum = max(self.maximum, self.concurrent)
        self.entered.set()
        try:
            await self.release.wait()
            if self.failures:
                self.failures -= 1
                raise TimeoutError("uncertain delivery")
        finally:
            self.concurrent -= 1


@pytest.mark.asyncio
async def test_replaces_queued_work_without_overlapping_provider_requests():
    h = Harness()
    h.worker.request_publication(h.decision("first"))
    await h.entered.wait()
    h.worker.request_publication(h.decision("obsolete"))
    h.worker.request_publication(h.decision("latest"))
    assert h.sent == ["first"]
    h.release.set()
    assert await h.worker.flush_publications("session", timeout=1)
    assert h.sent == ["first", "latest"]
    assert h.maximum == 1
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_starting_waits_for_matching_process_ready_notification():
    h = Harness(ready=False)
    h.release.set()
    h.worker.request_publication(h.decision("title"))
    assert not await h.worker.flush_publications("session", timeout=0)
    h.worker.notify_process_ready("session", "old")
    assert h.sent == []
    h.process = h.process._replace(ready=True)
    h.worker.notify_process_ready("session", "process")
    assert await h.worker.flush_publications("session", timeout=1)
    assert h.sent == ["title"]
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_uncertain_sdk_failure_ingests_before_each_bounded_retry():
    h = Harness()
    h.failures = 10
    h.release.set()
    h.worker.request_publication(h.decision("title"))
    assert await h.worker.flush_publications("session", timeout=1)
    assert h.sent == ["title"] * 3
    assert h.ingests >= 3
    assert h.coordinator.current_decision("session").title == "title"
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_resolves_manager_entry_at_dispatch_and_rejects_replacement_process():
    h = Harness()
    decision = h.decision("title")
    h.process = h.process._replace(process_token="replacement")
    h.worker.request_publication(decision)
    assert await h.worker.flush_publications("session", timeout=1)
    assert h.sent == []
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_shutdown_is_bounded_during_provider_io():
    h = Harness()
    h.worker.request_publication(h.decision("title"))
    await h.entered.wait()
    await h.worker.close_publications()
    assert h.concurrent == 0


@pytest.mark.asyncio
async def test_hybrid_outcome_keeps_successor_blocked_until_confirmed_process_death(monkeypatch):
    from tests.test_hybrid_title_confirmation import evidence, make_agent

    h = Harness(hybrid=True)
    agent, pasted, sent = make_agent(monkeypatch)
    h.process = h.process._replace(agent=agent)
    first = h.decision("title")
    h.worker.request_publication(first)
    await pasted.wait()
    records = (evidence("rename_command", "title", message="command", line=2),
               evidence("command_success", "title", line=3),
               evidence("rename_command", "old", message="manual", line=4),
               evidence("command_success", "old", parent="manual", line=5))
    with h.coordinator.staged_state() as stage:
        result = h.coordinator.observe(records)
        stage.commit()
    assert [record.title for record in result.apply] == ["title", "old"]
    agent.on_title_evidence(records)
    assert await h.worker.flush_publications("session", timeout=1)
    successor = h.decision("successor")
    h.worker.request_publication(successor)
    assert await h.worker.flush_publications("session", timeout=1)
    assert sent == ["/rename title"]
    assert h.coordinator.current_decision("session") == successor
    with h.coordinator.staged_state() as stage:
        result = h.coordinator.observe((evidence("title", "title", line=6),))
        stage.commit()
    assert result.apply == ()
    h.process = None
    await h.worker.finish_process("session", "process")
    assert await h.worker.flush_publications("session", timeout=1)
    assert h.finished == ["process"]
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_rechecks_current_manager_process_after_dispatch_preparation():
    h = Harness()
    original = h.worker.hooks.prepare_dispatch

    async def replace_process(decision, process):
        result = await original(decision, process)
        h.process = h.process._replace(process_token="replacement")
        return result

    h.worker.hooks = h.worker.hooks._replace(prepare_dispatch=replace_process)
    h.release.set()
    h.worker.request_publication(h.decision("title"))
    assert await h.worker.flush_publications("session", timeout=1)
    assert h.sent == []
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_module_api_requires_explicit_configuration_before_acceptance(monkeypatch):
    from twicc.providers.claude_code import title_publication

    monkeypatch.setattr(title_publication, "_worker", None)
    h = Harness()
    with pytest.raises(RuntimeError, match="not configured"):
        title_publication.request_publication(h.decision("title"))
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_database_thread_notifications_schedule_on_the_manager_loop():
    h = Harness()
    h.release.set()
    decision = h.decision("title")
    await asyncio.to_thread(h.worker.request_publication, decision)
    assert await h.worker.flush_publications("session", timeout=1)
    assert h.sent == ["title"]
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_close_deadline_handles_transport_that_defers_cancellation():
    h = Harness()
    deferred = asyncio.Event()
    exited = asyncio.Event()

    async def transport(request, *, timeout):
        h.entered.set()
        try:
            await h.release.wait()
        except asyncio.CancelledError:
            deferred.set()
            await h.release.wait()
        finally:
            exited.set()

    h._send_control_request = transport
    h.worker.CLOSE_TIMEOUT = 0
    h.worker.request_publication(h.decision("title"))
    await h.entered.wait()
    await h.worker.close_publications()
    await deferred.wait()
    assert not exited.is_set()
    h.release.set()
    await exited.wait()
    assert await h.worker.flush_publications("session", timeout=1)
    assert h.finished == []


@pytest.mark.asyncio
async def test_repeated_notifications_do_not_restart_exhausted_sdk_budget():
    h = Harness()
    h.failures = 100
    h.release.set()
    decision = h.decision("title")
    h.worker.request_publication(decision)
    assert await h.worker.flush_publications("session", timeout=1)
    h.worker.notify_process_ready("session", "process")
    assert await h.worker.flush_publications("session", timeout=1)
    assert len(h.sent) == 3
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_duplicate_request_during_inactive_reconciliation_does_not_write_twice():
    h = Harness()
    h.process = None
    entered = asyncio.Event()
    release = asyncio.Event()
    writes = []

    async def reconcile(sid):
        writes.append(sid)
        entered.set()
        await release.wait()

    h.worker.hooks = h.worker.hooks._replace(reconcile_inactive=reconcile)
    decision = h.decision("title")
    h.worker.request_publication(decision)
    await entered.wait()
    h.worker.request_publication(decision)
    release.set()
    assert await h.worker.flush_publications("session", timeout=1)
    assert writes == ["session"]
    await h.worker.close_publications()
    assert writes == ["session"]


@pytest.mark.asyncio
async def test_failed_preparation_does_not_strand_a_newer_queued_decision():
    h = Harness()
    h.release.set()
    original = h.worker.hooks.prepare_dispatch

    async def prepare(decision, process):
        if decision.title == "first":
            h.worker.request_publication(h.decision("latest"))
            raise OSError("transcript scan failed")
        return await original(decision, process)

    h.worker.hooks = h.worker.hooks._replace(prepare_dispatch=prepare)
    h.worker.request_publication(h.decision("first"))
    assert await h.worker.flush_publications("session", timeout=1)
    assert h.sent == ["latest"]
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_module_wrappers_use_one_configured_worker(monkeypatch):
    from twicc.providers.claude_code import title_publication

    monkeypatch.setattr(title_publication, "_worker", None)
    h = Harness(ready=False)
    await h.worker.close_publications()
    worker = title_publication.configure_publications(h.coordinator, h.worker.hooks)
    with pytest.raises(RuntimeError, match="already configured"):
        title_publication.configure_publications(h.coordinator, h.worker.hooks)
    h.release.set()
    title_publication.request_publication(h.decision("title"))
    assert not await title_publication.flush_publications("session", timeout=0)
    h.process = h.process._replace(ready=True)
    title_publication.notify_process_ready("session", "process")
    assert await title_publication.flush_publications("session", timeout=1)
    assert h.sent == ["title"]
    async with title_publication.process_publication_reservation("session"):
        assert worker._reservations["session"].locked()
    h.process = None
    await title_publication.finish_process("session", "process")
    await title_publication.close_publications()
    assert h.finished == ["process"]


@pytest.mark.asyncio
async def test_hybrid_successor_after_committed_preparation_does_not_inherit_unsent_hazard(monkeypatch):
    from tests.test_hybrid_title_confirmation import make_agent

    h = Harness(hybrid=True)
    agent, pasted, sent = make_agent(monkeypatch)
    agent.TITLE_CONFIRMATION_TIMEOUT = 0
    h.process = h.process._replace(agent=agent)
    original = h.worker.hooks.prepare_dispatch

    async def prepare(decision, process):
        accepted = await original(decision, process)
        if decision.title == "obsolete":
            h.worker.request_publication(h.decision("successor"))
        return accepted

    h.worker.hooks = h.worker.hooks._replace(prepare_dispatch=prepare)
    h.worker.request_publication(h.decision("obsolete"))
    assert await h.worker.flush_publications("session", timeout=1)
    assert sent == ["/rename successor"]
    assert h.coordinator.current_decision("session").title == "successor"
    await h.worker.close_publications()


@pytest.mark.asyncio
async def test_inactive_failure_continues_newer_queued_work_without_retrying_old_decision():
    h = Harness()
    h.process = None
    entered = asyncio.Event()
    release = asyncio.Event()
    writes = []

    async def reconcile(sid):
        value = h.coordinator.current_decision(sid).title
        writes.append(value)
        if value == "first":
            entered.set()
            await release.wait()
            raise OSError("inactive file write failed")

    h.worker.hooks = h.worker.hooks._replace(reconcile_inactive=reconcile)
    h.worker.request_publication(h.decision("first"))
    await entered.wait()
    h.worker.request_publication(h.decision("latest"))
    release.set()
    assert await h.worker.flush_publications("session", timeout=1)
    assert writes == ["first", "latest"]
    await h.worker.close_publications()
