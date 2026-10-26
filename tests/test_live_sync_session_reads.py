"""Characterization of the live sync's content storage, pinned before removing its duplicate session reads.

Plan: ``docs/plans/2026-10-26-live-sync-duplicate-session-reads-plan.md`` (Task 1).
Each scenario replays rollout lines through ``sync_session_slice`` (one call per
slice) and records, after each slice, the promotion wakeups, the cold-cache
invalidations and the storage state of every session of the scenario
(incarnation, version, epochs, item references and content rows). The records
are pinned in ``tests/fixtures/live_sync_session_reads.json``: the later tasks
of the plan must not change them. The budget tests pin the query count of a
one-line slice and the order of its first write.

Regenerate the fixture after an intended change with ``TWICC_UPDATE_FIXTURES=1``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import orjson
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from twicc.core.content_storage import ingestion, lifecycle
from twicc.core.content_storage.policy import CONTENT_STORAGE_VERSION
from twicc.core.enums import Provider
from twicc.core.models import Project, Session, SessionItem, SessionItemContent, SessionType
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.codex.compute import CodexSessionCompute
from twicc.providers.live_sync import LiveSyncLimits

from tests.codex_agent_run_fixtures import (
    AGENT_A,
    AGENT_B,
    AGENT_G,
    ALL_FIXTURES,
    PATH_A,
    PATH_B,
    PROJECT_ID,
    ROOT,
    T0,
    CodexAgentRunFixture,
    Rollout,
    _fixture,
    _root_start,
)
from tests.conftest import NOW
from tests.test_claude_agent_runs import line as claude_line
from tests.test_codex_agent_runs_live import LiveReplay

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frozen_now")]

FIXTURE = Path(__file__).parent / "fixtures" / "live_sync_session_reads.json"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def storage_snapshot(session_ids: Sequence[str]) -> dict:
    """The storage state of ``session_ids``: session columns, item references, content rows."""
    ids = sorted(set(session_ids))
    sessions = {
        row["id"]: {
            "incarnation_set": bool(row["storage_incarnation"]),
            "content_storage_version": row["content_storage_version"],
            "content_state_epoch": row["content_state_epoch"],
            "plaintext_verified_epoch": row["plaintext_verified_epoch"],
        }
        for row in Session.objects.filter(id__in=ids).values(
            "id", "storage_incarnation", "content_storage_version", "content_state_epoch",
            "plaintext_verified_epoch",
        )
    }
    contents = {row.id: row for row in SessionItemContent.objects.only("id", "sha256", "codec", "session_ids")}

    def reference(content_id):
        if content_id is None:
            return None
        row = contents[content_id]
        return {"sha256": bytes(row.sha256).hex(), "codec": row.codec}

    items = []
    referenced = set()
    for session_id, line_num, current_id, original_id in SessionItem.objects.filter(
        session_id__in=ids,
    ).order_by("session_id", "line_num").values_list("session_id", "line_num", "current_content_id",
                                                     "original_content_id"):
        referenced.update((current_id, original_id))
        items.append({"session": session_id, "line": line_num,
                      "current": reference(current_id), "original": reference(original_id)})
    rows = sorted(
        ({"sha256": bytes(row.sha256).hex(), "codec": row.codec, "session_ids": sorted(row.session_ids)}
         for row in contents.values() if row.id in referenced or set(row.session_ids) & set(ids)),
        key=lambda row: row["sha256"],
    )
    return {"sessions": [{"id": sid, **sessions[sid]} for sid in ids if sid in sessions],
            "items": items, "contents": rows}


class Spy:
    """Wraps ``module.name``: records each call's arguments and result, then returns that result."""

    def __init__(self, monkeypatch, module, name: str, *, describe: Callable | None = None):
        self.calls: list[tuple[object, object]] = []
        original = getattr(module, name)

        def spy(*args, **kwargs):
            described = describe(*args, **kwargs) if describe is not None else args
            result = original(*args, **kwargs)
            self.calls.append((described, result))
            return result

        monkeypatch.setattr(module, name, spy)

    @property
    def results(self) -> list:
        return [result for _, result in self.calls]

    def clear(self) -> None:
        self.calls.clear()


class SliceReplay:
    """Creates sessions with empty files, then appends lines and syncs exactly one slice per call."""

    def __init__(self, tmp_path: Path, monkeypatch, capture_on_commit, effects):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.capture_on_commit = capture_on_commit
        self.effects = effects
        self.computes = {Provider.CODEX: CodexSessionCompute(), Provider.CLAUDE_CODE: ClaudeCodeSessionCompute()}
        self.paths: dict[str, Path] = {}
        self.providers: dict[str, str] = {}
        self.records: list[dict] = []
        self.lines: dict[str, tuple[str, ...]] = {}

    def codex(self, fixture: CodexAgentRunFixture) -> LiveReplay:
        live = LiveReplay(fixture, self.tmp_path, compute=self.computes[Provider.CODEX])
        for fixture_session in fixture.sessions:
            self.track(fixture_session.session_id, live.paths[fixture_session.session_id], Provider.CODEX)
            self.lines[fixture_session.session_id] = fixture_session.lines
        return live

    def session(self, session_id: str, provider: str, *, parent_id: str | None = None) -> Session:
        """A session (a subagent of ``parent_id`` when given) with an empty file."""
        project, _ = Project.objects.get_or_create(id=PROJECT_ID)
        path = self.tmp_path / f"{session_id}.jsonl"
        path.touch()
        session = Session.objects.create(
            id=session_id, project=project, provider=provider, file_path=str(path),
            type=SessionType.SUBAGENT if parent_id else SessionType.SESSION, parent_session_id=parent_id,
        )
        self.track(session_id, path, provider)
        return session

    def track(self, session_id: str, path: Path, provider: str) -> None:
        self.paths[session_id] = path
        self.providers[session_id] = provider

    def sync(self, session_id: str, lines: Sequence[str] = (), *, active: frozenset[str] | None = None) -> None:
        """Append ``lines`` to the session file and run one ``sync_session_slice``, then record the slice."""
        path = self.paths[session_id]
        with path.open("a", encoding="utf-8") as handle:
            handle.writelines(f"{line}\n" for line in lines)
        token = lifecycle.active_session_ids.set(active) if active is not None else None
        try:
            with self.capture_on_commit(execute=True):
                self.computes[self.providers[session_id]].sync_session_slice(
                    session_id, path, limits=LiveSyncLimits(),
                )
        finally:
            if token is not None:
                lifecycle.active_session_ids.reset(token)
        self.records.append({
            "slice": len(self.records) + 1,
            "session": session_id,
            "wakeups": sorted(self.effects.wakeups),
            "cold": sorted(self.effects.cold, key=repr),
            "storage": storage_snapshot(list(self.paths)),
        })
        self.effects.wakeups.clear()
        self.effects.cold.clear()


def session_row(session_id: str) -> dict:
    return Session.objects.filter(id=session_id).values(
        "storage_incarnation", "content_storage_version", "content_state_epoch", "plaintext_verified_epoch",
    ).get()


def certify(session_id: str) -> None:
    """Give a synced root a valid plaintext certificate for its current epoch."""
    epoch = session_row(session_id)["content_state_epoch"]
    Session.objects.filter(id=session_id, storage_incarnation__isnull=True).update(storage_incarnation=str(uuid4()))
    Session.objects.filter(id=session_id).update(
        content_storage_version=CONTENT_STORAGE_VERSION, plaintext_verified_epoch=epoch,
    )
    assert is_certified(session_id)


def is_certified(session_id: str) -> bool:
    row = session_row(session_id)
    return (bool(row["storage_incarnation"]) and row["content_storage_version"] == CONTENT_STORAGE_VERSION
            and row["content_state_epoch"] is not None
            and row["plaintext_verified_epoch"] == row["content_state_epoch"])


def queryset_ids(queryset, *args, **kwargs) -> list[str]:
    """``update_lifecycle``'s target ids (one extra read, outside any measured query count)."""
    return sorted(queryset.values_list("id", flat=True))


def lifecycle_update_spy(monkeypatch) -> Spy:
    return Spy(monkeypatch, lifecycle, "update_lifecycle", describe=queryset_ids)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def t20_per_line(replay: SliceReplay) -> None:
    fixture = ALL_FIXTURES["t20_sequence"]()
    replay.codex(fixture)
    assert not Session.objects.filter(storage_incarnation__isnull=False).exists()
    updates = lifecycle_update_spy(replay.monkeypatch)
    for fixture_session in fixture.sessions:
        for line in fixture_session.lines:
            replay.sync(fixture_session.session_id, [line])
    assert any(rows >= 1 for rows in updates.results), updates.calls


def t20_one_chunk(replay: SliceReplay) -> None:
    fixture = ALL_FIXTURES["t20_sequence"]()
    replay.codex(fixture)
    for fixture_session in fixture.sessions:
        replay.sync(fixture_session.session_id, fixture_session.lines)


def child_before_root(replay: SliceReplay) -> None:
    fixture = ALL_FIXTURES["t20_sequence"]()
    replay.codex(fixture)
    assert session_row(ROOT)["storage_incarnation"] is None
    replay.sync(AGENT_A, fixture.session(AGENT_A).lines)
    replay.sync(ROOT, fixture.session(ROOT).lines)


def certified_root_multibatch_child_fixture() -> CodexAgentRunFixture:
    """A two-line root and a 121-line child: three insert batches of 50 lines in one child slice."""
    root = _root_start()
    child = Rollout(AGENT_A, subagent=True)
    child.meta(2.1)
    for turn in range(60):
        child.task_started(3 + 2 * turn, f"a{turn}")
        child.task_complete(4 + 2 * turn, f"a{turn}")
    return _fixture("certified_root_multibatch_child", root, child)


def certified_root_multibatch_child(replay: SliceReplay) -> None:
    """A certified root, then a child slice of more than 50 lines: the root must stay certified."""
    fixture = certified_root_multibatch_child_fixture()
    replay.codex(fixture)
    replay.sync(ROOT, fixture.session(ROOT).lines)
    certify(ROOT)
    bindings = Spy(replay.monkeypatch, ingestion, "bind_persisted_items")
    child_lines = fixture.session(AGENT_A).lines
    assert len(child_lines) == 121
    replay.sync(AGENT_A, child_lines)
    assert len(bindings.calls) == 3
    assert is_certified(ROOT)


def certified_root_replay_changed_date(replay: SliceReplay) -> None:
    fixture = ALL_FIXTURES["t20_sequence"]()
    replay.codex(fixture)
    replay.sync(ROOT, fixture.session(ROOT).lines)
    certify(ROOT)
    Session.objects.filter(id=ROOT).update(last_updated_at=NOW - timedelta(days=2), last_offset=0, last_line=0)
    carries = Spy(replay.monkeypatch, lifecycle, "carry_plaintext_certificate")
    items_before = SessionItem.objects.count()
    replay.sync(ROOT)
    assert SessionItem.objects.count() == items_before
    assert carries.calls


def active_set(replay: SliceReplay) -> None:
    replay.monkeypatch.setattr(timezone, "now", lambda: T0 + timedelta(days=40))
    fixture = ALL_FIXTURES["t20_sequence"]()
    replay.codex(fixture)
    for fixture_session in fixture.sessions:
        replay.sync(fixture_session.session_id, fixture_session.lines, active=frozenset({ROOT, AGENT_A}))
    assert any(record["wakeups"] for record in replay.records)


def cold_root_becomes_hot(replay: SliceReplay) -> None:
    fixture = ALL_FIXTURES["t20_sequence"]()
    replay.codex(fixture)
    old = NOW - timedelta(days=40)
    Session.objects.filter(id=ROOT).update(
        storage_incarnation=str(uuid4()), content_storage_version=CONTENT_STORAGE_VERSION,
        created_at=old, last_started_at=old, last_updated_at=old,
    )
    replay.sync(ROOT, fixture.session(ROOT).lines)
    assert ROOT in replay.records[-1]["wakeups"]


def certified_root_new_lines(replay: SliceReplay) -> None:
    """The root's certificate captured at slice start (C2) is carried to the slice's final epoch."""
    fixture = ALL_FIXTURES["t20_sequence"]()
    replay.codex(fixture)
    lines = fixture.session(ROOT).lines
    replay.sync(ROOT, lines[:5])
    certify(ROOT)
    captures = Spy(replay.monkeypatch, lifecycle, "capture_plaintext_certificate")
    carries = Spy(replay.monkeypatch, ingestion, "carry_plaintext_certificate")
    replay.sync(ROOT, lines[5:8])
    assert captures.results == [None, None]
    assert carries.results and carries.results[-1] is True


def owner_abort_two_children(replay: SliceReplay) -> None:
    root = _root_start()
    root.spawn(2, "c_a", AGENT_A, PATH_A)
    root.spawn(3, "c_b", AGENT_B, PATH_B)
    root.turn_aborted(30, "t1")
    child_a = Rollout(AGENT_A, subagent=True)
    child_a.meta(2.1)
    child_a.task_started(2.5, "a1")
    child_b = Rollout(AGENT_B, subagent=True)
    child_b.meta(3.1)
    child_b.task_started(3.5, "b1")
    fixture = _fixture("owner_abort_two_children", root, child_a, child_b)
    replay.codex(fixture)
    replay.sync(AGENT_A, fixture.session(AGENT_A).lines)
    replay.sync(AGENT_B, fixture.session(AGENT_B).lines)
    updates = lifecycle_update_spy(replay.monkeypatch)
    replay.sync(ROOT, fixture.session(ROOT).lines)
    assert updates.results == [1, 1], updates.calls


def subagent_stops_itself_and_its_child(replay: SliceReplay) -> None:
    fixture = ALL_FIXTURES["subagent_owner_usage_limit"]()
    replay.codex(fixture)
    # The fixture has no rollout for the nested agent: create its row by hand.
    replay.session(AGENT_G, Provider.CODEX, parent_id=ROOT)
    replay.sync(ROOT, fixture.session(ROOT).lines)
    updates = lifecycle_update_spy(replay.monkeypatch)
    replay.sync(AGENT_A, fixture.session(AGENT_A).lines)
    assert updates.results == [1, 1], updates.calls
    assert [AGENT_A] in [ids for ids, _ in updates.calls]


CLAUDE_ROOT = "11111111-1111-4111-8111-111111111111"
CLAUDE_CHILD = "a1111111111111111"


def claude_lines() -> list[str]:
    return [orjson.dumps(entry).decode() for entry in (
        claude_line("user", "hello", 0),
        claude_line("assistant", [{"type": "text", "text": "hi"}], 1),
        claude_line("user", "thanks", 2),
    )]


def claude_root_per_line(replay: SliceReplay) -> None:
    replay.session(CLAUDE_ROOT, Provider.CLAUDE_CODE)
    for line in claude_lines():
        replay.sync(CLAUDE_ROOT, [line])


SCENARIOS: dict[str, Callable[[SliceReplay], None]] = {
    scenario.__name__: scenario for scenario in (
        t20_per_line,
        t20_one_chunk,
        child_before_root,
        certified_root_multibatch_child,
        certified_root_replay_changed_date,
        active_set,
        cold_root_becomes_hot,
        certified_root_new_lines,
        owner_abort_two_children,
        subagent_stops_itself_and_its_child,
        claude_root_per_line,
    )
}


def replay(scenario: str, tmp_path: Path, django_capture_on_commit_callbacks, *, monkeypatch, effects) -> list[dict]:
    """Run ``scenario`` slice by slice and return one record per slice."""
    harness = SliceReplay(tmp_path, monkeypatch, django_capture_on_commit_callbacks, effects)
    SCENARIOS[scenario](harness)
    return harness.records


# ---------------------------------------------------------------------------
# Golden equivalence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_live_sync_matches_golden_record(scenario, tmp_path, django_capture_on_commit_callbacks, monkeypatch,
                                         effects):
    records = replay(scenario, tmp_path, django_capture_on_commit_callbacks, monkeypatch=monkeypatch,
                     effects=effects)
    rendered = orjson.dumps(records)
    if os.environ.get("TWICC_UPDATE_FIXTURES") == "1":
        stored = orjson.loads(FIXTURE.read_bytes()) if FIXTURE.is_file() else {}
        stored[scenario] = orjson.loads(rendered)
        FIXTURE.write_bytes(orjson.dumps(stored, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n")
    assert FIXTURE.is_file(), "missing fixture: run this test once with TWICC_UPDATE_FIXTURES=1"
    stored = orjson.loads(FIXTURE.read_bytes())
    assert scenario in stored, "missing scenario in the fixture: run this test once with TWICC_UPDATE_FIXTURES=1"
    assert stored[scenario] == orjson.loads(rendered), (
        "the live sync's content-storage behaviour changed: update the fixture with TWICC_UPDATE_FIXTURES=1 "
        "only if the change is intended"
    )


# ---------------------------------------------------------------------------
# One-line slice budgets
# ---------------------------------------------------------------------------


# Exact query counts of a one-line slice, under ``frozen_now`` (hot sessions). Pinned with ``==`` so any added
# read fails. Before the duplicate ``core_session`` reads were removed: 51 / 53 / 60 / 52.
BUDGETS: dict[tuple[str, str], int] = {
    ("claude_code", "root"): 35,
    ("codex", "root"): 37,
    ("claude_code", "child"): 42,
    ("codex", "child"): 34,
}


def budget_lines(provider: str, session_id: str) -> list[str]:
    if provider == Provider.CLAUDE_CODE:
        return claude_lines()
    rollout = Rollout(session_id, subagent=session_id != ROOT)
    rollout.meta(0)
    rollout.task_started(1, "t1")
    rollout.task_complete(2, "t1")
    rollout.task_started(3, "t2")
    return rollout.lines


def slice_query_count(provider: str, kind: str, tmp_path: Path) -> tuple[int, list[str]]:
    """The queries of a one-line slice of a root, or of a child whose root file stays empty and unsynced."""
    compute = CodexSessionCompute() if provider == Provider.CODEX else ClaudeCodeSessionCompute()
    root_id, child_id = (ROOT, AGENT_A) if provider == Provider.CODEX else (CLAUDE_ROOT, CLAUDE_CHILD)
    project, _ = Project.objects.get_or_create(id=PROJECT_ID)
    paths = {}
    for session_id, parent_id in ((root_id, None), *(((child_id, root_id),) if kind == "child" else ())):
        paths[session_id] = tmp_path / f"{session_id}.jsonl"
        paths[session_id].touch()
        Session.objects.create(
            id=session_id, project=project, provider=provider, file_path=str(paths[session_id]),
            type=SessionType.SUBAGENT if parent_id else SessionType.SESSION, parent_session_id=parent_id,
        )
    measured = child_id if kind == "child" else root_id
    path = paths[measured]
    *head, last = budget_lines(provider, measured)
    path.write_text("".join(f"{line}\n" for line in head), encoding="utf-8")
    compute.sync_session_slice(measured, path, limits=LiveSyncLimits())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{last}\n")
    with CaptureQueriesContext(connection) as queries:
        compute.sync_session_slice(measured, path, limits=LiveSyncLimits())
    assert SessionItem.objects.filter(session_id=measured).count() == len(head) + 1
    statements = [query["sql"] for query in queries.captured_queries]
    return len(statements), statements


BUDGET_CASES = [pytest.param(provider, kind, id=f"{provider}-{kind}")
                for provider in ("claude_code", "codex") for kind in ("root", "child")]


@pytest.mark.parametrize("provider,kind", BUDGET_CASES)
def test_one_line_slice_query_budget(provider, kind, tmp_path):
    count, statements = slice_query_count(provider, kind, tmp_path)
    assert count == BUDGETS[(provider, kind)], "\n".join(statements)


@pytest.mark.parametrize("provider,kind", BUDGET_CASES)
def test_first_write_of_a_slice_is_the_incarnation_update(provider, kind, tmp_path):
    _, statements = slice_query_count(provider, kind, tmp_path)
    writes = [sql for sql in statements if not sql.lstrip().upper().startswith(("SELECT", "SAVEPOINT", "RELEASE"))]
    assert writes and writes[0].startswith('UPDATE "core_session" SET "storage_incarnation"'), writes[:3]
