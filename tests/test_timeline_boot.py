from datetime import timedelta

import pytest
from django.utils import timezone

from twicc.agent.process_run_cleanup import CleanupStaleProcessRunsJob, _apply_cleanup_stale_process_runs_job
from twicc.core.models import ProcessRun, Project, Session, SessionItem, TimelineEvent
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_boot import reconcile_at_boot, record_backend_ended_clean
from tests.timeline_helpers import run_with_direct_writes

pytestmark = pytest.mark.django_db(transaction=True)
WORK = {"subagents": 1, "shells": 0, "monitors": 0, "scheduled_wakeup_at": None, "goal": False}


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-boot", directory="/tmp/tl-boot")


def stale_run(project, *, state="assistant_turn", awaiting=False, work=None, changed_ago=3600, **session_kw):
    now = timezone.now()
    Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex", **session_kw)
    return ProcessRun.objects.create(
        provider="codex", session_id="s1", started_at=now - timedelta(hours=3), state=state,
        last_state_change_at=now - timedelta(seconds=changed_ago), awaiting_user_input=awaiting,
        background_work_in_progress=work,
    )


def rows(**filters):
    return list(TimelineEvent.objects.filter(**filters).order_by("id"))


def test_lost_process_is_ended_at_the_last_evidence_never_at_boot(project):
    run = stale_run(project, changed_ago=3600)
    boot = timezone.now()
    reconcile_at_boot(now=boot, boot_id="b1")
    lost = rows(session_id="s1", kind="process.ended")[0]
    assert lost.reason == "lost" and lost.precision == "estimated" and lost.actor == "system"
    assert lost.occurred_at == run.last_state_change_at
    assert lost.data["upper_bound"] == boot.isoformat()
    assert lost.project_id == project.id


def test_the_latest_evidence_among_the_sources_wins(project):
    run = stale_run(project, changed_ago=3600, last_updated_at=timezone.now() - timedelta(minutes=5))
    reconcile_at_boot(now=timezone.now(), boot_id="b1")
    updated = Session.objects.get(id="s1").last_updated_at
    assert rows(session_id="s1", kind="process.ended")[0].occurred_at == updated
    assert run.last_state_change_at < updated


def test_the_last_session_item_timestamp_counts_as_evidence(project):
    stale_run(project, changed_ago=3600)
    last_item = timezone.now() - timedelta(minutes=2)
    SessionItem.objects.create(session_id="s1", line_num=1, content="{}", timestamp=last_item - timedelta(hours=1))
    SessionItem.objects.create(session_id="s1", line_num=2, content="{}", timestamp=last_item)
    SessionItem.objects.create(session_id="s1", line_num=3, content="{}", timestamp=None)
    reconcile_at_boot(now=timezone.now(), boot_id="b1")
    assert rows(session_id="s1", kind="process.ended")[0].occurred_at == last_item


@pytest.mark.parametrize("source", ["last_updated_at", "item"])
def test_evidence_later_than_the_boot_is_capped_at_the_boot(project, source):
    boot = timezone.now()
    later = boot + timedelta(hours=2)
    stale_run(project, changed_ago=3600, **({"last_updated_at": later} if source == "last_updated_at" else {}))
    if source == "item":
        SessionItem.objects.create(session_id="s1", line_num=1, content="{}", timestamp=later)
    reconcile_at_boot(now=boot, boot_id="b1")
    assert rows(session_id="s1", kind="process.ended")[0].occurred_at == boot


def test_open_inner_intervals_are_closed_first_with_the_same_time(project):
    stale_run(project, awaiting=True, work=WORK)
    reconcile_at_boot(now=timezone.now(), boot_id="b1")
    got = rows(session_id="s1")
    assert [r.kind for r in got] == ["turn.ended", "awaiting_input.ended", "process.ended"]
    assert got[0].data == {"background_work": None}  # a lost process runs nothing
    assert len({r.occurred_at for r in got}) == 1
    assert all(r.precision == "estimated" for r in got)  # inner ends included, like the lost end


def test_a_process_lost_while_idle_in_user_turn_writes_only_process_ended(project):
    stale_run(project, state="user_turn", work=WORK)
    reconcile_at_boot(now=timezone.now(), boot_id="b1")
    assert [r.kind for r in rows(session_id="s1")] == ["process.ended"]


def test_a_lost_process_inherits_the_data_of_its_started_row(project):
    stale_run(project)
    record_event_sync("process.started", session_id="s1", actor="user",
                      data={"provider": "codex", "cause": "resume", "twicc_pid": 9, "hybrid": True})
    reconcile_at_boot(now=timezone.now(), boot_id="b1")
    lost = rows(session_id="s1", kind="process.ended")[0]
    assert (lost.data["provider"], lost.data["cause"], lost.data["twicc_pid"], lost.data["hybrid"]) == (
        "codex", "resume", 9, True)
    assert lost.data["upper_bound"] is not None


def test_with_several_stale_rows_of_one_session_only_the_first_lost_end_inherits(project):
    stale_run(project)
    now = timezone.now()
    ProcessRun.objects.create(
        provider="codex", session_id="s1", started_at=now - timedelta(hours=1), state="assistant_turn",
        last_state_change_at=now - timedelta(minutes=30),
    )
    record_event_sync("process.started", session_id="s1", actor="user", data={"provider": "codex", "twicc_pid": 9})
    reconcile_at_boot(now=now, boot_id="b1")
    ended = rows(session_id="s1", kind="process.ended")
    assert len(ended) == 2
    assert sorted(r.data["provider"] or "" for r in ended) == ["", "codex"]


def test_dead_rows_are_left_alone(project):
    stale_run(project, state="dead")
    reconcile_at_boot(now=timezone.now(), boot_id="b1")
    assert rows(session_id="s1") == []


def test_first_boot_writes_backend_started_with_unknown_machine_restart(project):
    reconcile_at_boot(now=timezone.now(), boot_id="b1")
    started = rows(kind="backend.started")[0]
    assert started.session_id is None and started.project_id is None
    assert started.data["boot_id"] == "b1" and started.data["machine_restarted"] is None
    assert rows(kind="backend.ended") == []


def test_clean_previous_instance_gets_no_synthetic_end(project):
    reconcile_at_boot(now=timezone.now(), boot_id="b1")
    run_with_direct_writes(record_backend_ended_clean)
    reconcile_at_boot(now=timezone.now(), boot_id="b1")
    ended = rows(kind="backend.ended")
    assert [r.data.get("clean") for r in ended] == [True]
    assert ended[0].data["boot_id"] == "b1" and ended[0].data["pid"] == rows(kind="backend.started")[0].data["pid"]
    assert rows(kind="backend.started")[1].data["machine_restarted"] is False


def test_crashed_previous_instance_is_ended_at_its_last_non_backfilled_row(project):
    t0 = timezone.now() - timedelta(hours=2)
    record_event_sync("backend.started", occurred_at=t0, actor="system", data={"pid": 1, "boot_id": "old"})
    record_event_sync("turn.started", session_id="s1", occurred_at=t0 + timedelta(minutes=30), actor="agent")
    # An estimated row (e.g. a ``lost`` end of an earlier boot) counts: its time is evidence of life.
    last = record_event_sync("turn.ended", session_id="s1", occurred_at=t0 + timedelta(minutes=90),
                             actor="agent", precision="estimated")
    # A backfilled row is the only exclusion.
    record_event_sync("session.created", session_id="s1", occurred_at=t0 + timedelta(minutes=100),
                      actor="system", dedupe_key="backfill:session.created", data={"origin": "sync"})
    reconcile_at_boot(now=timezone.now(), boot_id="new")
    ended = rows(kind="backend.ended")[0]
    # The rest of the data is inherited from the crashed instance's ``backend.started``.
    assert ended.data == {"clean": False, "pid": 1, "version": None, "boot_id": "old", "machine_restarted": None}
    assert ended.precision == "estimated"
    assert ended.occurred_at == last.occurred_at
    assert rows(kind="backend.started")[1].data["machine_restarted"] is True


def test_the_evidence_time_of_a_lost_process_is_never_after_the_backend_end(project):
    t0 = timezone.now() - timedelta(hours=3)
    record_event_sync("backend.started", occurred_at=t0, actor="system", data={"pid": 1})
    stale_run(project, changed_ago=3600)
    reconcile_at_boot(now=timezone.now(), boot_id="b")
    lost = rows(session_id="s1", kind="process.ended")[0]
    ended = rows(kind="backend.ended")[0]
    assert ended.id > lost.id  # step 3 runs after step 1
    assert ended.occurred_at >= lost.occurred_at
    assert ended.occurred_at == lost.occurred_at  # the greatest evidence of life


def test_a_back_dated_row_never_pulls_backend_ended_before_its_own_start(project):
    t0 = timezone.now() - timedelta(hours=1)
    record_event_sync("backend.started", occurred_at=t0, actor="system", data={"pid": 1})
    # A lost-end row written after the start (higher id) but dated before it.
    record_event_sync("process.ended", session_id="s1", occurred_at=t0 - timedelta(minutes=30), actor="system",
                      reason="lost", precision="estimated")
    reconcile_at_boot(now=timezone.now(), boot_id="b")
    assert rows(kind="backend.ended")[0].occurred_at == t0


def test_evidence_dated_after_the_boot_never_pushes_backend_ended_after_it(project):
    boot = timezone.now()
    record_event_sync("backend.started", occurred_at=boot - timedelta(hours=2), actor="system", data={"pid": 1})
    # Simulates a wrong clock before NTP synced: a row dated after this boot.
    record_event_sync("turn.started", session_id="s1", occurred_at=boot + timedelta(hours=1), actor="agent")
    reconcile_at_boot(now=boot, boot_id="b")
    ended = rows(kind="backend.ended")[0]
    assert ended.occurred_at == boot
    assert ended.occurred_at <= rows(kind="backend.started")[1].occurred_at


def test_a_clock_that_went_backwards_makes_the_upper_bound_win(project):
    boot = timezone.now()
    # The previous backend.started is dated AFTER this boot (clock went backwards).
    record_event_sync("backend.started", occurred_at=boot + timedelta(hours=2), actor="system", data={"pid": 1})
    reconcile_at_boot(now=boot, boot_id="b")
    ended = rows(kind="backend.ended")[0]
    assert ended.occurred_at == boot  # the upper bound wins: a negative duration, id order stays correct
    assert ended.id > rows(kind="backend.started")[0].id


def test_crashed_instance_with_no_later_event_ends_at_its_own_start(project):
    t0 = timezone.now() - timedelta(hours=1)
    record_event_sync("backend.started", occurred_at=t0, actor="system", data={"pid": 1})
    reconcile_at_boot(now=timezone.now(), boot_id="b")
    assert rows(kind="backend.ended")[0].occurred_at == t0


def test_the_cleanup_job_reconciles_the_timeline_then_cleans_the_process_runs(project):
    stale_run(project)
    job = CleanupStaleProcessRunsJob(future=None)  # a direct call never touches the future
    assert _apply_cleanup_stale_process_runs_job(job) == 1
    assert not ProcessRun.objects.filter(session_id="s1").exists()
    assert [r.kind for r in rows(session_id="s1")] == ["turn.ended", "process.ended"]
    assert len(rows(kind="backend.started")) == 1


def test_a_failing_reconciliation_never_blocks_the_cleanup(project, monkeypatch):
    stale_run(project)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("twicc.core.services.timeline_boot.reconcile_at_boot", boom)
    assert _apply_cleanup_stale_process_runs_job(CleanupStaleProcessRunsJob(future=None)) == 1
    assert not ProcessRun.objects.filter(session_id="s1").exists()
    assert rows() == []


def test_missing_boot_id_makes_machine_restarted_unknown(project):
    record_event_sync("backend.started", actor="system", data={"pid": 1, "boot_id": "old"})
    reconcile_at_boot(now=timezone.now(), boot_id=None)
    assert rows(kind="backend.started")[1].data["machine_restarted"] is None


def test_an_unreadable_version_never_loses_the_reconciliation(project, monkeypatch):
    def boom():
        raise OSError("no pyproject")

    monkeypatch.setattr("twicc.core.services.timeline_boot.get_version", boom)
    stale_run(project)
    reconcile_at_boot(now=timezone.now(), boot_id="b1")
    assert [r.kind for r in rows(session_id="s1")] == ["turn.ended", "process.ended"]
    assert rows(kind="backend.started")[0].data["version"] is None
