# Session Timeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the important events of every session in a new append-only `TimelineEvent` table, with one standard recording function, hooks at every writer, boot-time crash reconciliation and a conservative backfill.

**Architecture:** One model (`TimelineEvent`) and one validated payload registry (`core/timeline.py`). One never-raising recording function (`core/services/session_timeline.py`) that every hook calls, inside the existing DB write lock. Hooks sit at the real writers of each fact (agent manager, flag services, creation paths, title and settings writers). A boot job reconciles processes lost to a crash; a background task backfills existing sessions.

**Tech Stack:** Python 3.13, Django 6 (SQLite, WAL), asyncio, pytest + pytest-django.

**Spec:** `docs/plans/2026-10-13-session-timeline-design.md` (read it first; section numbers below refer to it).

## Global Constraints

- All code, comments and docs in **English** (CLAUDE.md).
- `Session` table: **no change** (spec §1). No column added, changed or removed.
- Event names follow `subject.verb` (spec §4). The set of kinds is closed.
- Simple immutable data uses **`NamedTuple`**, not `@dataclass` (CLAUDE.md). Backend JSON uses `orjson`; this plan needs no manual JSON handling (`JSONField` does it).
- No aliased imports (`as`) unless a real name collision forces it.
- Every timeline write runs under `run_under_db_write_lock` (spec §6). A failed write is logged and never blocks or undoes the user action.
- Never import application code inside a migration (the migration of this plan is auto-generated and imports none).
- Tests: `uv run pytest <file> -v` from the main checkout. In a worktree: `cd <worktree> && TWICC_DATA_DIR=$PWD uv run pytest <file> -v`. Never `uv pip`, never `--active`.
- Python lint: `uvx ruff check <files>` (line length 120). Ruff is not installed in the env.
- Do **not** run `migrate` by hand and do not restart servers (CLAUDE.md "Operations Reserved to User"). Creating the migration file with `makemigrations` is allowed and required.
- Do **not** write the CHANGELOG (needs an explicit user request).
- Preserve uncommitted changes you did not make (e.g. `frontend/src/inline-artifacts/*`): never revert, stage or commit them.
- Commit messages: Conventional Commits, a descriptive body, and the trailer `Co-Authored-By: Claude MODEL <noreply@anthropic.com>` where `MODEL` is the exact name of the model doing the work. `git add` lists files explicitly, never a directory.

## Review Focus

The inputs and conditions the spec implies but that no happy-path test would exercise. Each line has a pinning test in the task that owns the code.

1. **A flag set to its current value** (archive an archived session, mute a muted one, hide a hidden one) writes nothing. Task 4.
2. **Archiving a pinned session with `also_unpin`** writes `archived.started` then `pinned.ended` (scope inherited from `pinned.started`) with `caused_by` pointing at the archive event, and nothing else. Task 4.
3. **A failing event insert inside a caller's transaction** does not roll back or poison the caller's work (savepoint). Task 3.
4. **Agent death with open inner intervals** (assistant turn, waiting for the user) closes both before `process.ended`, with the same `occurred_at`, and that `turn.ended` carries `background_work` null. Tasks 2 and 7.
5. **Boot after a crash** writes `process.ended(reason="lost")` dated at the last evidence of life, never at boot time, plus `backend.ended(clean=false)` for the previous instance. A process lost while idle in `user_turn` writes only `process.ended`. Task 9.
6. **Backfill run twice, or beside live writes,** never duplicates a row. Task 10.
7. **Bulk archive of sessions that another request archived meanwhile** emits `archived.started` only for sessions still unarchived under the lock. Task 5.
8. **An `ended` row inherits the data of its OPEN `started` row**: the latest `started` row of the same session with no newer `ended` row; a closed interval is never inherited (lost `started` write, second stale row at boot), a caller-provided field wins, no open `started` row leaves the fields null. Task 3 (service), Tasks 7 and 9 (process and backend rows).
9. **Background work changing while the agent is idle** re-emits `turn.ended` with the new snapshot; the same change during an assistant turn, or a death from `user_turn`, writes no extra row. Tasks 2 and 7.
10. **A pin scope change** writes `pinned.ended` (old scope) then `pinned.started` (new scope), same `occurred_at`, the second `caused_by` the first. Task 4.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/twicc/core/models.py` | modify (append) | `TimelineKind`, `TimelinePrecision`, `TimelineActor`, `TimelineEvent` |
| `src/twicc/core/migrations/0156_timeline_event.py` | create (generated) | Creates the table and its indexes |
| `src/twicc/core/timeline.py` | create | Payload `NamedTuple`s, `STARTED_OF`, `validate_data`, `inherit_started_fields`, pure `process_transition_events` |
| `src/twicc/core/services/session_timeline.py` | create | `record_event_sync` (with the `ended`-row inheritance), `record_event`, `latest_event_id`, `project_id_of`, `record_session_created`, `record_title_change` |
| `src/twicc/agent/timeline_hooks.py` | create | Agent-aware helpers: `record_process_started`, `record_process_changes`, `record_process_ended` |
| `src/twicc/core/services/timeline_boot.py` | create | Boot reconciliation and `backend.*` events |
| `src/twicc/core/services/timeline_backfill.py` | create | Backfill batch + async runner |
| `src/twicc/core/services/session_update.py` | modify | Hooks: archive, pin, mute, title, settings; new `archive_sessions_bulk` |
| `src/twicc/core/services/session_visibility.py` | modify | Hook: hidden |
| `src/twicc/views.py` | modify | Hooks: bulk archive, PATCH title |
| `src/twicc/agent/base_manager.py` | modify | Hooks: process start, transition, background work, death, pending-title flush |
| `src/twicc/agent/process_run_cleanup.py` | modify | Call the boot reconciliation before the bulk DEAD update |
| `src/twicc/providers/sessions_watcher.py` | modify | Hook: `session.created` for live-discovered sessions |
| `src/twicc/providers/db_writer.py` | modify | Hooks: `session.created` (initial sync), settings (retire job); timeline purge in `_apply_delete_sessions_payload` |
| `src/twicc/providers/compute_base.py` | modify | Hooks: provider and placeholder titles |
| `src/twicc/providers/codex/titles.py` | modify | Hook: Codex title import |
| `src/twicc/providers/claude_code/ws.py` | modify | Hook: `permission_mode` change from an approved `setMode` suggestion |
| `src/twicc/core/management/commands/sync.py` | modify | `sync --reset` deletes timeline rows before projects (`PROTECT`) |
| `tests/test_agent_run_stop_step.py` | modify | Delete `TimelineEvent` rows before `Project` rows in `_reset_db_and_caches` |
| `src/twicc/core/services/title_automation.py` | modify | Hook: automatic title |
| `src/twicc/cli/run.py` | modify | `backend.ended(clean)` at shutdown, start/cancel the backfill task |
| `src/twicc/providers/claude_code/agent/hybrid/agent.py` | modify | Pass `actor` to `persist_session_settings` |
| `tests/timeline_helpers.py` | create | Shared test helpers |
| `tests/test_timeline_*.py` | create | One test file per task |
| `docs/plans/2026-10-13-session-timeline-design.md` | modify | Spec clarifications (Task 11) |
| `CLAUDE.md`, `AGENTS.md` | modify | Document the model (Task 11) |

---

### Task 1: Model, enums, payload registry, migration

**Files:**
- Modify: `src/twicc/core/models.py` (append at end of file)
- Create: `src/twicc/core/timeline.py`
- Create: `src/twicc/core/migrations/0156_timeline_event.py` (generated)
- Modify: `src/twicc/core/management/commands/sync.py` (`--reset` deletes projects), `tests/test_agent_run_stop_step.py` (`_reset_db_and_caches`): both delete `Project` rows, which `PROTECT` now blocks (Step 6)
- Test: `tests/test_timeline_model.py`

**Interfaces:**
- Produces: `TimelineKind`, `TimelinePrecision`, `TimelineActor`, `TimelineEvent` (models); `PAYLOAD_TYPES: dict[str, type | None]`, `STARTED_OF: dict[str, str]`, `validate_data(kind: str, data: dict | None) -> dict`, `inherit_started_fields(kind: str, data: dict | None, started_data: dict | None) -> dict` (timeline.py).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timeline_model.py
import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from twicc.core.models import Project, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision
from twicc.core.timeline import PAYLOAD_TYPES, STARTED_OF, inherit_started_fields, validate_data


def test_every_kind_has_a_payload_entry():
    assert set(PAYLOAD_TYPES) == set(TimelineKind.values)


def test_started_of_pairs_each_ended_kind_with_a_started_kind_that_it_repeats():
    assert set(STARTED_OF) == {
        "awaiting_input.ended", "process.ended", "pinned.ended", "backend.ended",
    }
    for ended, started in STARTED_OF.items():
        assert started == ended.replace(".ended", ".started")
        # The ended payload has every field of the started payload, plus its own.
        assert set(PAYLOAD_TYPES[started]._fields) <= set(PAYLOAD_TYPES[ended]._fields)


def test_inherit_started_fields_fills_only_what_the_caller_left_out():
    started = {"provider": "codex", "cause": "resume", "twicc_pid": 7, "hybrid": True, "via": "cli"}
    merged = inherit_started_fields("process.ended", {"error": "boom", "provider": "claude_code"}, started)
    assert merged == {"error": "boom", "provider": "claude_code", "cause": "resume", "twicc_pid": 7, "hybrid": True}
    assert inherit_started_fields("process.ended", {"error": "boom"}, None) == {"error": "boom"}
    assert inherit_started_fields("pinned.ended", None, {"scope": "all"}) == {"scope": "all"}


def test_kind_names_follow_subject_verb():
    for value in TimelineKind.values:
        subject, _, verb = value.partition(".")
        assert subject and verb and "." not in verb, value


def test_validate_data_fills_defaults_and_keeps_known_keys():
    assert validate_data("session.created", {"origin": "sync"}) == {
        "origin": "sync", "provider": None, "settings": None, "spawned_by": None,
    }
    assert validate_data("title.changed", {"source": "user", "new": "B"}) == {"source": "user", "new": "B", "old": None}
    assert validate_data("process.ended", None) == {
        "provider": None, "cause": None, "twicc_pid": None, "hybrid": None, "error": None, "upper_bound": None,
    }
    assert validate_data("turn.ended", None) == {"background_work": None}


def test_validate_data_rejects_unknown_and_missing_keys():
    with pytest.raises(ValueError):
        validate_data("session.created", {"origin": "sync", "nope": 1})
    with pytest.raises(ValueError):
        validate_data("session.created", {})


def test_validate_data_rejects_payload_on_payloadless_kind():
    assert validate_data("turn.started", None) == {}
    with pytest.raises(ValueError):
        validate_data("turn.started", {"x": 1})


def test_validate_data_allows_the_reserved_via_key_on_every_kind():
    assert validate_data("turn.started", {"via": "cli"}) == {"via": "cli"}
    with pytest.raises(ValueError):
        validate_data("turn.started", {"via": "carrier-pigeon"})


@pytest.mark.django_db
def test_dedupe_key_is_unique_per_session_only_when_set():
    now = timezone.now()
    base = dict(kind=TimelineKind.SESSION_CREATED, occurred_at=now, actor=TimelineActor.SYSTEM)
    TimelineEvent.objects.create(session_id="s1", dedupe_key="k", **base)
    TimelineEvent.objects.create(session_id="s2", dedupe_key="k", **base)
    TimelineEvent.objects.create(session_id="s1", **base)
    TimelineEvent.objects.create(session_id="s1", **base)
    with pytest.raises(IntegrityError), transaction.atomic():
        TimelineEvent.objects.create(session_id="s1", dedupe_key="k", **base)


@pytest.mark.django_db
def test_defaults_and_project_fk():
    project = Project.objects.create(id="-tl-model", directory="/tmp/tl-model")
    event = TimelineEvent.objects.create(
        session_id="s1", project=project, kind=TimelineKind.TURN_STARTED,
        occurred_at=timezone.now(), actor=TimelineActor.AGENT,
    )
    event.refresh_from_db()
    assert event.precision == TimelinePrecision.EXACT
    assert event.data == {} and event.reason == "" and event.caused_by_id is None
    assert event.recorded_at is not None
    assert project.timeline_events.count() == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_timeline_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'TimelineActor'`.

- [ ] **Step 3: Add the model**

Append to `src/twicc/core/models.py`:

```python
class TimelineKind(models.TextChoices):
    """Closed set of timeline event kinds, named ``subject.verb`` (timeline design §4).

    Intervals use ``<subject>.started`` / ``<subject>.ended``; point facts use
    ``<subject>.<past participle>``.
    """

    SESSION_CREATED = "session.created", "Session created"
    TITLE_CHANGED = "title.changed", "Title changed"
    SETTINGS_CHANGED = "settings.changed", "Settings changed"
    PROCESS_STARTED = "process.started", "Process started"
    PROCESS_ENDED = "process.ended", "Process ended"
    TURN_STARTED = "turn.started", "Turn started"
    TURN_ENDED = "turn.ended", "Turn ended"
    AWAITING_INPUT_STARTED = "awaiting_input.started", "Awaiting input started"
    AWAITING_INPUT_ENDED = "awaiting_input.ended", "Awaiting input ended"
    ARCHIVED_STARTED = "archived.started", "Archived started"
    ARCHIVED_ENDED = "archived.ended", "Archived ended"
    HIDDEN_STARTED = "hidden.started", "Hidden started"
    HIDDEN_ENDED = "hidden.ended", "Hidden ended"
    MUTED_STARTED = "muted.started", "Muted started"
    MUTED_ENDED = "muted.ended", "Muted ended"
    PINNED_STARTED = "pinned.started", "Pinned started"
    PINNED_ENDED = "pinned.ended", "Pinned ended"
    BACKEND_STARTED = "backend.started", "Backend started"
    BACKEND_ENDED = "backend.ended", "Backend ended"


class TimelinePrecision(models.TextChoices):
    """How much to trust ``TimelineEvent.occurred_at``."""

    EXACT = "exact", "Observed live"
    DERIVED = "derived", "Derived from a timestamped datum"
    ESTIMATED = "estimated", "Inferred, see data.upper_bound"


class TimelineActor(models.TextChoices):
    """Who caused a timeline event."""

    USER = "user", "User"
    AGENT = "agent", "Agent"
    SYSTEM = "system", "System"
    EXTERNAL = "external", "External"


class TimelineEvent(models.Model):
    """One append-only fact about a session (or about the backend instance).

    ``session_id`` is a plain CharField (like :class:`ProcessRun`): the Session
    row may not exist yet when the first process event is written, and rows must
    survive a purge. ``NULL`` ``session_id`` and ``project`` mean an instance
    event (``backend.*``). Design: ``docs/plans/2026-10-13-session-timeline-design.md``.
    """

    session_id = models.CharField(max_length=200, null=True, blank=True)
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.PROTECT, related_name="timeline_events",
    )
    kind = models.CharField(max_length=40, choices=TimelineKind.choices)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    precision = models.CharField(max_length=10, choices=TimelinePrecision.choices, default=TimelinePrecision.EXACT)
    actor = models.CharField(max_length=10, choices=TimelineActor.choices)
    reason = models.CharField(max_length=30, blank=True, default="")
    caused_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="effects")
    data = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["session_id", "occurred_at", "id"], name="idx_timeline_session"),
            models.Index(fields=["project", "occurred_at", "id"], name="idx_timeline_project"),
            models.Index(fields=["kind", "occurred_at"], name="idx_timeline_kind"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session_id", "dedupe_key"],
                condition=models.Q(dedupe_key__isnull=False),
                name="uniq_timeline_dedupe",
            ),
        ]
```

- [ ] **Step 4: Add the payload registry**

```python
# src/twicc/core/timeline.py
"""Payload registry and pure helpers of the session timeline.

Design: ``docs/plans/2026-10-13-session-timeline-design.md`` (§3-§5).
"""

from __future__ import annotations

from typing import NamedTuple

from twicc.agent.states import AgentState
from twicc.core.models import TimelineKind

#: Version of the payload shapes below. Bump it when a kind's payload changes shape (design §3).
SCHEMA_VERSION = 1

#: Entry surfaces accepted in the reserved ``data["via"]`` key (design §3, ``actor``).
VIA_VALUES = frozenset({"ui", "cli", "mcp"})


class SessionCreatedData(NamedTuple):
    origin: str  # "twicc" | "external" | "sync"
    provider: str | None = None
    settings: dict | None = None  # non-null AgentSettings fields of the created row; origin "twicc" only
    spawned_by: str | None = None  # Session.spawned_by_id


class TitleChangedData(NamedTuple):
    source: str  # "auto" | "user" | "agent"
    new: str
    old: str | None = None  # previous title; None when there was none or it is unknown


class SettingsChangedData(NamedTuple):
    changes: dict  # {field: [old, new]}


class ProcessStartedData(NamedTuple):
    provider: str
    cause: str = "new"  # "new" | "resume"
    twicc_pid: int | None = None
    hybrid: bool = False  # the agent runs in hybrid CLI mode


class ProcessEndedData(NamedTuple):
    # The first four fields repeat ProcessStartedData (inherited by the recording
    # service, see STARTED_OF). All optional: the started row may predate the rollout.
    provider: str | None = None
    cause: str | None = None
    twicc_pid: int | None = None
    hybrid: bool | None = None
    error: str | None = None
    upper_bound: str | None = None  # ISO time of the boot that detected a lost process


class TurnEndedData(NamedTuple):
    # The ProcessRun.background_work_in_progress snapshot ({"subagents": n, "shells": n,
    # "monitors": n, "scheduled_wakeup_at": iso | None, "goal": bool}), or None when
    # nothing runs. Non-None = waiting for the user while work still runs behind the agent.
    background_work: dict | None = None


class AwaitingInputStartedData(NamedTuple):
    request_type: str | None = None
    tool_name: str | None = None


class AwaitingInputEndedData(NamedTuple):
    # Repeats AwaitingInputStartedData (inherited, see STARTED_OF).
    request_type: str | None = None
    tool_name: str | None = None


class PinnedStartedData(NamedTuple):
    scope: str  # "project" | "workspace" | "all"


class PinnedEndedData(NamedTuple):
    scope: str | None = None  # inherited from the started row, see STARTED_OF


class BackendStartedData(NamedTuple):
    pid: int
    version: str | None = None
    boot_id: str | None = None
    machine_restarted: bool | None = None  # None = unknown


class BackendEndedData(NamedTuple):
    clean: bool
    # Repeats BackendStartedData (inherited, see STARTED_OF); all optional.
    pid: int | None = None
    version: str | None = None
    boot_id: str | None = None
    machine_restarted: bool | None = None


#: ``None`` means "no payload allowed" (only the reserved ``via`` key).
PAYLOAD_TYPES: dict[str, type | None] = {
    TimelineKind.SESSION_CREATED: SessionCreatedData,
    TimelineKind.TITLE_CHANGED: TitleChangedData,
    TimelineKind.SETTINGS_CHANGED: SettingsChangedData,
    TimelineKind.PROCESS_STARTED: ProcessStartedData,
    TimelineKind.PROCESS_ENDED: ProcessEndedData,
    TimelineKind.TURN_STARTED: None,
    TimelineKind.TURN_ENDED: TurnEndedData,
    TimelineKind.AWAITING_INPUT_STARTED: AwaitingInputStartedData,
    TimelineKind.AWAITING_INPUT_ENDED: AwaitingInputEndedData,
    TimelineKind.ARCHIVED_STARTED: None,
    TimelineKind.ARCHIVED_ENDED: None,
    TimelineKind.HIDDEN_STARTED: None,
    TimelineKind.HIDDEN_ENDED: None,
    TimelineKind.MUTED_STARTED: None,
    TimelineKind.MUTED_ENDED: None,
    TimelineKind.PINNED_STARTED: PinnedStartedData,
    TimelineKind.PINNED_ENDED: PinnedEndedData,
    TimelineKind.BACKEND_STARTED: BackendStartedData,
    TimelineKind.BACKEND_ENDED: BackendEndedData,
}

#: ``ended`` kind -> its ``started`` kind. An ``ended`` row repeats the data of its
#: OPEN ``started`` row (no newer ``ended`` row) so that each line reads alone
#: (design §3). The recording service applies it in one place (``record_event_sync``); ``turn.*``, ``archived.*``,
#: ``hidden.*`` and ``muted.*`` have no started payload, so they are not listed.
STARTED_OF: dict[str, str] = {
    TimelineKind.AWAITING_INPUT_ENDED: TimelineKind.AWAITING_INPUT_STARTED,
    TimelineKind.PROCESS_ENDED: TimelineKind.PROCESS_STARTED,
    TimelineKind.PINNED_ENDED: TimelineKind.PINNED_STARTED,
    TimelineKind.BACKEND_ENDED: TimelineKind.BACKEND_STARTED,
}


def inherit_started_fields(kind: str, data: dict | None, started_data: dict | None) -> dict:
    """Return ``data`` of the ``ended`` kind ``kind`` completed from ``started_data``.

    Every payload field of ``kind`` that the caller did not provide is copied from
    ``started_data`` (the data of the OPEN ``started`` row: the caller found no
    newer ``ended`` row), when that row has it. A caller-provided field wins, even
    when it is ``None``. Without an open ``started`` row (``started_data`` is
    ``None``) nothing is copied. The reserved ``via`` key
    is never copied. Pure: no validation, see :func:`validate_data`.
    """
    merged = dict(data or {})
    if not started_data:
        return merged
    for name in PAYLOAD_TYPES[kind]._fields:
        if name not in merged and name in started_data:
            merged[name] = started_data[name]
    return merged


def validate_data(kind: str, data: dict | None) -> dict:
    """Return the validated payload of ``kind``, defaults filled in.

    Raises ``ValueError`` on an unknown kind, an unknown or missing key, or a
    bad ``via``. The reserved ``via`` key is accepted on every kind.
    """
    try:
        payload_type = PAYLOAD_TYPES[kind]
    except KeyError:
        raise ValueError(f"unknown timeline kind {kind!r}") from None
    data = dict(data or {})
    via = data.pop("via", None)
    if via is not None and via not in VIA_VALUES:
        raise ValueError(f"invalid via {via!r}")
    if payload_type is None:
        if data:
            raise ValueError(f"{kind} takes no payload, got {sorted(data)}")
        result = {}
    else:
        unknown = set(data) - set(payload_type._fields)
        if unknown:
            raise ValueError(f"{kind}: unknown payload keys {sorted(unknown)}")
        try:
            result = payload_type(**data)._asdict()
        except TypeError as exc:
            raise ValueError(f"{kind}: {exc}") from exc
    if via is not None:
        result["via"] = via
    return result
```

Task 2 appends `process_transition_events` to this file.

- [ ] **Step 5: Generate the migration**

Run: `cd /home/twidi/dev/twicc-poc && TWICC_DATA_DIR=$(mktemp -d) uv run python -m django makemigrations core -n timeline_event --settings=twicc.settings_test`
Expected: creates `src/twicc/core/migrations/0156_timeline_event.py` with one `CreateModel`. Open it and confirm it depends on `('core', '0155_session_hidden_partial_index')`, contains the three indexes and the `uniq_timeline_dedupe` constraint, and imports nothing from `twicc`.

- [ ] **Step 6: Keep every `Project` delete working (`on_delete=PROTECT`)**

`TimelineEvent.project` is `PROTECT` (spec §3), so any `Project.objects.all().delete()` raises `ProtectedError` once timeline rows exist. Find the deletes: `grep -rn "Project.objects.*delete()" src tests`. Known sites:

- `src/twicc/core/management/commands/sync.py` (`sync --reset`, line ~23): delete the timeline rows first.

```python
            # Timeline rows protect their project: they go first.
            TimelineEvent.objects.all().delete()
            Project.objects.all().delete()
```

- `tests/test_agent_run_stop_step.py` (`_reset_db_and_caches`, line ~588): add `TimelineEvent` to the tuple of models deleted before `Project`:

```python
    for model in (AgentInteraction, AgentRunEnd, ToolResultLink, AgentLink, TimelineEvent):
        model.objects.all().delete()
```

Import `TimelineEvent` from `twicc.core.models` in both files. Apply the same fix to every other site the grep finds that runs after timeline rows can exist. Do not change the `PROTECT` rule.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_timeline_model.py tests/test_agent_run_stop_step.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/twicc/core/models.py src/twicc/core/timeline.py src/twicc/core/migrations/0156_timeline_event.py src/twicc/core/management/commands/sync.py tests/test_timeline_model.py tests/test_agent_run_stop_step.py
git commit   # subject: feat(timeline): add the TimelineEvent model and payload registry
```

---

### Task 2: Pure process-transition diff

**Files:**
- Modify: `src/twicc/core/timeline.py`
- Test: `tests/test_timeline_transitions.py`

**Interfaces:**
- Consumes: `AgentState`, `TimelineKind` (already imported in `timeline.py`).
- Produces: `EventSpec(kind: str, data: dict)` (`NamedTuple`); `process_transition_events(*, prev_state, prev_awaiting, prev_work, new_state, new_awaiting, new_work, request_type=None, tool_name=None) -> list[EventSpec]`.

Rules (spec §5, §6): ends before starts. `turn.started` follows the entry into `ASSISTANT_TURN` (no payload). `turn.ended` carries `background_work`, the snapshot of the new row:
- leaving `ASSISTANT_TURN` to a state other than `dead`: `turn.ended` with `new_work` (a snapshot or `None`);
- leaving `ASSISTANT_TURN` to `dead`: `turn.ended` with `background_work=None`, whatever `new_work` is (a dead process runs nothing);
- previous and new state both `user_turn` and `prev_work != new_work` (exact dict equality; `None` against a dict is a change): `turn.ended` again, with `new_work`. No change, no row;
- a work change in any other situation (during `ASSISTANT_TURN`, `starting`, ...), and a death from `user_turn`, emit nothing.

`awaiting_input.*` follows the boolean; the started row carries `request_type` and `tool_name`. On death the caller passes `new_awaiting=False` (and `new_work=None`), so the open inner intervals close; the caller then appends `process.ended`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timeline_transitions.py
from twicc.core.timeline import EventSpec, process_transition_events

WORK = {"subagents": 0, "shells": 2, "monitors": 0, "scheduled_wakeup_at": None, "goal": False}
OTHER_WORK = {"subagents": 0, "shells": 2, "monitors": 1, "scheduled_wakeup_at": None, "goal": False}


def specs(**kw):
    base = dict(prev_state="user_turn", prev_awaiting=False, prev_work=None,
                new_state="user_turn", new_awaiting=False, new_work=None)
    base.update(kw)
    return process_transition_events(**base)


def diff(**kw):
    return [spec.kind for spec in specs(**kw)]


def test_no_change_emits_nothing():
    assert diff() == []
    assert diff(prev_state="starting", new_state="starting") == []
    assert diff(prev_work=WORK, new_work=WORK) == []


def test_entering_and_leaving_the_assistant_turn():
    assert specs(prev_state="starting", new_state="assistant_turn") == [EventSpec("turn.started", {})]
    assert diff(prev_state="user_turn", new_state="assistant_turn") == ["turn.started"]
    assert specs(prev_state="assistant_turn", new_state="user_turn") == [
        EventSpec("turn.ended", {"background_work": None})]


def test_turn_ended_carries_the_background_work_of_the_new_row():
    assert specs(prev_state="assistant_turn", new_state="user_turn", new_work=WORK) == [
        EventSpec("turn.ended", {"background_work": WORK})]


def test_work_changes_while_idle_re_emit_turn_ended_with_the_new_snapshot():
    assert specs(prev_work=None, new_work=WORK) == [EventSpec("turn.ended", {"background_work": WORK})]
    assert specs(prev_work=WORK, new_work=OTHER_WORK) == [EventSpec("turn.ended", {"background_work": OTHER_WORK})]
    assert specs(prev_work=WORK, new_work=None) == [EventSpec("turn.ended", {"background_work": None})]


def test_work_changes_outside_the_idle_state_emit_nothing():
    assert diff(prev_state="assistant_turn", new_state="assistant_turn", new_work=WORK) == []
    assert diff(prev_state="assistant_turn", prev_work=WORK, new_state="assistant_turn") == []
    assert diff(prev_state="starting", new_state="starting", new_work=WORK) == []
    # Entering a turn supersedes a pending "idle but busy" state: only turn.started.
    assert diff(prev_work=WORK, new_state="assistant_turn", new_work=WORK) == ["turn.started"]


def test_awaiting_input_follows_the_flag_and_carries_the_request():
    started = specs(
        prev_state="assistant_turn", new_state="assistant_turn", new_awaiting=True,
        request_type="tool_approval", tool_name="Bash",
    )
    assert started == [EventSpec("awaiting_input.started", {"request_type": "tool_approval", "tool_name": "Bash"})]
    assert diff(prev_state="assistant_turn", prev_awaiting=True,
                new_state="assistant_turn", new_awaiting=False) == ["awaiting_input.ended"]


def test_death_closes_the_turn_and_the_wait_with_no_background_work():
    got = specs(prev_state="assistant_turn", prev_awaiting=True, prev_work=WORK, new_state="dead", new_work=WORK)
    assert got == [EventSpec("turn.ended", {"background_work": None}), EventSpec("awaiting_input.ended", {})]
    assert diff(prev_state="assistant_turn", new_state="dead") == ["turn.ended"]


def test_death_from_user_turn_emits_nothing_even_with_work_running():
    assert diff(new_state="dead") == []
    assert diff(prev_work=WORK, new_state="dead") == []


def test_ends_come_before_starts_in_one_transition():
    assert diff(prev_state="assistant_turn", prev_awaiting=True,
                new_state="user_turn", new_awaiting=False) == ["turn.ended", "awaiting_input.ended"]
    assert diff(prev_state="user_turn", prev_awaiting=False,
                new_state="assistant_turn", new_awaiting=True) == ["turn.started", "awaiting_input.started"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_timeline_transitions.py -v`
Expected: FAIL with `ImportError: cannot import name 'EventSpec'`.

- [ ] **Step 3: Implement**

Append to `src/twicc/core/timeline.py`:

```python
class EventSpec(NamedTuple):
    """One event to record: the kind and its payload (the caller adds the rest)."""

    kind: str
    data: dict


def process_transition_events(
    *,
    prev_state: str,
    prev_awaiting: bool,
    prev_work: dict | None,
    new_state: str,
    new_awaiting: bool,
    new_work: dict | None,
    request_type: str | None = None,
    tool_name: str | None = None,
) -> list[EventSpec]:
    """Events of one ``ProcessRun`` change, ends first, then starts.

    Pure function of the previous and the new row values (design §6). The
    background work is not an interval: ``turn.ended`` carries its snapshot, and
    is emitted again when the snapshot changes while the session stays in
    ``user_turn``. On a transition to ``dead`` the caller passes
    ``new_awaiting=False`` and ``new_work=None`` (the row forces both), so every
    open inner interval closes; the caller then appends ``process.ended``.
    """
    assistant = AgentState.ASSISTANT_TURN.value
    user_turn = AgentState.USER_TURN.value
    dead = AgentState.DEAD.value
    ends: list[EventSpec] = []
    starts: list[EventSpec] = []

    if prev_state == assistant and new_state != assistant:
        # A dead process runs nothing, whatever the caller passed.
        work = None if new_state == dead else new_work
        ends.append(EventSpec(TimelineKind.TURN_ENDED, {"background_work": work}))
    elif prev_state != assistant and new_state == assistant:
        starts.append(EventSpec(TimelineKind.TURN_STARTED, {}))
    elif prev_state == user_turn and new_state == user_turn and prev_work != new_work:
        # Idle, and the work running behind the agent changed.
        ends.append(EventSpec(TimelineKind.TURN_ENDED, {"background_work": new_work}))

    if prev_awaiting and not new_awaiting:
        ends.append(EventSpec(TimelineKind.AWAITING_INPUT_ENDED, {}))
    elif new_awaiting and not prev_awaiting:
        starts.append(EventSpec(
            TimelineKind.AWAITING_INPUT_STARTED, {"request_type": request_type, "tool_name": tool_name},
        ))

    return ends + starts
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_timeline_transitions.py tests/test_timeline_model.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/core/timeline.py tests/test_timeline_transitions.py
git commit   # subject: feat(timeline): compute the events of a process transition
```

---

### Task 3: The recording service

**Files:**
- Create: `src/twicc/core/services/session_timeline.py`
- Create: `tests/timeline_helpers.py`
- Test: `tests/test_timeline_service.py`

**Interfaces:**
- Consumes: Task 1 models, `validate_data`, `STARTED_OF`, `inherit_started_fields`.
- Produces:
  - `record_event_sync(kind, *, session_id=None, project_id=None, occurred_at=None, precision=TimelinePrecision.EXACT, actor, reason="", caused_by=None, data=None, dedupe_key=None) -> TimelineEvent | None` — never raises, uses a savepoint, returns `None` on failure or dedupe hit. `caused_by` is an event, an id, or `None`. For a kind in `STARTED_OF` (`awaiting_input.ended`, `process.ended`, `pinned.ended`, `backend.ended`) it first fills every payload field the caller did not provide from the data of the OPEN `started` row: the latest row of the matching `started` kind with the same `session_id` (instance events: `NULL`), provided no row of the `ended` kind with the same `session_id` has a greater id; otherwise nothing is inherited (design §3). This is the only place that rule is implemented.
  - `async record_event(kind, **kwargs)` — same arguments; runs `record_event_sync` in a thread under `run_under_db_write_lock` (reentrant for the task already holding the lock).
  - `latest_event_id(session_id: str, kind: str) -> int | None`.
  - `project_id_of(session_id: str) -> str | None`.
  - `record_session_created(session, *, origin: str) -> TimelineEvent | None` — writes `session.created` (with `settings` and `spawned_by` read from the row, see Step 3), then the initial `hidden.started` / `muted.started` rows for the flags already set, all `caused_by` the created event.
  - `record_title_change(session_id: str, title: str, *, source: str, old: str | None = None) -> None` (used by Task 8).
  - Test helpers: `run_with_writer(factory)`, `kinds(session_id)`.

- [ ] **Step 1: Write the helpers and failing tests**

```python
# tests/timeline_helpers.py
"""Shared helpers of the timeline tests."""

import asyncio

from twicc.core.models import TimelineEvent
from twicc.providers import db_writer


def run_with_writer(factory):
    """Run ``await factory()`` with the DB writer (and its write lock) started."""

    async def runner():
        db_writer.start_db_writer()
        try:
            return await factory()
        finally:
            await db_writer.stop_db_writer()

    return asyncio.run(runner())


def kinds(session_id):
    """Kinds recorded for ``session_id``, in order."""
    return list(
        TimelineEvent.objects.filter(session_id=session_id).order_by("occurred_at", "id").values_list("kind", flat=True)
    )
```

Check how other tests import `tests/live_sync_helpers.py` (`grep -n "live_sync_helpers" tests/*.py | head -3`) and use the same import style for `timeline_helpers` in every test file of this plan (`from tests.timeline_helpers import ...` below stands for that style).

```python
# tests/test_timeline_service.py
import pytest
from django.db import transaction
from django.utils import timezone

from twicc.core.models import (
    Project, Session, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.services.session_timeline import (
    latest_event_id, project_id_of, record_event, record_event_sync, record_session_created,
)
from tests.timeline_helpers import kinds, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-service", directory="/tmp/tl-service")


def test_record_event_sync_writes_one_row_with_defaults(project):
    before = timezone.now()
    event = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", project_id=project.id,
                              actor=TimelineActor.AGENT)
    assert event is not None
    event.refresh_from_db()
    assert event.project_id == project.id and event.precision == TimelinePrecision.EXACT
    assert event.occurred_at >= before and event.data == {}


def test_invalid_payload_returns_none_and_writes_nothing(project):
    assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT,
                             data={"bogus": 1}) is None
    assert TimelineEvent.objects.count() == 0


def test_dedupe_key_makes_a_second_write_a_noop(project):
    kwargs = dict(session_id="s1", actor=TimelineActor.SYSTEM, dedupe_key="k1", data={"origin": "sync"})
    assert record_event_sync(TimelineKind.SESSION_CREATED, **kwargs) is not None
    assert record_event_sync(TimelineKind.SESSION_CREATED, **kwargs) is None
    assert TimelineEvent.objects.count() == 1


def test_a_failing_insert_does_not_poison_the_callers_transaction(project):
    with transaction.atomic():
        Project.objects.create(id="-tl-a", directory="/tmp/a")
        # actor=None violates NOT NULL: the INSERT fails at SQL level.
        assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=None) is None
        Project.objects.create(id="-tl-b", directory="/tmp/b")
    assert Project.objects.filter(id__in=["-tl-a", "-tl-b"]).count() == 2


def test_caused_by_accepts_an_event_or_an_id(project):
    cause = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", actor=TimelineActor.USER)
    by_event = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", actor=TimelineActor.USER,
                                 caused_by=cause)
    by_id = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", actor=TimelineActor.USER,
                              caused_by=cause.id)
    assert by_event.caused_by_id == by_id.caused_by_id == cause.id


def test_latest_event_id_and_project_id_of(project):
    Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex")
    first = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", actor=TimelineActor.USER)
    second = record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", actor=TimelineActor.USER)
    assert latest_event_id("s1", TimelineKind.ARCHIVED_STARTED) == second.id != first.id
    assert latest_event_id("s1", TimelineKind.HIDDEN_STARTED) is None
    assert project_id_of("s1") == project.id and project_id_of("nope") is None


def test_an_ended_row_inherits_the_data_of_the_open_started_row_of_its_session(project):
    started = dict(session_id="s1", actor=TimelineActor.USER)
    record_event_sync(TimelineKind.PROCESS_STARTED, data={"provider": "claude_code", "twicc_pid": 1}, **started)
    record_event_sync(TimelineKind.PROCESS_STARTED,
                      data={"provider": "codex", "cause": "resume", "twicc_pid": 7, "hybrid": True}, **started)
    record_event_sync(TimelineKind.PROCESS_STARTED, session_id="other", actor=TimelineActor.USER,
                      data={"provider": "claude_code"})
    ended = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", actor=TimelineActor.SYSTEM,
                              data={"error": "boom"})
    assert ended.data == {"provider": "codex", "cause": "resume", "twicc_pid": 7, "hybrid": True,
                          "error": "boom", "upper_bound": None}


def test_a_closed_interval_is_never_inherited(project):
    started = dict(session_id="s1", actor=TimelineActor.USER)
    record_event_sync(TimelineKind.PROCESS_STARTED, data={"provider": "codex", "twicc_pid": 7}, **started)
    first = record_event_sync(TimelineKind.PROCESS_ENDED, **started)
    assert first.data["provider"] == "codex"
    # No new ``started`` row (lost write, or a second stale row at boot): nothing to inherit.
    second = record_event_sync(TimelineKind.PROCESS_ENDED, **started)
    assert second.data == {"provider": None, "cause": None, "twicc_pid": None, "hybrid": None,
                           "error": None, "upper_bound": None}


def test_a_lost_start_does_not_fall_back_to_an_older_closed_interval(project):
    started = dict(session_id="s1", actor=TimelineActor.USER)
    record_event_sync(TimelineKind.PROCESS_STARTED, data={"provider": "codex", "twicc_pid": 7}, **started)
    record_event_sync(TimelineKind.PROCESS_ENDED, **started)
    # The second ``process.started`` write was lost: only its ``ended`` row exists.
    assert record_event_sync(TimelineKind.PROCESS_ENDED, **started).data["provider"] is None


def test_a_reopened_interval_inherits_its_own_data(project):
    started = dict(session_id="s1", actor=TimelineActor.USER)
    record_event_sync(TimelineKind.PROCESS_STARTED, data={"provider": "codex", "twicc_pid": 7}, **started)
    record_event_sync(TimelineKind.PROCESS_ENDED, **started)
    record_event_sync(TimelineKind.PROCESS_STARTED, data={"provider": "claude_code", "twicc_pid": 8}, **started)
    ended = record_event_sync(TimelineKind.PROCESS_ENDED, **started)
    assert ended.data["provider"] == "claude_code" and ended.data["twicc_pid"] == 8


def test_a_pin_scope_change_inherits_the_scope_of_each_interval(project):
    pin = dict(session_id="s1", actor=TimelineActor.USER)
    record_event_sync(TimelineKind.PINNED_STARTED, data={"scope": "project"}, **pin)
    assert record_event_sync(TimelineKind.PINNED_ENDED, **pin).data == {"scope": "project"}
    record_event_sync(TimelineKind.PINNED_STARTED, data={"scope": "all"}, **pin)
    assert record_event_sync(TimelineKind.PINNED_ENDED, **pin).data == {"scope": "all"}


def test_a_field_provided_by_the_caller_wins_over_the_started_row(project):
    record_event_sync(TimelineKind.PROCESS_STARTED, session_id="s1", actor=TimelineActor.USER,
                      data={"provider": "codex", "twicc_pid": 7})
    ended = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", actor=TimelineActor.SYSTEM,
                              data={"provider": "claude_code", "twicc_pid": None})
    assert ended.data["provider"] == "claude_code" and ended.data["twicc_pid"] is None
    assert ended.data["cause"] == "new"  # still inherited


def test_an_ended_row_without_a_started_row_keeps_its_fields_null(project):
    ended = record_event_sync(TimelineKind.PROCESS_ENDED, session_id="s1", actor=TimelineActor.SYSTEM)
    assert ended.data == {"provider": None, "cause": None, "twicc_pid": None, "hybrid": None,
                          "error": None, "upper_bound": None}
    pinned = record_event_sync(TimelineKind.PINNED_ENDED, session_id="s1", actor=TimelineActor.USER)
    assert pinned.data == {"scope": None}


def test_pin_and_awaiting_input_ended_rows_inherit_too(project):
    record_event_sync(TimelineKind.PINNED_STARTED, session_id="s1", actor=TimelineActor.USER, data={"scope": "all"})
    record_event_sync(TimelineKind.AWAITING_INPUT_STARTED, session_id="s1", actor=TimelineActor.AGENT,
                      data={"request_type": "tool_approval", "tool_name": "Bash"})
    assert record_event_sync(TimelineKind.PINNED_ENDED, session_id="s1", actor=TimelineActor.USER).data == {"scope": "all"}
    assert record_event_sync(TimelineKind.AWAITING_INPUT_ENDED, session_id="s1", actor=TimelineActor.AGENT).data == {
        "request_type": "tool_approval", "tool_name": "Bash"}


def test_instance_events_inherit_from_the_latest_instance_row(project):
    record_event_sync(TimelineKind.BACKEND_STARTED, actor=TimelineActor.SYSTEM,
                      data={"pid": 5, "version": "1.0", "boot_id": "b", "machine_restarted": False})
    ended = record_event_sync(TimelineKind.BACKEND_ENDED, actor=TimelineActor.SYSTEM, data={"clean": True})
    assert ended.data == {"clean": True, "pid": 5, "version": "1.0", "boot_id": "b", "machine_restarted": False}


def test_kinds_without_a_started_payload_copy_nothing(project):
    record_event_sync(TimelineKind.ARCHIVED_STARTED, session_id="s1", actor=TimelineActor.USER)
    assert record_event_sync(TimelineKind.ARCHIVED_ENDED, session_id="s1", actor=TimelineActor.USER).data == {}


def test_async_record_event_runs_under_the_write_lock(project):
    event = run_with_writer(lambda: record_event(
        TimelineKind.TURN_STARTED, session_id="s1", project_id=project.id, actor=TimelineActor.AGENT))
    assert event is not None and kinds("s1") == ["turn.started"]


def test_record_session_created_writes_the_initial_flags_caused_by_the_creation(project):
    session = Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex", hidden=True,
                                     mute_on_user_turn=True)
    record_session_created(session, origin="twicc")
    rows = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))
    assert [r.kind for r in rows] == ["session.created", "hidden.started", "muted.started"]
    assert rows[0].data == {"origin": "twicc", "provider": "codex", "settings": {}, "spawned_by": None}
    assert rows[1].caused_by_id == rows[2].caused_by_id == rows[0].id
    assert rows[0].project_id == project.id


def test_record_session_created_reads_the_settings_and_the_spawner_from_the_row(project):
    Session.objects.create(id="boss", file_path="boss.jsonl", project=project, provider="codex")
    session = Session.objects.create(
        id="s1", file_path="s1.jsonl", project=project, provider="codex", spawned_by_id="boss",
        selected_model="gpt-x", effort="high", thinking_enabled=False,
    )
    record_session_created(session, origin="twicc")
    # Only the non-null fields; ``False`` is a value, not an absence.
    assert TimelineEvent.objects.get(session_id="s1").data == {
        "origin": "twicc", "provider": "codex", "spawned_by": "boss",
        "settings": {"selected_model": "gpt-x", "effort": "high", "thinking_enabled": False},
    }


def test_record_session_created_keeps_the_settings_for_twicc_sessions_only(project):
    Session.objects.create(id="boss", file_path="boss.jsonl", project=project, provider="codex")
    for origin in ("external", "sync"):
        session = Session.objects.create(
            id=f"s-{origin}", file_path=f"{origin}.jsonl", project=project, provider="codex",
            spawned_by_id="boss", selected_model="gpt-x",
        )
        record_session_created(session, origin=origin)
        data = TimelineEvent.objects.get(session_id=session.id).data
        assert data["settings"] is None and data["spawned_by"] == "boss"


def test_record_title_change_skips_subagents_and_unknown_sessions(project):
    from twicc.core.models import SessionType
    from twicc.core.services.session_timeline import record_title_change

    parent = Session.objects.create(id="p", file_path="p.jsonl", project=project, provider="codex")
    Session.objects.create(id="c", file_path="c.jsonl", project=project, provider="codex",
                           type=SessionType.SUBAGENT, parent_session=parent)
    record_title_change("c", "Child title", source="agent")
    record_title_change("nope", "Ghost", source="agent")
    record_title_change("p", "Parent title", source="agent", old="Before")
    assert kinds("c") == [] and kinds("nope") == [] and kinds("p") == ["title.changed"]
    assert TimelineEvent.objects.get(session_id="p").data == {"source": "agent", "new": "Parent title", "old": "Before"}
    record_title_change("p", "Next", source="user")
    assert TimelineEvent.objects.filter(session_id="p").order_by("-id").first().data["old"] is None


def test_record_session_created_without_flags_writes_one_row(project):
    session = Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex")
    record_session_created(session, origin="sync")
    assert kinds("s1") == ["session.created"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_timeline_service.py -v`
Expected: FAIL with `ModuleNotFoundError: twicc.core.services.session_timeline`.

- [ ] **Step 3: Implement**

```python
# src/twicc/core/services/session_timeline.py
"""Recording of timeline events (design §6).

Every hook calls :func:`record_event_sync` (from code already running in a
worker thread, inside or outside a transaction) or :func:`record_event` (from
async code). Neither ever raises: a failed timeline write is logged and must
never block or undo the user action it describes.
"""

from __future__ import annotations

import asyncio
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from twicc.core.models import (
    Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.timeline import STARTED_OF, inherit_started_fields, validate_data
from twicc.providers.db_writer import run_under_db_write_lock

logger = logging.getLogger(__name__)


def record_event_sync(
    kind: str,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
    occurred_at=None,
    precision: str = TimelinePrecision.EXACT,
    actor: str,
    reason: str = "",
    caused_by: TimelineEvent | int | None = None,
    data: dict | None = None,
    dedupe_key: str | None = None,
) -> TimelineEvent | None:
    """Write one event. Returns the row, or ``None`` on failure or dedupe hit.

    The insert runs in its own savepoint: when the caller already holds a
    transaction, a failed insert rolls back only this row. For an ``ended`` kind
    listed in ``STARTED_OF``, the payload fields the caller did not provide are
    copied from the open ``started`` row of the same session (design §3).
    """
    try:
        if kind in STARTED_OF:
            data = inherit_started_fields(kind, data, _open_started_data(session_id, kind))
        payload = validate_data(kind, data)
        caused_by_id = caused_by.pk if isinstance(caused_by, TimelineEvent) else caused_by
        with transaction.atomic():
            if (
                dedupe_key is not None
                and TimelineEvent.objects.filter(session_id=session_id, dedupe_key=dedupe_key).exists()
            ):
                return None
            return TimelineEvent.objects.create(
                session_id=session_id,
                project_id=project_id,
                kind=kind,
                occurred_at=occurred_at or timezone.now(),
                precision=precision,
                actor=actor,
                reason=reason,
                caused_by_id=caused_by_id,
                data=payload,
                dedupe_key=dedupe_key,
            )
    except IntegrityError:
        if dedupe_key is not None:
            logger.debug("Timeline event %s deduplicated by the database for %s", kind, session_id)
        else:
            logger.exception("Timeline event %s could not be written for session %s", kind, session_id)
        return None
    except Exception:
        logger.exception("Timeline event %s could not be written for session %s", kind, session_id)
        return None


async def record_event(kind: str, **kwargs) -> TimelineEvent | None:
    """Async twin of :func:`record_event_sync`, under the DB write lock.

    The lock is reentrant for the task that already holds it, so this is safe
    to call from inside another ``run_under_db_write_lock`` callback.
    """
    return await run_under_db_write_lock(
        lambda: asyncio.to_thread(lambda: record_event_sync(kind, **kwargs))
    )


def latest_event_id(session_id: str, kind: str) -> int | None:
    """Id of the most recent (highest id) event of ``kind`` for ``session_id``, if any."""
    return (
        TimelineEvent.objects.filter(session_id=session_id, kind=kind)
        .order_by("-id")
        .values_list("id", flat=True)
        .first()
    )


def _open_started_data(session_id: str | None, ended_kind: str) -> dict | None:
    """``data`` of the open ``started`` row matching ``ended_kind``, or ``None``.

    The open row is the latest ``started`` row of ``session_id`` (``None`` = instance
    events), by id, with no row of ``ended_kind`` for the same session and a greater
    id (ids, never ``occurred_at``: lost-end rows are back-dated): a closed interval
    is never inherited.
    """
    started = (
        TimelineEvent.objects.filter(session_id=session_id, kind=STARTED_OF[ended_kind])
        .order_by("-id")
        .values("id", "data")
        .first()
    )
    if started is None:
        return None
    if TimelineEvent.objects.filter(session_id=session_id, kind=ended_kind, id__gt=started["id"]).exists():
        return None
    return started["data"]


def project_id_of(session_id: str) -> str | None:
    """Project id of a session row, or ``None`` when the row does not exist."""
    return Session.objects.filter(id=session_id).values_list("project_id", flat=True).first()


def record_session_created(session: Session, *, origin: str) -> TimelineEvent | None:
    """Write ``session.created`` and the initial flag intervals of a new row.

    A session created already hidden or muted gets the matching ``*.started``
    rows, same ``occurred_at``, ``caused_by`` the creation event (design §5).
    ``pinned`` is never set at creation. Sync: call from worker-thread code.

    ``settings`` and ``spawned_by`` are read from the row just created (the
    stored, clamped values, not the request). ``settings`` is kept only for
    ``origin == "twicc"``; ``spawned_by`` is ``Session.spawned_by_id``.
    """
    from twicc.providers.helpers import AgentSettings

    now = timezone.now()
    common = dict(session_id=session.id, project_id=session.project_id, occurred_at=now)
    settings = None
    if origin == "twicc":
        settings = {
            name: value for name, value in AgentSettings.from_session(session)._asdict().items()
            if value is not None
        }
    created = record_event_sync(
        TimelineKind.SESSION_CREATED, actor=TimelineActor.SYSTEM,
        data={"origin": origin, "provider": session.provider, "settings": settings,
              "spawned_by": session.spawned_by_id}, **common,
    )
    if created is None:
        return None
    if session.hidden:
        record_event_sync(TimelineKind.HIDDEN_STARTED, actor=TimelineActor.USER, caused_by=created, **common)
    if session.mute_on_user_turn:
        record_event_sync(TimelineKind.MUTED_STARTED, actor=TimelineActor.USER, caused_by=created, **common)
    return created


def record_title_change(session_id: str, title: str, *, source: str, old: str | None = None) -> None:
    """Write ``title.changed``. ``source`` is ``"auto"``, ``"user"`` or ``"agent"``.

    ``old`` is the previous title when the caller knows it (``None`` otherwise).

    Subagent sessions get no event (decision D2): the compute fills titles for
    every session it processes, subagents included.
    """
    row = Session.objects.filter(id=session_id).values_list("project_id", "type").first()
    if row is None or row[1] == SessionType.SUBAGENT:
        return
    record_event_sync(
        TimelineKind.TITLE_CHANGED, session_id=session_id, project_id=row[0],
        actor=TimelineActor.USER if source == "user" else TimelineActor.AGENT,
        data={"source": source, "new": title, "old": old},
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_timeline_service.py -v`
Expected: all PASS. If the module-level import of `twicc.providers.db_writer` creates a circular import, move it into `record_event` and re-run.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/core/services/session_timeline.py tests/timeline_helpers.py tests/test_timeline_service.py
git commit   # subject: feat(timeline): add the event recording service
```

---

### Task 4: Flag hooks — archived, pinned, muted, hidden

**Files:**
- Modify: `src/twicc/core/services/session_update.py` (`apply_session_archived_change` ~line 410, `apply_session_pinned_change` ~596, `apply_session_mute_on_user_turn_change` ~619)
- Modify: `src/twicc/core/services/session_visibility.py` (`_apply_visibility_flag` ~line 117)
- Test: `tests/test_timeline_flag_hooks.py`

**Interfaces:**
- Consumes: `record_event_sync` (Task 3).
- Produces: nothing new; the four functions keep their signatures.

Rules: write only on a real flip; `actor=TimelineActor.USER`. Archive with `also_unpin` on a pinned session also writes `pinned.ended`, `caused_by` the `archived.started` row. `pinned` `None`→scope writes `pinned.started` (`data.scope`); scope→`None` writes `pinned.ended`; scope→other scope writes `pinned.ended` then `pinned.started` (same `occurred_at`, the second `caused_by` the first). The `scope` of every `pinned.ended` is inherited from the open `pinned.started` by the recording service (Task 3): the hooks never pass it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timeline_flag_hooks.py
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services.session_update import (
    apply_session_archived_change, apply_session_mute_on_user_turn_change, apply_session_pinned_change,
)
from tests.timeline_helpers import kinds, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-flags", directory="/tmp/tl-flags")


@pytest.fixture(autouse=True)
def no_agent_side_effects(monkeypatch):
    from twicc import terminal
    from twicc.agent import registry

    monkeypatch.setattr(registry, "get_agent_manager_registry",
                        lambda: SimpleNamespace(kill_agent=AsyncMock()))
    monkeypatch.setattr(terminal, "kill_all_tmux_terminals", lambda scope: None)


def make(project, **kw):
    return Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex", **kw)


@pytest.mark.parametrize("before,after,expected", [
    (False, True, ["archived.started"]),
    (True, False, ["archived.ended"]),
    (True, True, []),
    (False, False, []),
])
def test_archive_writes_only_on_a_real_flip(project, before, after, expected):
    session = make(project, archived=before)
    run_with_writer(lambda: apply_session_archived_change(session, after))
    assert kinds("s1") == expected


def test_archive_with_also_unpin_writes_pinned_ended_caused_by_the_archive(project):
    session = make(project)
    run_with_writer(lambda: apply_session_pinned_change(session, "project"))
    run_with_writer(lambda: apply_session_archived_change(session, True, also_unpin=True))
    rows = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))
    assert [r.kind for r in rows] == ["pinned.started", "archived.started", "pinned.ended"]
    assert rows[2].caused_by_id == rows[1].id
    assert rows[2].data == {"scope": "project"}  # inherited, not passed by the hook
    assert rows[1].actor == "user" and rows[1].project_id == project.id


def test_archive_with_also_unpin_on_an_unpinned_session_writes_no_pinned_event(project):
    session = make(project)
    run_with_writer(lambda: apply_session_archived_change(session, True, also_unpin=True))
    assert kinds("s1") == ["archived.started"]


def test_pinning_writes_started_with_the_scope(project):
    session = make(project)
    run_with_writer(lambda: apply_session_pinned_change(session, "project"))
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "pinned.started" and row.data == {"scope": "project"} and row.caused_by_id is None


def test_unpinning_writes_ended_with_the_inherited_scope(project):
    session = make(project)
    run_with_writer(lambda: apply_session_pinned_change(session, "all"))
    run_with_writer(lambda: apply_session_pinned_change(session, None))
    rows = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))
    assert [(r.kind, r.data) for r in rows] == [
        ("pinned.started", {"scope": "all"}), ("pinned.ended", {"scope": "all"}),
    ]


def test_changing_the_scope_ends_the_old_pin_then_starts_the_new_one(project):
    session = make(project)
    run_with_writer(lambda: apply_session_pinned_change(session, "project"))
    run_with_writer(lambda: apply_session_pinned_change(session, "all"))
    rows = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))
    assert [(r.kind, r.data["scope"]) for r in rows] == [
        ("pinned.started", "project"), ("pinned.ended", "project"), ("pinned.started", "all"),
    ]
    assert rows[2].caused_by_id == rows[1].id and rows[1].caused_by_id is None
    assert rows[1].occurred_at == rows[2].occurred_at


@pytest.mark.parametrize("value", [None, "workspace"])
def test_pin_to_the_same_value_writes_nothing(project, value):
    session = make(project, pinned=value)
    run_with_writer(lambda: apply_session_pinned_change(session, value))
    assert kinds("s1") == []


@pytest.mark.parametrize("before,after,expected", [
    (False, True, ["muted.started"]), (True, False, ["muted.ended"]), (True, True, []), (False, False, []),
])
def test_mute_writes_only_on_a_real_flip(project, before, after, expected):
    session = make(project, mute_on_user_turn=before)
    run_with_writer(lambda: apply_session_mute_on_user_turn_change(session, after))
    assert kinds("s1") == expected


def hide_unhide(session, hide):
    from twicc.core.services import session_visibility

    async def go():
        fn = session_visibility.hide_session if hide else session_visibility.unhide_session
        return await fn(session)

    with patch.object(session_visibility, "_push_hidden_to_live_agent", lambda *a: None), \
            patch.object(session_visibility, "_broadcast_session_removed", AsyncMock()), \
            patch.object(session_visibility, "_broadcast_project_updated", AsyncMock()):
        return run_with_writer(go)


def test_hide_and_unhide_write_one_event_each(project):
    session = make(project, permission_mode="yolo", question_widget=False)
    assert hide_unhide(session, True).success
    assert kinds("s1") == ["hidden.started"]
    assert hide_unhide(session, False).success
    assert kinds("s1") == ["hidden.started", "hidden.ended"]


def test_hiding_an_already_hidden_session_writes_nothing(project):
    session = make(project, hidden=True, permission_mode="yolo", question_widget=False)
    assert hide_unhide(session, True).success
    assert kinds("s1") == []
```

Before running, read `tests/test_agent_hidden_broadcast_gate.py::test_hide_session_mutes_the_live_agent_before_the_recompute` and `test_unhide_session_restates_the_live_agent_state_after_the_row`: the patches in `hide_unhide` must match the ones those tests use (add the extra broadcast helpers `unhide_session` calls, if any).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_timeline_flag_hooks.py -v`
Expected: FAIL (assertions on empty `kinds(...)`).

- [ ] **Step 3: Hook archive**

In `apply_session_archived_change`, replace the block that sets `fields` and saves:

```python
    was_archived = session.archived
    was_pinned = session.pinned
    fields = ["archived"]
    session.archived = archived
    if also_unpin:
        session.pinned = None
        fields.append("pinned")

    async def _save_and_record() -> None:
        await session.asave(update_fields=fields)
        archive_event = None
        if archived != was_archived:
            archive_event = await asyncio.to_thread(
                record_event_sync,
                TimelineKind.ARCHIVED_STARTED if archived else TimelineKind.ARCHIVED_ENDED,
                session_id=session.id, project_id=session.project_id, actor=TimelineActor.USER,
            )
        if also_unpin and was_pinned is not None:
            # ``also_unpin`` clears ``pinned`` itself: the pin service is not called.
            # The ``scope`` of the row is inherited from ``pinned.started`` by the service.
            await asyncio.to_thread(
                record_event_sync, TimelineKind.PINNED_ENDED,
                session_id=session.id, project_id=session.project_id, actor=TimelineActor.USER,
                caused_by=archive_event,
            )

    await run_under_db_write_lock(_save_and_record)
```

Add to the module imports: `from django.utils import timezone` (if absent), `from twicc.core.models import TimelineActor, TimelineKind` and `from twicc.core.services.session_timeline import record_event_sync` (match the file's existing import style; keep them at module level unless that creates a cycle).

- [ ] **Step 4: Hook pin and mute**

```python
async def apply_session_pinned_change(session, pinned) -> None:
    ...docstring unchanged...
    was_pinned = session.pinned
    session.pinned = pinned

    def _record() -> None:
        # One ``occurred_at`` for the pair of a scope change. ``pinned.ended`` is
        # written first, so its inherited ``scope`` is the OLD one (the latest
        # ``pinned.started`` is still the old pin); the new ``started`` row is
        # ``caused_by`` it.
        common = dict(
            session_id=session.id, project_id=session.project_id, actor=TimelineActor.USER,
            occurred_at=timezone.now(),
        )
        ended = None
        if was_pinned is not None:
            ended = record_event_sync(TimelineKind.PINNED_ENDED, **common)
        if pinned is not None:
            record_event_sync(
                TimelineKind.PINNED_STARTED, caused_by=ended, data={"scope": pinned}, **common,
            )

    async def _save_and_record() -> None:
        await session.asave(update_fields=["pinned"])
        if pinned != was_pinned:
            await asyncio.to_thread(_record)

    await run_under_db_write_lock(_save_and_record)


async def apply_session_mute_on_user_turn_change(session, mute_on_user_turn: bool) -> None:
    """Persist the per-session finished-working notification gate."""
    was_muted = session.mute_on_user_turn
    session.mute_on_user_turn = mute_on_user_turn

    async def _save_and_record() -> None:
        await session.asave(update_fields=["mute_on_user_turn"])
        if mute_on_user_turn != was_muted:
            await asyncio.to_thread(
                record_event_sync,
                TimelineKind.MUTED_STARTED if mute_on_user_turn else TimelineKind.MUTED_ENDED,
                session_id=session.id, project_id=session.project_id, actor=TimelineActor.USER,
            )

    await run_under_db_write_lock(_save_and_record)
```

- [ ] **Step 5: Hook hidden**

`session_visibility._apply_visibility_flag` already runs inside `transaction.atomic()` in a worker thread: record in the same transaction.

```python
    with transaction.atomic():
        session = Session.objects.get(id=session_id)
        was_hidden = session.hidden
        before = session_contribution(session)
        session.hidden = new_hidden
        session.save(update_fields=['hidden'])
        apply_contribution_changes([], [], before_sessions=[before],
            after_sessions=[session_contribution(session)], repair=True)
        if was_hidden != new_hidden:
            record_event_sync(
                TimelineKind.HIDDEN_STARTED if new_hidden else TimelineKind.HIDDEN_ENDED,
                session_id=session.id, project_id=session.project_id, actor=TimelineActor.USER,
            )
        session.refresh_from_db()
    return session
```

Import inside the function (the module imports lazily there): `from twicc.core.models import TimelineActor, TimelineKind` and `from twicc.core.services.session_timeline import record_event_sync`.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_timeline_flag_hooks.py tests/test_title_check_triggers.py tests/test_agent_hidden_broadcast_gate.py -v`
Expected: all PASS (the last two prove the existing behaviour is intact).

- [ ] **Step 7: Commit**

```bash
git add src/twicc/core/services/session_update.py src/twicc/core/services/session_visibility.py tests/test_timeline_flag_hooks.py
git commit   # subject: feat(timeline): record archive, pin, mute and hide changes
```

---

### Task 5: Bulk archive hook

**Files:**
- Modify: `src/twicc/core/services/session_update.py` (new function `archive_sessions_bulk`)
- Modify: `src/twicc/views.py` (`bulk_archive_sessions`, the `aupdate` at ~line 1453)
- Test: `tests/test_timeline_bulk_archive.py`

**Interfaces:**
- Consumes: `record_event_sync` (Task 3).
- Produces: `archive_sessions_bulk(ids: set[str]) -> list[str]` — sync; archives all `ids`, returns the ids whose `archived` was still false (the ones that flipped).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timeline_bulk_archive.py
import pytest

from twicc.core.models import Project, Session
from twicc.core.services.session_update import archive_sessions_bulk
from tests.timeline_helpers import kinds

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-bulk", directory="/tmp/tl-bulk")


def test_bulk_archive_flips_all_and_records_one_event_per_flipped_session(project):
    for sid in ("a", "b"):
        Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex")
    flipped = archive_sessions_bulk({"a", "b"})
    assert sorted(flipped) == ["a", "b"]
    assert Session.objects.filter(archived=True).count() == 2
    assert kinds("a") == kinds("b") == ["archived.started"]


def test_a_session_archived_meanwhile_records_nothing(project):
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex")
    Session.objects.create(id="b", file_path="b.jsonl", project=project, provider="codex", archived=True)
    assert archive_sessions_bulk({"a", "b"}) == ["a"]
    assert kinds("a") == ["archived.started"] and kinds("b") == []


def test_search_version_is_reset_for_every_requested_session(project):
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex", search_version=7)
    archive_sessions_bulk({"a"})
    assert Session.objects.get(id="a").search_version == 0


def test_empty_set_is_a_noop():
    assert archive_sessions_bulk(set()) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_timeline_bulk_archive.py -v`
Expected: FAIL with `ImportError: cannot import name 'archive_sessions_bulk'`.

- [ ] **Step 3: Implement**

Add to `session_update.py`:

```python
def archive_sessions_bulk(ids: set[str]) -> list[str]:
    """Archive ``ids`` in one transaction and record ``archived.started`` per flipped session.

    Sync; call under the DB write lock from a worker thread. The caller computed
    ``ids`` before taking the lock, so another request may have archived some of
    them meanwhile: events are written only for the sessions whose ``archived``
    is still false here (design §6). The endpoint's candidate filter excluded
    active, pinned and hidden sessions when it built ``ids``, but the write below
    filters only by id: the timeline records exactly the sessions that flipped. ``search_version`` is reset for every id,
    as before, so an interrupted re-index is picked up at the next boot.
    """
    from twicc.core.models import Session

    if not ids:
        return []
    with transaction.atomic():
        to_flip = list(
            Session.objects.filter(id__in=ids, archived=False).values_list("id", "project_id")
        )
        Session.objects.filter(id__in=ids).update(archived=True, search_version=0)
        for session_id, project_id in to_flip:
            record_event_sync(
                TimelineKind.ARCHIVED_STARTED, session_id=session_id, project_id=project_id,
                actor=TimelineActor.USER,
            )
    return [session_id for session_id, _ in to_flip]
```

In `views.py`, replace the `aupdate` call:

```python
    await run_under_db_write_lock(
        lambda: asyncio.to_thread(archive_sessions_bulk, ids)
    )
```

and add `from twicc.core.services.session_update import archive_sessions_bulk` inline just before that call (`bulk_archive_sessions` has no local imports; the module-level imports of `views.py` stay untouched).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_timeline_bulk_archive.py -v && uv run pytest tests -k "bulk_archive or archive" -v`
Expected: PASS; no regression in existing archive tests.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/core/services/session_update.py src/twicc/views.py tests/test_timeline_bulk_archive.py
git commit   # subject: feat(timeline): record the bulk archive of old sessions
```

---

### Task 6: `session.created` hooks

**Files:**
- Modify: `src/twicc/providers/sessions_watcher.py` (`create_session_sync`, ~line 593)
- Modify: `src/twicc/providers/db_writer.py` (`_apply_create_session_payload`, ~line 2389)
- Test: `tests/test_timeline_session_created.py`

**Interfaces:**
- Consumes: `record_session_created` (Task 3).

Rules: only `type=SESSION` rows get events (D2). Watcher origin is `"twicc"` when a `ProcessRun` already exists for the session id (TwiCC creates it before the JSONL file appears), else `"external"`. Initial sync uses `"sync"`. `record_session_created` (Task 3) reads `settings` (origin `"twicc"` only) and `spawned_by` from the created row, so the hooks pass nothing more. In the watcher the settings reach the row through the `agent_settings` argument and `spawned_by` through the pending attributes.

- [ ] **Step 1: Write the failing tests**

Model the fixtures on `tests/test_mute_on_user_turn_creation.py` (`_Compute`, `_Watcher`).

```python
# tests/test_timeline_session_created.py
import pytest
from django.utils import timezone

from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Project, Session, SessionType, TimelineEvent
from twicc.providers.helpers import AgentSettings
from twicc.pending_session_attributes import pop_pending_session_attributes, set_pending_session_attributes
from twicc.providers.db_writer import CreateSessionPayload, _apply_create_session_payload
from twicc.providers.sessions_watcher import BaseSessionsWatcher, ParsedSessionFile
from tests.timeline_helpers import kinds

pytestmark = pytest.mark.django_db(transaction=True)


class _Compute:
    provider = Provider.CODEX
    compute_version = 1


class _Watcher(BaseSessionsWatcher):
    def get_compute(self):
        return _Compute()


@pytest.fixture
def project(tmp_path):
    return Project.objects.create(id="-tl-created", directory=str(tmp_path))


def parsed(project, sid, type_=SessionType.SESSION):
    return ParsedSessionFile(project.id, sid, type_, f"{sid}.jsonl")


def test_a_discovered_session_is_external_and_records_no_settings(project):
    _Watcher().create_session_sync(
        parsed(project, "ext"), project, agent_settings=AgentSettings(selected_model="opus"),
    )
    row = TimelineEvent.objects.get(session_id="ext")
    assert row.kind == "session.created" and row.data["origin"] == "external"
    assert row.data["settings"] is None and row.data["spawned_by"] is None


def test_a_session_with_a_process_run_is_twicc_and_records_its_settings(project):
    now = timezone.now()
    ProcessRun.objects.create(provider="codex", session_id="mine", started_at=now, last_state_change_at=now)
    _Watcher().create_session_sync(
        parsed(project, "mine"), project, agent_settings=AgentSettings(selected_model="opus", effort="high"),
    )
    data = TimelineEvent.objects.get(session_id="mine").data
    assert data["origin"] == "twicc"
    assert data["settings"] == {"selected_model": "opus", "effort": "high"}


def test_the_spawning_session_is_recorded_from_the_row(project):
    Session.objects.create(id="boss", file_path="boss.jsonl", project=project, provider=Provider.CODEX)
    set_pending_session_attributes("kid", spawned_by_id="boss")
    try:
        _Watcher().create_session_sync(parsed(project, "kid"), project)
    finally:
        pop_pending_session_attributes("kid")
    assert TimelineEvent.objects.get(session_id="kid", kind="session.created").data["spawned_by"] == "boss"


def test_pending_hidden_and_muted_flags_become_initial_intervals(project):
    set_pending_session_attributes("flagged", hidden=True, mute_on_user_turn=True)
    try:
        _Watcher().create_session_sync(parsed(project, "flagged"), project)
    finally:
        pop_pending_session_attributes("flagged")
    assert kinds("flagged") == ["session.created", "hidden.started", "muted.started"]


def test_subagent_sessions_get_no_event(project):
    parent = _Watcher().create_session_sync(parsed(project, "parent"), project)
    _Watcher().create_session_sync(
        ParsedSessionFile(project.id, "child", SessionType.SUBAGENT, "child.jsonl", parent_session_id="parent"),
        project, parent_session=parent,
    )
    assert kinds("child") == []


def test_initial_sync_payload_records_origin_sync(project):
    session = Session(id="synced", project_id=project.id, provider=Provider.CODEX, file_path="synced.jsonl",
                      type=SessionType.SESSION)
    payload = CreateSessionPayload(
        provider=Provider.CODEX, project_id=project.id, new_project_directory=None, new_project_stale=False,
        session=session, items=[], last_offset=0, last_line=0, mtime=0.0,
    )
    _apply_create_session_payload(payload)
    data = TimelineEvent.objects.get(session_id="synced").data
    assert data["origin"] == "sync" and data["settings"] is None and data["spawned_by"] is None
```

Check `ParsedSessionFile.__init__`'s signature (`sed -n 166,185p src/twicc/providers/sessions_watcher.py`) and `CreateSessionPayload`'s fields (`sed -n 99,125p src/twicc/providers/db_writer.py`) and adjust the constructor calls to the real signatures.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_timeline_session_created.py -v`
Expected: FAIL (`TimelineEvent.DoesNotExist`).

- [ ] **Step 3: Implement the watcher hook**

In `create_session_sync`, in the non-subagent path, replace the last line `return Session.objects.create(**kwargs)` with:

```python
        session = Session.objects.create(**kwargs)
        from twicc.core.models import ProcessRun
        from twicc.core.services.session_timeline import record_session_created

        origin = "twicc" if ProcessRun.objects.filter(session_id=session.id).exists() else "external"
        record_session_created(session, origin=origin)
        return session
```

The method runs inside the watcher's write-lock critical section (see the comment at the call site in `start_watcher`), so no extra lock is needed.

- [ ] **Step 4: Implement the initial-sync hook**

In `_apply_create_session_payload`, inside the `with transaction.atomic():` block right after `payload.session.save()`:

```python
        if payload.session.type == SessionType.SESSION:
            record_session_created(payload.session, origin="sync")
```

with `from twicc.core.models import Session, SessionItem, SessionType` and `from twicc.core.services.session_timeline import record_session_created` imported in the function.

- [ ] **Step 5: Purge the timeline of deleted sessions (spec §9)**

`session_id` is not a FK, so deleting a `Session` does not cascade. The only session delete in the code base is `_apply_delete_sessions_payload` (`db_writer.py` ~line 2516), fed by the Codex initial sync for internal rollouts that must not exist in TwiCC. Inside its `transaction.atomic()`, right after `Session.objects.filter(id__in=ids).delete()`, add:

```python
        TimelineEvent.objects.filter(session_id__in=ids).delete()
```

with `TimelineEvent` added to the function's `from twicc.core.models import Session, SessionItem` import. Add to `tests/test_timeline_session_created.py`:

```python
def test_deleting_sessions_purges_their_timeline_rows(project):
    from twicc.providers.db_writer import DeleteSessionsPayload, _apply_delete_sessions_payload

    for sid in ("gone", "kept"):
        Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider=Provider.CODEX)
        record_session_created(Session.objects.get(id=sid), origin="sync")
    _apply_delete_sessions_payload(DeleteSessionsPayload(provider=Provider.CODEX, session_ids=["gone"]))
    assert kinds("gone") == [] and kinds("kept") == ["session.created"]
```

(import `record_session_created` from `twicc.core.services.session_timeline` at the top of the test file.)

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_timeline_session_created.py tests/test_mute_on_user_turn_creation.py tests/test_title_origin_writers.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/twicc/providers/sessions_watcher.py src/twicc/providers/db_writer.py tests/test_timeline_session_created.py
git commit   # subject: feat(timeline): record session creation and its initial flags
```

---

### Task 7: Process hooks in the agent manager

**Files:**
- Create: `src/twicc/agent/timeline_hooks.py`
- Modify: `src/twicc/agent/base_manager.py` (`_register_and_start` ~line 678, `_persist_process_run_transition` ~line 1111, `_persist_background_work` ~line 1060)
- Test: `tests/test_timeline_process_hooks.py`

**Interfaces:**
- Consumes: `process_transition_events`, `EventSpec` (Task 2); `record_event_sync`, `latest_event_id` (Task 3); `DELIBERATE_STOP_REASONS` (`twicc.agent.states`).
- Produces (sync, never raise, call from worker threads):
  - `ProcessSnapshot(state: str, awaiting: bool, work: dict | None)` (`NamedTuple`) and `snapshot_of(process_run) -> ProcessSnapshot`.
  - `record_process_started(agent, *, resume: bool) -> None`
  - `record_process_changes(agent, prev: ProcessSnapshot, *, new_state: str, new_awaiting: bool, new_work: dict | None) -> None` — transition events (`turn.*`, `awaiting_input.*`, the `turn.ended` re-emission of an idle agent), no `process.ended`.
  - `record_process_ended(agent, prev: ProcessSnapshot) -> None` — inner closes (`turn.ended` with `background_work` null, `awaiting_input.ended`), then `process.ended`.

Rules: skip everything for `agent.ephemeral`. `process.started.data.hybrid` = `bool(getattr(agent, "is_hybrid", False))`: `HybridClaudeAgent.is_hybrid = True` is the class attribute the manager and WS layer already branch on (`getattr(agent, "is_hybrid", False)`); no other agent class defines it. `awaiting_input.started` carries the `request_type` and `tool_name` of the first pending request: `BaseAgent.pending_requests` is sorted by `created_at`, so that is the oldest, the one that opened the interval. Requests added or removed while the interval is open write nothing (the diff follows `bool(pending_requests)`). `process.ended.reason` = `agent.kill_reason or "error"`. Actor `USER` when the reason is in `DELIBERATE_STOP_REASONS`, else `SYSTEM`. When the reason is `"archived"`, `caused_by` is the latest `archived.started` of the session. `data.error` = `str(agent.error)[:500]` when set; `provider`, `cause`, `twicc_pid` and `hybrid` of `process.ended` (and the `awaiting_input.ended` data) are inherited from the `started` rows by the recording service (Task 3), so the hooks pass none of them. The inner closes and `process.ended` share one `occurred_at`. Project id comes from `agent.project_id`. Background work changing between transitions goes through `_persist_background_work`, which calls `record_process_changes` with the previous state as the new state: the diff (Task 2) re-emits `turn.ended` only while the session is idle.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timeline_process_hooks.py
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.utils import timezone

from twicc.agent.base_manager import BaseAgentManager
from twicc.agent.states import AgentState
from twicc.core.enums import Provider
from twicc.core.models import ProcessRun, Project, TimelineEvent
from twicc.core.services.session_timeline import record_event_sync
from tests.timeline_helpers import kinds, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)

WORK = {"subagents": 1, "shells": 0, "monitors": 0, "scheduled_wakeup_at": None, "goal": False}


class _Manager(BaseAgentManager):
    provider = Provider.CODEX


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-proc", directory="/tmp/tl-proc")


def make_agent(project, **kw):
    now = timezone.now()
    run = ProcessRun.objects.create(provider="codex", session_id="s1", started_at=now, last_state_change_at=now)
    agent = SimpleNamespace(
        session_id="s1", project_id=project.id, provider=Provider.CODEX, ephemeral=False,
        process_run=run, pending_requests=(), kill_reason=None, error=None, _work=None, is_hybrid=False,
        get_pid=lambda: 123, background_work_snapshot=lambda: agent._work,
        start=AsyncMock(),
    )
    for key, value in kw.items():
        setattr(agent, key, value)
    return agent


def transition(agent, state):
    manager = _Manager()
    return run_with_writer(lambda: manager._persist_process_run_transition(agent, state))


def test_register_and_start_records_process_started(project, monkeypatch):
    manager = _Manager()
    agent = make_agent(project)
    agent.process_run.delete()
    agent.process_run = None
    agent._background_work_callback = None
    monkeypatch.setattr(manager, "_broadcast_session_updated", AsyncMock())
    monkeypatch.setattr(manager, "_on_state_change", AsyncMock())
    monkeypatch.setattr(manager, "_ensure_timeout_monitor_running", lambda: None)
    run_with_writer(lambda: manager._register_and_start(agent, "hello", resume=True))
    row = TimelineEvent.objects.get(session_id="s1", kind="process.started")
    assert row.data["provider"] == "codex" and row.data["cause"] == "resume" and row.data["hybrid"] is False
    assert row.project_id == project.id and row.actor == "user"


def test_a_hybrid_agent_records_hybrid_true(project, monkeypatch):
    manager = _Manager()
    agent = make_agent(project, is_hybrid=True)
    agent.process_run.delete()
    agent.process_run = None
    agent._background_work_callback = None
    monkeypatch.setattr(manager, "_broadcast_session_updated", AsyncMock())
    monkeypatch.setattr(manager, "_on_state_change", AsyncMock())
    monkeypatch.setattr(manager, "_ensure_timeout_monitor_running", lambda: None)
    run_with_writer(lambda: manager._register_and_start(agent, "hello", resume=False))
    row = TimelineEvent.objects.get(session_id="s1", kind="process.started")
    assert row.data["hybrid"] is True and row.data["cause"] == "new"


def test_a_turn_round_trip_records_started_then_ended(project):
    agent = make_agent(project)
    transition(agent, AgentState.ASSISTANT_TURN)
    transition(agent, AgentState.USER_TURN)
    assert kinds("s1") == ["turn.started", "turn.ended"]
    turn_ended = TimelineEvent.objects.get(session_id="s1", kind="turn.ended")
    assert turn_ended.data == {"background_work": None}


def test_waiting_for_the_user_records_the_request_and_the_tool(project):
    agent = make_agent(project)
    transition(agent, AgentState.ASSISTANT_TURN)
    agent.pending_requests = (SimpleNamespace(request_type="tool_approval", tool_name="Bash"),)
    transition(agent, AgentState.ASSISTANT_TURN)
    agent.pending_requests = ()
    transition(agent, AgentState.ASSISTANT_TURN)
    rows = list(TimelineEvent.objects.filter(session_id="s1", kind__startswith="awaiting_input"))
    assert [r.kind for r in rows] == ["awaiting_input.started", "awaiting_input.ended"]
    assert rows[0].data == {"request_type": "tool_approval", "tool_name": "Bash"}
    assert rows[1].data == rows[0].data  # inherited by the recording service


def test_turn_ended_carries_the_background_work_running_behind_the_agent(project):
    agent = make_agent(project)
    transition(agent, AgentState.ASSISTANT_TURN)
    agent._work = WORK
    transition(agent, AgentState.USER_TURN)
    assert TimelineEvent.objects.get(session_id="s1", kind="turn.ended").data == {"background_work": WORK}


def test_background_work_changes_while_idle_re_emit_turn_ended(project):
    agent = make_agent(project)
    manager = _Manager()
    transition(agent, AgentState.ASSISTANT_TURN)
    agent._work = WORK
    transition(agent, AgentState.USER_TURN)
    agent._work = None
    run_with_writer(lambda: manager._persist_background_work(agent, None))
    run_with_writer(lambda: manager._persist_background_work(agent, None))  # no change: no row
    rows = list(TimelineEvent.objects.filter(session_id="s1", kind="turn.ended").order_by("id"))
    assert [r.data for r in rows] == [{"background_work": WORK}, {"background_work": None}]


def test_background_work_changes_during_an_assistant_turn_record_nothing(project):
    agent = make_agent(project)
    manager = _Manager()
    transition(agent, AgentState.ASSISTANT_TURN)
    agent._work = WORK
    run_with_writer(lambda: manager._persist_background_work(agent, WORK))
    agent._work = None
    run_with_writer(lambda: manager._persist_background_work(agent, None))
    assert kinds("s1") == ["turn.started"]


def test_death_closes_open_intervals_then_ends_the_process_with_one_timestamp(project):
    agent = make_agent(project)
    agent._work = WORK
    agent.pending_requests = (SimpleNamespace(request_type="question", tool_name="AskUserQuestion"),)
    transition(agent, AgentState.ASSISTANT_TURN)
    agent.kill_reason = "manual"
    transition(agent, AgentState.DEAD)
    tail = list(TimelineEvent.objects.filter(session_id="s1").order_by("id"))[-3:]
    assert [r.kind for r in tail] == ["turn.ended", "awaiting_input.ended", "process.ended"]
    assert tail[0].data == {"background_work": None}  # a dead process runs nothing
    assert len({r.occurred_at for r in tail}) == 1
    assert tail[-1].reason == "manual" and tail[-1].actor == "user"


def test_death_from_user_turn_with_background_work_writes_only_process_ended(project):
    agent = make_agent(project)
    transition(agent, AgentState.ASSISTANT_TURN)
    agent._work = WORK
    transition(agent, AgentState.USER_TURN)
    agent.kill_reason = "shutdown"
    transition(agent, AgentState.DEAD)
    assert kinds("s1") == ["turn.started", "turn.ended", "process.ended"]


def test_process_ended_inherits_the_data_of_process_started(project):
    from twicc.agent.timeline_hooks import record_process_started

    agent = make_agent(project, is_hybrid=True)
    record_process_started(agent, resume=True)
    agent.kill_reason = "manual"
    transition(agent, AgentState.DEAD)
    started = TimelineEvent.objects.get(session_id="s1", kind="process.started")
    ended = TimelineEvent.objects.get(session_id="s1", kind="process.ended")
    for field in ("provider", "cause", "twicc_pid", "hybrid"):
        assert ended.data[field] == started.data[field]
    assert ended.data["provider"] == "codex" and ended.data["cause"] == "resume" and ended.data["hybrid"] is True


def test_unsolicited_death_is_an_error_by_the_system(project):
    agent = make_agent(project, error="boom")
    transition(agent, AgentState.DEAD)
    row = TimelineEvent.objects.get(session_id="s1", kind="process.ended")
    assert row.reason == "error" and row.actor == "system" and row.data["error"] == "boom"


def test_archive_death_points_at_the_archive_event(project):
    cause = record_event_sync("archived.started", session_id="s1", actor="user")
    agent = make_agent(project, kill_reason="archived")
    transition(agent, AgentState.DEAD)
    assert TimelineEvent.objects.get(session_id="s1", kind="process.ended").caused_by_id == cause.id


def test_ephemeral_agents_record_nothing(project):
    agent = make_agent(project, ephemeral=True)
    transition(agent, AgentState.ASSISTANT_TURN)
    assert kinds("s1") == []


def test_a_failing_hook_never_blocks_the_process_run_update(project, monkeypatch):
    agent = make_agent(project)
    monkeypatch.setattr("twicc.agent.timeline_hooks.record_event_sync",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    agent.kill_reason = "manual"
    transition(agent, AgentState.DEAD)
    assert not ProcessRun.objects.filter(session_id="s1").exists()


def test_an_agent_without_project_id_still_records(project):
    agent = make_agent(project)
    del agent.project_id
    transition(agent, AgentState.ASSISTANT_TURN)
    assert TimelineEvent.objects.get(session_id="s1").project_id is None
```

If the fake agent lacks an attribute the real methods read (the first run will show it), add that attribute to `make_agent`; keep the assertions.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_timeline_process_hooks.py -v`
Expected: FAIL (no timeline rows).

- [ ] **Step 3: Implement the hooks module**

```python
# src/twicc/agent/timeline_hooks.py
"""Timeline recording for the agent manager (design §6, §7).

Sync helpers, called from worker threads inside the manager's
``run_under_db_write_lock`` callbacks. They never raise.
"""

from __future__ import annotations

import functools
import logging
from typing import NamedTuple

from django.utils import timezone

from twicc.agent.states import DELIBERATE_STOP_REASONS, AgentState
from twicc.core.models import TimelineActor, TimelineKind
from twicc.core.services.session_timeline import latest_event_id, record_event_sync
from twicc.core.timeline import process_transition_events

logger = logging.getLogger(__name__)

_ERROR_TEXT_LIMIT = 500


def _never_raises(function):
    """Make a hook harmless: a failure is logged and never reaches the manager.

    ``record_event_sync`` already swallows write errors, but the hooks also read
    the agent (``project_id``, ``pending_requests``, ``process_run``). An error
    there must not skip the ``ProcessRun`` update or delete that follows.
    """

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            logger.exception("Timeline hook %s failed", function.__name__)

    return wrapper


class ProcessSnapshot(NamedTuple):
    """The three ProcessRun values the timeline diffs between transitions."""

    state: str
    awaiting: bool
    work: dict | None


def snapshot_of(process_run) -> ProcessSnapshot | None:
    """The diffed values of a ``ProcessRun`` row, or ``None`` when the agent has no row."""
    if process_run is None:
        return None
    return ProcessSnapshot(
        process_run.state, bool(process_run.awaiting_user_input), process_run.background_work_in_progress,
    )


def _enabled(agent) -> bool:
    return not getattr(agent, "ephemeral", False)


def _first_pending(agent):
    pending = tuple(getattr(agent, "pending_requests", None) or ())
    return pending[0] if pending else None


def _write(agent, specs, *, occurred_at, actor=TimelineActor.AGENT) -> None:
    for spec in specs:
        record_event_sync(
            spec.kind, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
            occurred_at=occurred_at, actor=actor, data=spec.data,
        )


@_never_raises
def record_process_started(agent, *, resume: bool) -> None:
    if not _enabled(agent):
        return
    run = agent.process_run
    record_event_sync(
        TimelineKind.PROCESS_STARTED, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
        actor=TimelineActor.USER,
        data={"provider": agent.provider.value, "cause": "resume" if resume else "new",
              "twicc_pid": run.twicc_pid if run is not None else None,
              # ``HybridClaudeAgent.is_hybrid`` is a class attribute; other agents lack it.
              "hybrid": bool(getattr(agent, "is_hybrid", False))},
    )


@_never_raises
def record_process_changes(agent, prev: ProcessSnapshot | None, *, new_state: str, new_awaiting: bool,
                           new_work: dict | None) -> None:
    """Events of a non-fatal transition (also used for background-work-only changes)."""
    if prev is None or not _enabled(agent):
        return
    pending = _first_pending(agent)
    specs = process_transition_events(
        prev_state=prev.state, prev_awaiting=prev.awaiting, prev_work=prev.work,
        new_state=new_state, new_awaiting=new_awaiting, new_work=new_work,
        request_type=getattr(pending, "request_type", None), tool_name=getattr(pending, "tool_name", None),
    )
    _write(agent, specs, occurred_at=timezone.now())


@_never_raises
def record_process_ended(agent, prev: ProcessSnapshot | None) -> None:
    """Close the open turn and wait, then write ``process.ended`` (one ``occurred_at``).

    ``provider``, ``cause``, ``twicc_pid`` and ``hybrid`` of the row are inherited
    from ``process.started`` by the recording service.
    """
    if prev is None or not _enabled(agent):
        return
    now = timezone.now()
    specs = process_transition_events(
        prev_state=prev.state, prev_awaiting=prev.awaiting, prev_work=prev.work,
        new_state=AgentState.DEAD.value, new_awaiting=False, new_work=None,
    )
    _write(agent, specs, occurred_at=now)
    reason = getattr(agent, "kill_reason", None) or "error"
    error = getattr(agent, "error", None)
    caused_by = latest_event_id(agent.session_id, TimelineKind.ARCHIVED_STARTED) if reason == "archived" else None
    record_event_sync(
        TimelineKind.PROCESS_ENDED, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
        occurred_at=now, reason=reason, caused_by=caused_by,
        actor=TimelineActor.USER if reason in DELIBERATE_STOP_REASONS else TimelineActor.SYSTEM,
        data={"error": str(error)[:_ERROR_TEXT_LIMIT]} if error else None,
    )
```

- [ ] **Step 4: Wire `_register_and_start`**

Inside `_persist_run_and_start_timestamps`, after the `Session ... .update(...)` call:

```python
            await asyncio.to_thread(record_process_started, agent, resume=resume)
```

with `from twicc.agent.timeline_hooks import record_process_started` imported at the top of the method body (lazy, like the other imports there). Steps 5 and 6 import `record_process_changes, record_process_ended, snapshot_of` the same way, inside `_persist_process_run_transition` and `_persist_background_work`.

- [ ] **Step 5: Wire `_persist_process_run_transition`**

In `_persist_update` (non-DEAD branch), capture the previous snapshot before the DB update and record after it:

```python
                async def _persist_update() -> None:
                    background_work = agent.background_work_snapshot()
                    prev = snapshot_of(agent.process_run) if agent.process_run is not None else None
                    await asyncio.to_thread(lambda: ProcessRun.objects.filter(pk=pr_pk).update(...))  # unchanged
                    if prev is not None:
                        await asyncio.to_thread(
                            lambda: record_process_changes(
                                agent, prev, new_state=state_value, new_awaiting=awaiting, new_work=background_work,
                            )
                        )
                    if agent.process_run is not None:
                        ...  # unchanged in-memory mirror
```

In `_settle_dead`, make the first statement `prev = snapshot_of(agent.process_run)` (it is `None` when the agent has no row; the hook then does nothing) and, before the keep/delete decision, call `await asyncio.to_thread(lambda: record_process_ended(agent, prev))`.

- [ ] **Step 6: Wire `_persist_background_work`**

In `_persist`, capture `prev = snapshot_of(agent.process_run)` before the `update`, and after it:

```python
            if agent.process_run is not None:
                await asyncio.to_thread(
                    lambda: record_process_changes(
                        agent, prev, new_state=prev.state, new_awaiting=prev.awaiting, new_work=current,
                    )
                )
```

placed before the in-memory mirror line `agent.process_run.background_work_in_progress = current`, so `prev` still holds the old value. The diff (Task 2) turns the change into a `turn.ended` re-emission only when `prev.state` is `user_turn`; in any other state it writes nothing.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_timeline_process_hooks.py tests/test_background_work.py tests/test_agent_run_plumbing.py -v`
Expected: all PASS. Then the wider agent tests: `uv run pytest tests -k "manager or agent_state or process_run" -v`.

- [ ] **Step 8: Commit**

```bash
git add src/twicc/agent/timeline_hooks.py src/twicc/agent/base_manager.py tests/test_timeline_process_hooks.py
git commit   # subject: feat(timeline): record process, turn and waiting events
```

---

### Task 8: Title and settings hooks

**Files:**
- Modify: `src/twicc/core/services/session_update.py` (`update_session_title_from_payload` ~line 311; `persist_session_settings` ~line 153)
- Modify: `src/twicc/views.py` (PATCH title, ~line 1184)
- Modify: `src/twicc/core/services/title_automation.py` (`_apply_check`, ~line 90)
- Modify: `src/twicc/providers/compute_base.py` (`apply_placeholder_title` ~line 1969, `_write_provider_title` ~line 1983)
- Modify: `src/twicc/agent/base_manager.py` (`_try_flush_pending_title`, the pending-title flush ~line 852)
- Modify: `src/twicc/providers/codex/titles.py` (`_apply_sync_session_titles_job` ~line 56)
- Modify: `src/twicc/providers/claude_code/ws.py` (`update_session_permission_mode`, ~line 84: writes `permission_mode` on an approved `setMode` suggestion)
- Modify: `src/twicc/providers/db_writer.py` (`_apply_retire_sessions_job` ~line 2703)
- Modify: `src/twicc/providers/claude_code/agent/hybrid/agent.py` (~line 366)
- Test: `tests/test_timeline_title_settings.py`

**Interfaces:**
- Consumes: `record_event_sync`, `record_title_change`, `project_id_of` (Task 3); `AgentSettings` (`twicc.providers.helpers`).
- Produces: `persist_session_settings(session_id, updates, *, actor=TimelineActor.USER, reason="")` (new keyword-only parameters, backward compatible); `record_settings_change(session_id, before, updates, *, actor, reason="")` in `session_update.py`.

Rules: `title.changed` only when the title text differs. `data.old` is the previous title, filled at every site where it is known or cheap to read (user service and PATCH: `old_title`; automatic check: `snapshot.title`; placeholder: `None` by construction; provider-title overwrite: read before the update; Codex import: the loop's `session.title`; pending flush: the `old_title` it already reads). `source` is `user` (both user paths), `auto` (automatic check, placeholder), `agent` (provider title, second branch of `_write_provider_title`). The title set when the row is created is part of `session.created` and writes no `title.changed`. `settings.changed` carries `{field: [old, new]}` for the changed fields of the `AgentSettings` bundle only, and is skipped when none changed. The retire job uses `actor=SYSTEM`, `reason="model_retired"`; the hybrid switch uses `actor=SYSTEM`, `reason="hybrid_switch"`.

- [ ] **Step 1: Step zero — enumerate every writer (spec §6 requirement)**

Run:
```bash
cd /home/twidi/dev/twicc-poc
grep -rn "title_origin\|update(title\|\.update(.*title" src/twicc --include=*.py | grep -v "migrations\|tests"
grep -rn "persist_session_settings" src/twicc --include=*.py | grep -v migrations
grep -rnE "a?update\(" src/twicc --include=*.py -B3 | grep -E "(selected_model|effort|thinking_enabled|permission_mode|context_max|claude_in_chrome|fast_mode|question_widget)" | grep -v migrations
sed -n 1,60p tests/test_title_origin_writers.py
```
Expected: the sites listed under **Files** (all of them are planned below). If the grep finds a title or settings writer that is not listed, add a hook following the same pattern and one test in this task's test file. The creation-time title written by `create_session_sync` is part of `session.created` and gets no hook. `context_max` written by the compute from the JSONL window is an observed value, not a setting change: it is excluded on purpose. `AgentSettings` has eight fields (it includes `question_widget`, which CLAUDE.md's list of seven omits); `settings.changed` records all eight. The second grep deliberately matches `aupdate(` and looks three lines back, because a plain `.update(` grep misses multi-line async updates. `tests/test_title_origin_writers.py` already enumerates title writers by origin: use it as the checklist. List the hooked sites in the commit body.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_timeline_title_settings.py
from unittest.mock import patch

import pytest

from twicc.core.enums import Provider
from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services.session_update import persist_session_settings, update_session_title_from_payload
from tests.test_title_automation import env, session  # noqa: F401  (fixtures reused below)
from tests.timeline_helpers import kinds, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-title", directory="/tmp/tl-title")


def make(project, **kw):
    return Session.objects.create(id="s1", file_path="s1.jsonl", project=project, provider="codex", **kw)


def test_user_title_update_records_source_user(project):
    make(project, title="Old")
    with patch("twicc.core.services.session_update.ensure_provider_running"):
        run_with_writer(lambda: update_session_title_from_payload({"session_id": "s1", "title": "New"}))
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "title.changed" and row.actor == "user"
    assert row.data == {"source": "user", "new": "New", "old": "Old"}


def test_user_title_update_with_the_same_text_records_nothing(project):
    make(project, title="Same")
    with patch("twicc.core.services.session_update.ensure_provider_running"):
        run_with_writer(lambda: update_session_title_from_payload({"session_id": "s1", "title": "Same"}))
    assert kinds("s1") == []


def test_placeholder_title_records_source_auto(project):
    from twicc.providers.compute_base import BaseSessionCompute

    make(project)
    assert BaseSessionCompute.apply_placeholder_title("s1", "First words")
    assert TimelineEvent.objects.get(session_id="s1").data == {"source": "auto", "new": "First words", "old": None}


def test_provider_title_overwrite_records_source_agent(project):
    from twicc.providers.compute_base import BaseSessionCompute

    make(project, title="Before", title_origin="user")
    assert BaseSessionCompute._write_provider_title("s1", "From provider")
    assert TimelineEvent.objects.get(session_id="s1").data == {"source": "agent", "new": "From provider", "old": "Before"}


def test_settings_change_records_only_changedbundle_fields(project):
    make(project, selected_model="sonnet", effort="medium")
    persist_session_settings("s1", {"selected_model": "opus", "effort": "medium"})
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "settings.changed" and row.data == {"changes": {"selected_model": ["sonnet", "opus"]}}


def test_non_bundle_columns_are_not_recorded(project):
    make(project, selected_model="sonnet")
    persist_session_settings("s1", {"title_checked_at": None})
    assert kinds("s1") == []


def test_settings_update_without_a_bundle_change_records_nothing(project):
    make(project, selected_model="sonnet")
    persist_session_settings("s1", {"selected_model": "sonnet"})
    assert kinds("s1") == []


def test_settings_actor_and_reason_are_passed_through(project):
    make(project, selected_model="sonnet")
    persist_session_settings("s1", {"selected_model": "opus"}, actor="system", reason="model_retired")
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.actor == "system" and row.reason == "model_retired"


def test_retire_job_records_a_system_settings_change(project):
    from twicc.providers import db_writer

    make(project, selected_model="old-model")
    job = db_writer._RetireSessionsJob(provider=Provider.CODEX, updates={"s1": {"selected_model": "new-model"}}, future=None)
    db_writer._apply_retire_sessions_job(job)
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.actor == "system" and row.reason == "model_retired"
    assert row.data == {"changes": {"selected_model": ["old-model", "new-model"]}}
```


- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_timeline_title_settings.py -v`
Expected: FAIL.

- [ ] **Step 4: Implement the settings hooks**

In `session_update.py` (add at the top of the module, next to the existing `run_under_db_write_lock` import, `from twicc.core.models import TimelineActor, TimelineKind` and `from twicc.core.services.session_timeline import project_id_of, record_event_sync, record_title_change`, completing the imports Task 4 added; `session_update` already imports `twicc.providers.db_writer` at module level and `session_timeline` imports it too, but nothing in `session_timeline`, `core/timeline.py` or `core/models.py` imports `session_update`, so no cycle appears; `db_writer` does not import `session_update` at module level, and the Task 8 hooks reach it through function-level imports. `session_update` imports `twicc.core.models` lazily inside its functions today: if a module-level models import fails at app loading, import `TimelineActor`/`TimelineKind` inside the functions that use them instead):

```python
def bundle_fields(updates: dict) -> list[str]:
    from twicc.providers.helpers import AgentSettings

    return [name for name in updates if name in AgentSettings._fields]


def record_settings_change(session_id: str, before: dict | None, updates: dict, *,
                           actor: str, reason: str = "") -> None:
    """Write ``settings.changed`` for the bundle fields of ``updates`` whose value changed.

    ``before`` is the row's values for ``bundle_fields(updates)`` read before the update.
    """
    if not before:
        return
    changes = {name: [before[name], updates[name]] for name in before if before[name] != updates[name]}
    if not changes:
        return
    record_event_sync(
        TimelineKind.SETTINGS_CHANGED, session_id=session_id, project_id=project_id_of(session_id),
        actor=actor, reason=reason, data={"changes": changes},
    )


@transaction.atomic
def persist_session_settings(
    session_id: str, updates: dict, *, actor: str = TimelineActor.USER, reason: str = "",
) -> None:
    """Write settings and order widget snapshots under the DB writer lease."""
    from twicc.core.models import Session

    previous = None
    if "question_widget" in updates:
        previous = Session.objects.filter(id=session_id).values("provider", "question_widget").first()
    bundle = bundle_fields(updates)
    before = Session.objects.filter(id=session_id).values(*bundle).first() if bundle else None
    Session.objects.filter(id=session_id).update(**updates)
    record_settings_change(session_id, before, updates, actor=actor, reason=reason)
    ...  # existing question_widget block unchanged
```

`_apply_retire_sessions_job` (`db_writer.py`): import lazily inside the function (the module must not import `session_update` at load time) and record inside the existing `transaction.atomic()` loop:

```python
    from twicc.core.models import Session, TimelineActor
    from twicc.core.services.session_update import bundle_fields, record_settings_change

    if not job.updates:
        return 0
    count = 0
    with transaction.atomic():
        for session_id, fields in job.updates.items():
            bundle = bundle_fields(fields)
            before = Session.objects.filter(id=session_id).values(*bundle).first() if bundle else None
            count += Session.objects.filter(id=session_id).update(**fields)
            record_settings_change(
                session_id, before, fields, actor=TimelineActor.SYSTEM, reason="model_retired",
            )
    return count
```

`src/twicc/providers/claude_code/ws.py::update_session_permission_mode` (an approved `setMode` suggestion changes `permission_mode`, a bundle field): read the previous value and write the event in the same worker-thread transaction, under the same lock acquire as the update:

```python
    from django.db import transaction

    from twicc.core.models import Session, TimelineActor
    from twicc.core.serializers import serialize_session
    from twicc.core.services.session_update import record_settings_change

    def _update() -> int:
        with transaction.atomic():
            before = Session.objects.filter(id=session_id).values("permission_mode").first()
            rows = (
                Session.objects.filter(id=session_id)
                .exclude(permission_mode=permission_mode)
                .update(permission_mode=permission_mode)
            )
            if rows:
                record_settings_change(
                    session_id, before, {"permission_mode": permission_mode},
                    actor=TimelineActor.USER, reason="set_mode",
                )
            return rows

    rows = await run_under_db_write_lock(lambda: asyncio.to_thread(_update))
```

(replacing the existing `rows = await run_under_db_write_lock(lambda: Session.objects...aupdate(...))`; add `import asyncio` to the module if it is not imported). Add to `tests/test_timeline_title_settings.py`:

```python
def test_set_mode_suggestion_records_a_user_settings_change_once(project):
    from twicc.providers.claude_code.ws import update_session_permission_mode

    make(project, permission_mode="default", hidden=True)  # hidden: no broadcast needed
    run_with_writer(lambda: update_session_permission_mode("s1", "acceptEdits"))
    run_with_writer(lambda: update_session_permission_mode("s1", "acceptEdits"))
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.data == {"changes": {"permission_mode": ["default", "acceptEdits"]}}
    assert row.actor == "user" and row.reason == "set_mode"
```

Hybrid agent (`hybrid/agent.py:366`): the real call is wrapped, keep the wrapper and only add the two keywords:

```python
        await run_under_db_write_lock(
            lambda: sync_to_async(persist_session_settings)(
                self.session_id, {"claude_in_chrome": False},
                actor=TimelineActor.SYSTEM, reason="hybrid_switch",
            )
        )
```

(`from twicc.core.models import TimelineActor` next to the other local imports of that method.) The WS call in `asgi.py:1305` keeps the default (`user`).

- [ ] **Step 5: Implement the title hooks**

User service (`update_session_title_from_payload`): capture `old_title = session.title` before assigning, then:

```python
    async def _save_title() -> None:
        await session.asave(update_fields=["title", "title_origin"])
        if title != old_title:
            await asyncio.to_thread(record_title_change, session.id, title, source="user", old=old_title)

    await run_under_db_write_lock(_save_title)
```

Apply the same change at the PATCH site in `views.py` (~line 1184); add `from twicc.core.services.session_timeline import record_title_change` to that function's imports.

`title_automation._apply_check` (add `import asyncio` and `from twicc.core.services.session_timeline import record_title_change` at the top of the module), inside `apply()` after the successful `aupdate`:

```python
        if result == "changed":
            await asyncio.to_thread(record_title_change, snapshot.id, title, source="auto", old=snapshot.title)
        return result
```

`compute_base.py` (import `record_title_change` lazily inside each method: `compute_base` is imported very early and `session_timeline` imports the DB writer):

```python
    @staticmethod
    def apply_placeholder_title(target_session_id: str, title: str) -> bool:
        """Stamp an initial title only if the database title is still NULL."""
        from twicc.core.services.session_timeline import record_title_change

        changed = bool(Session.objects.filter(id=target_session_id, title__isnull=True).update(
            title=title, title_origin='auto',
        ))
        if changed:
            record_title_change(target_session_id, title, source="auto", old=None)
        return changed

    @staticmethod
    def _write_provider_title(target_session_id: str, title: str) -> bool:
        """Stamp NULL transitions; leave the origin of existing titles intact."""
        from twicc.core.services.session_timeline import record_title_change

        if BaseSessionCompute.apply_placeholder_title(target_session_id, title):
            return True
        old_title = Session.objects.filter(id=target_session_id).values_list("title", flat=True).first()
        changed = bool(Session.objects.filter(id=target_session_id).exclude(title=title).update(title=title))
        if changed:
            record_title_change(target_session_id, title, source="agent", old=old_title)
        return changed
```

- [ ] **Step 6: Hook the pending-title flush and the Codex title import**

Both writers are covered by `tests/test_title_origin_writers.py`; model the new tests on `test_pending_flush_validates_title` and the `codex_import` branch of `test_provider_writes_preserve_existing_origin` there.

`BaseAgentManager._try_flush_pending_title` writes the title a user chose for a draft session (`title_origin="user"`). Inside its `_persist_session_title` closure, record the change when a row was updated:

```python
            async def _persist_session_title() -> int:
                old_title = await sync_to_async(
                    lambda: Session.objects.filter(id=session_id).values_list("title", flat=True).first()
                )()
                updated = await sync_to_async(
                    Session.objects.filter(id=session_id).update
                )(title=pending, title_origin="user")
                # Retries of a failed provider rename re-run this update: only a real change is an event.
                if updated and old_title != pending:
                    await asyncio.to_thread(record_title_change, session_id, pending, source="user", old=old_title)
                return updated
```

(`from twicc.core.services.session_timeline import record_title_change` with the other imports of the method.) The previous title is read first so that the retries of a failed provider rename (`_retry_flush_pending_title`) never write a second identical row.

`_apply_sync_session_titles_job` (Codex; add `from twicc.core.services.session_timeline import record_title_change` to the function's existing local imports): in the loop, mirror the compute rule: record `source="auto"` when the NULL-title branch stamped the row, `source="agent"` otherwise. `session.title` is still the previous title there (the object was loaded before the updates and is refreshed only after):

```python
                stamped = Session.objects.filter(id=session.id, title__isnull=True).update(
                    title=new_title, title_origin="auto",
                )
                if not stamped:
                    Session.objects.filter(id=session.id).update(title=new_title)
                record_title_change(
                    session.id, new_title, source="auto" if stamped else "agent", old=session.title,
                )
```

Add to `tests/test_timeline_title_settings.py`:

```python
def test_automatic_title_check_records_source_auto(env, session):
    """Uses the ``env`` / ``session`` fixtures of ``tests/test_title_automation.py``."""
    from asgiref.sync import async_to_sync

    from tests.test_title_automation import NOW
    from twicc.title_cadence import TitleCheckState

    async_to_sync(env.module._apply_check)(
        session, TitleCheckState(7, NOW), title="New title", now=NOW, closing=False, succeeded=True)
    row = TimelineEvent.objects.get(session_id=session.id, kind="title.changed")
    assert row.data == {"source": "auto", "new": "New title", "old": session.title} and row.actor == "agent"


def test_codex_title_import_records_auto_for_a_null_title_and_agent_otherwise(project):
    from twicc.providers.codex.titles import SyncSessionTitlesJob, _apply_sync_session_titles_job

    make(project, title=None)
    Session.objects.create(id="s2", file_path="s2.jsonl", project=project, provider="codex", title="Old")
    _apply_sync_session_titles_job(SyncSessionTitlesJob({"s1": "Imported", "s2": "Renamed"}, None))
    assert TimelineEvent.objects.get(session_id="s1").data == {"source": "auto", "new": "Imported", "old": None}
    assert TimelineEvent.objects.get(session_id="s2").data == {"source": "agent", "new": "Renamed", "old": "Old"}


def test_pending_title_flush_records_source_user(project):
    from types import SimpleNamespace

    from twicc.agent.base_manager import BaseAgentManager

    make(project, title=None)
    manager = SimpleNamespace(
        _broadcast_session_updated=lambda sid: BaseAgentManager._broadcast_session_updated(None, sid),
    )
    run_with_writer(lambda: BaseAgentManager._try_flush_pending_title(
        manager, SimpleNamespace(session_id="s1", provider="codex"), "Chosen"))
    assert TimelineEvent.objects.get(session_id="s1").data == {"source": "user", "new": "Chosen", "old": None}
```

If the Codex job's constructor or the flush's collaborators differ from `tests/test_title_origin_writers.py` (read it), copy that file's exact setup, not these sketches.

- [ ] **Step 7: Run the tests**

Run: `uv run pytest tests/test_timeline_title_settings.py tests/test_title_origin_writers.py tests/test_title_check_triggers.py tests/test_title_auto_task.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/twicc/core/services/session_update.py src/twicc/core/services/title_automation.py src/twicc/providers/compute_base.py src/twicc/providers/codex/titles.py src/twicc/agent/base_manager.py src/twicc/providers/db_writer.py src/twicc/providers/claude_code/agent/hybrid/agent.py src/twicc/providers/claude_code/ws.py src/twicc/views.py tests/test_timeline_title_settings.py
git commit   # subject: feat(timeline): record title and settings changes
```

---

### Task 9: Boot reconciliation and backend events

**Files:**
- Create: `src/twicc/core/services/timeline_boot.py`
- Modify: `src/twicc/agent/process_run_cleanup.py` (`_apply_cleanup_stale_process_runs_job`)
- Modify: `src/twicc/cli/run.py` (shutdown sequence, ~line 505)
- Test: `tests/test_timeline_boot.py`

**Interfaces:**
- Consumes: `record_event_sync`, `record_event` (Task 3; the inheritance of `process.ended` and `backend.ended` data happens there), `process_transition_events` (Task 2).
- Produces:
  - `reconcile_at_boot(*, now=None, boot_id=_UNSET) -> None` — sync; called by the cleanup job in its own transaction, before the bulk DEAD update.
  - `read_boot_id() -> str | None`.
  - `async record_backend_ended_clean() -> None`.
  - `BACKFILL_PREFIX = "backfill:"`.

Spec §7, in order (the spec numbers them 1, 3 and 4 because step 2 is the evidence rule inside step 1): (1) for each non-DEAD `ProcessRun`: the inner ends (`turn.ended` with `background_work` null for a row in `assistant_turn`, `awaiting_input.ended` for a row awaiting input; nothing for a row idle in `user_turn`, whatever its background work) then `process.ended(reason="lost", precision="estimated")` dated at the last evidence of life (`max(last_state_change_at, Session.last_updated_at, max SessionItem.timestamp of the session)`, capped at boot time), `data.upper_bound` = boot time. The inner ends and `process.ended` all have `precision="estimated"`. `process.ended` inherits from the open `process.started`: when several non-DEAD rows share one session (the cleanup dedups them only after the reconciliation), only the first one processed inherits, the others keep null fields; (2), after step 1, if the latest `backend.started` has no later (higher id) `backend.ended`, write `backend.ended(clean=false, precision="estimated")` (the rest of its data inherited from that open `backend.started`) at the greatest `occurred_at` among all rows with a higher id except backfilled ones (`dedupe_key` starting with `backfill:`), this boot's step-1 rows included, else at that `backend.started`'s `occurred_at`, then clamped to [that `backend.started`'s `occurred_at`, this boot's `backend.started` `occurred_at`] (`now`); (3) write `backend.started` with `pid`, `version`, `boot_id`, `machine_restarted` (`None` when either side is missing).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timeline_boot.py
from datetime import timedelta

import pytest
from django.utils import timezone

from twicc.agent.process_run_cleanup import CleanupStaleProcessRunsJob, _apply_cleanup_stale_process_runs_job
from twicc.core.models import ProcessRun, Project, Session, SessionItem, TimelineEvent
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_boot import reconcile_at_boot, record_backend_ended_clean
from tests.timeline_helpers import run_with_writer

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
    run_with_writer(record_backend_ended_clean)
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_timeline_boot.py -v`
Expected: FAIL with `ModuleNotFoundError: twicc.core.services.timeline_boot`.

- [ ] **Step 3: Implement**

```python
# src/twicc/core/services/timeline_boot.py
"""Boot-time timeline reconciliation and backend instance events (design §7)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from django.db.models import Max
from django.utils import timezone

from twicc.agent.states import AgentState
from twicc.core.models import (
    ProcessRun, Session, SessionItem, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
)
from twicc.core.services.session_timeline import record_event, record_event_sync
from twicc.core.timeline import process_transition_events
from twicc.version import get_version

_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_UNSET = object()
BACKFILL_PREFIX = "backfill:"


def read_boot_id() -> str | None:
    """The kernel boot id (Linux), or ``None`` where the platform has none."""
    try:
        return _BOOT_ID_PATH.read_text().strip() or None
    except OSError:
        return None


def _last_evidence(run: ProcessRun, session: Session | None, boot: datetime) -> datetime:
    """Latest sign of life of a lost process, never later than the boot that found it."""
    candidates = [run.last_state_change_at]
    if session is not None and session.last_updated_at:
        candidates.append(session.last_updated_at)
    last_item = SessionItem.objects.filter(session_id=run.session_id).aggregate(last=Max("timestamp"))["last"]
    if last_item is not None:
        candidates.append(last_item)
    return min(max(candidates), boot)


def _reconcile_lost_processes(now: datetime) -> None:
    """Spec §7 step 1. Must run before the bulk DEAD update."""
    for run in ProcessRun.objects.exclude(state=AgentState.DEAD.value):
        session = Session.objects.filter(id=run.session_id).first()
        common = dict(
            session_id=run.session_id, project_id=session.project_id if session else None,
            occurred_at=_last_evidence(run, session, now),
            precision=TimelinePrecision.ESTIMATED, actor=TimelineActor.SYSTEM,
        )
        for spec in process_transition_events(
            prev_state=run.state, prev_awaiting=run.awaiting_user_input,
            prev_work=run.background_work_in_progress,
            new_state=AgentState.DEAD.value, new_awaiting=False, new_work=None,
        ):
            record_event_sync(spec.kind, data=spec.data, **common)
        record_event_sync(
            TimelineKind.PROCESS_ENDED, reason="lost", data={"upper_bound": now.isoformat()}, **common,
        )


def _close_previous_instance(now: datetime) -> TimelineEvent | None:
    """Spec §7 step 3. Returns the previous ``backend.started`` row, if any.

    Runs after :func:`_reconcile_lost_processes`. "Later" means a higher ``id``, never a
    later ``occurred_at`` (lost-end rows are back-dated). ``now`` is the ``occurred_at``
    that this boot's ``backend.started`` will get: the date of ``backend.ended`` is clamped
    to [previous ``backend.started``, ``now``] so it is never dated before its own start
    (a back-dated lost row written after it) nor after the next instance began (a wrong
    clock after a power loss). If the clock went backwards so that the previous start is
    dated after ``now``, ``now`` wins (a negative duration; the id order stays correct).
    The evidence rows keep their own dates; row order is by id.
    """
    started = TimelineEvent.objects.filter(kind=TimelineKind.BACKEND_STARTED).order_by("-id").first()
    if started is None:
        return None
    if TimelineEvent.objects.filter(kind=TimelineKind.BACKEND_ENDED, id__gt=started.id).exists():
        return started
    # Every row written after ``started`` (by id) counts; in practice those are this boot's
    # step-1 rows (estimated, last evidence of life of processes the previous instance ran,
    # so it was alive then) and live rows it wrote. Only backfilled rows are ignored.
    last_alive = (
        TimelineEvent.objects.filter(id__gt=started.id)
        .exclude(dedupe_key__startswith=BACKFILL_PREFIX)
        .order_by("-occurred_at")
        .first()
    )
    when = last_alive.occurred_at if last_alive else started.occurred_at
    when = min(max(when, started.occurred_at), now)  # lower bound first, then the upper one
    record_event_sync(
        TimelineKind.BACKEND_ENDED, occurred_at=when,
        precision=TimelinePrecision.ESTIMATED, actor=TimelineActor.SYSTEM, data={"clean": False},
    )
    return started


def reconcile_at_boot(*, now: datetime | None = None, boot_id=_UNSET) -> None:
    """Reconcile what the previous instance left behind, then record this boot.

    Sync. Called by the cleanup job in its own transaction, BEFORE the bulk
    update that forces stale ``ProcessRun`` rows to DEAD.
    """
    now = now or timezone.now()
    boot_id = read_boot_id() if boot_id is _UNSET else boot_id
    _reconcile_lost_processes(now)
    previous = _close_previous_instance(now)
    previous_boot_id = previous.data.get("boot_id") if previous else None
    machine_restarted = None if (boot_id is None or previous_boot_id is None) else boot_id != previous_boot_id
    record_event_sync(
        TimelineKind.BACKEND_STARTED, occurred_at=now, actor=TimelineActor.SYSTEM,
        data={"pid": os.getpid(), "version": get_version(), "boot_id": boot_id,
              "machine_restarted": machine_restarted},
    )


async def record_backend_ended_clean() -> None:
    """Record the clean end of this instance (call after every manager shut down)."""
    await record_event(TimelineKind.BACKEND_ENDED, actor=TimelineActor.SYSTEM, data={"clean": True})
```

Verify that `twicc.version.get_version()` returns a string under the test settings; if it can raise, wrap it with `try/except Exception: None`.

Wire the cleanup job in `process_run_cleanup._apply_cleanup_stale_process_runs_job` (the reconciliation runs in its own transaction, before the cleanup's, so the stale rows still carry their previous state):

```python
    from twicc.core.services.timeline_boot import reconcile_at_boot

    # The timeline must never block the cleanup: its own atomic block rolls back alone.
    try:
        with transaction.atomic():
            reconcile_at_boot()
    except Exception:
        logger.exception("Timeline boot reconciliation failed")
    with transaction.atomic():
        return _cleanup_stale_process_runs()
```

Wire the shutdown in `cli/run.py`, right after `await orchestrators.shutdown_all()` and before `await inline_exports.stop()`:

```python
            from twicc.core.services.timeline_boot import record_backend_ended_clean
            try:
                await record_backend_ended_clean()
            except Exception:
                logger.exception("Could not record the clean backend end")
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_timeline_boot.py -v`
Expected: all PASS. The two cleanup-job tests exercise the wiring; `tests/test_title_auto_task.py` patches `cleanup_stale_process_runs` and does not.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/core/services/timeline_boot.py src/twicc/agent/process_run_cleanup.py src/twicc/cli/run.py tests/test_timeline_boot.py
git commit   # subject: feat(timeline): reconcile lost processes at boot and record backend events
```

---

### Task 10: Backfill

**Files:**
- Create: `src/twicc/core/services/timeline_backfill.py`
- Modify: `src/twicc/cli/run.py` (start and cancel the background task)
- Test: `tests/test_timeline_backfill.py`

**Interfaces:**
- Consumes: `record_event_sync` (Task 3), `BACKFILL_PREFIX` (Task 9).
- Produces: `backfill_batch_sync(after_id: str, batch_size: int = 200) -> tuple[str | None, int]` (last session id seen, rows written; `(None, 0)` when no session is left) and `async run_timeline_backfill() -> None`.

Rules (spec §8, §9): only `type=SESSION`. `session.created` from `created_at` (skipped when null), `precision=derived`, `origin="sync"`, `spawned_by` from the `Session.spawned_by_id` column when set, `settings` null. For `archived`, `hidden`, `mute_on_user_turn` true and `pinned` not null: `<flag>.started` at `last_updated_at`, falling back to `created_at`, skipped when both are null, `precision=estimated`. **No process rows.** A subject is skipped when the session already has any row of that subject (live or backfilled): `session.created`; for a flag, any kind starting with `archived.` / `hidden.` / `muted.` / `pinned.`. Every row carries `dedupe_key="backfill:<kind>"`. Each batch runs under the write lock; the runner yields between batches.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timeline_backfill.py
from datetime import timedelta

import pytest
from django.utils import timezone

from twicc.core.models import Project, Session, SessionType, TimelineEvent
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_backfill import backfill_batch_sync, run_timeline_backfill
from tests.timeline_helpers import kinds, run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-backfill", directory="/tmp/tl-backfill")


def make(project, sid="s1", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex", **kw)


def run_all():
    run_with_writer(run_timeline_backfill)


def test_creation_row_is_derived_from_created_at(project):
    created = timezone.now() - timedelta(days=10)
    make(project, created_at=created)
    run_all()
    row = TimelineEvent.objects.get(session_id="s1")
    assert row.kind == "session.created" and row.occurred_at == created
    assert row.precision == "derived" and row.data["origin"] == "sync"
    assert row.dedupe_key == "backfill:session.created" and row.project_id == project.id
    assert row.data == {"origin": "sync", "provider": "codex", "settings": None, "spawned_by": None}


def test_creation_row_carries_the_spawner_when_the_column_is_set(project):
    make(project, "boss", created_at=timezone.now())
    make(project, "kid", created_at=timezone.now(), spawned_by_id="boss", selected_model="opus")
    run_all()
    data = TimelineEvent.objects.get(session_id="kid", kind="session.created").data
    assert data["spawned_by"] == "boss" and data["settings"] is None


def test_flag_rows_use_last_updated_at_then_created_at_else_are_skipped(project):
    updated = timezone.now() - timedelta(days=1)
    make(project, "a", created_at=updated - timedelta(days=5), last_updated_at=updated, archived=True)
    make(project, "b", created_at=updated - timedelta(days=5), hidden=True)
    make(project, "c", mute_on_user_turn=True)
    run_all()
    assert TimelineEvent.objects.get(session_id="a", kind="archived.started").occurred_at == updated
    assert TimelineEvent.objects.get(session_id="b", kind="hidden.started").precision == "estimated"
    assert kinds("c") == []


def test_pinned_row_carries_the_scope(project):
    make(project, created_at=timezone.now(), pinned="workspace")
    run_all()
    assert TimelineEvent.objects.get(session_id="s1", kind="pinned.started").data == {"scope": "workspace"}


def test_no_process_rows_are_ever_written(project):
    now = timezone.now()
    make(project, created_at=now, last_started_at=now, last_stopped_at=now)
    run_all()
    assert kinds("s1") == ["session.created"]


def test_subagents_are_skipped(project):
    parent = make(project, "p", created_at=timezone.now())
    make(project, "child", type=SessionType.SUBAGENT, parent_session=parent, created_at=timezone.now())
    run_all()
    assert kinds("child") == []


def test_running_twice_never_duplicates(project):
    make(project, created_at=timezone.now(), archived=True, last_updated_at=timezone.now())
    run_all()
    first = TimelineEvent.objects.count()
    run_all()
    assert TimelineEvent.objects.count() == first == 2


def test_an_existing_live_row_of_the_subject_suppresses_the_backfill_row(project):
    make(project, created_at=timezone.now(), archived=True, last_updated_at=timezone.now())
    record_event_sync("session.created", session_id="s1", actor="system", data={"origin": "twicc"})
    record_event_sync("archived.ended", session_id="s1", actor="user")
    run_all()
    assert kinds("s1") == ["session.created", "archived.ended"]


def test_batches_cover_every_session(project):
    for i in range(7):
        make(project, f"s{i}", created_at=timezone.now())
    last, written = backfill_batch_sync("", batch_size=3)
    assert written == 3 and last == "s2"
    run_all()
    assert TimelineEvent.objects.filter(kind="session.created").count() == 7
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_timeline_backfill.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/twicc/core/services/timeline_backfill.py
"""Conservative backfill of the timeline for existing sessions (design §8).

Writes only ``session.created`` and the flag intervals. Never a process row:
``Session.last_started_at`` / ``last_stopped_at`` are recomputed from the JSONL
file and cannot prove that TwiCC ran a process. Idempotent: every row carries
a ``backfill:`` dedupe key, and a subject that already has a row (live or
backfilled) is skipped.
"""

from __future__ import annotations

import asyncio
import logging

from django.db import transaction

from twicc.core.models import Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision
from twicc.core.services.session_timeline import record_event_sync
from twicc.core.services.timeline_boot import BACKFILL_PREFIX
from twicc.providers.db_writer import run_under_db_write_lock

logger = logging.getLogger(__name__)

BATCH_SIZE = 200

#: (flag attribute, started kind, subject prefix) — "set" means truthy / not null.
_FLAGS = (
    ("archived", TimelineKind.ARCHIVED_STARTED, "archived."),
    ("hidden", TimelineKind.HIDDEN_STARTED, "hidden."),
    ("mute_on_user_turn", TimelineKind.MUTED_STARTED, "muted."),
    ("pinned", TimelineKind.PINNED_STARTED, "pinned."),
)


def _existing_kinds(session_ids: list[str]) -> dict[str, set[str]]:
    existing: dict[str, set[str]] = {sid: set() for sid in session_ids}
    for session_id, kind in TimelineEvent.objects.filter(session_id__in=session_ids).values_list("session_id", "kind"):
        existing[session_id].add(kind)
    return existing


def _backfill_session(session: Session, existing: set[str]) -> int:
    written = 0
    common = dict(session_id=session.id, project_id=session.project_id, actor=TimelineActor.SYSTEM)
    if session.created_at is not None and TimelineKind.SESSION_CREATED not in existing:
        row = record_event_sync(
            TimelineKind.SESSION_CREATED, occurred_at=session.created_at, precision=TimelinePrecision.DERIVED,
            dedupe_key=f"{BACKFILL_PREFIX}{TimelineKind.SESSION_CREATED}",
            data={"origin": "sync", "provider": session.provider, "spawned_by": session.spawned_by_id},
            **common,
        )
        written += row is not None
    flag_time = session.last_updated_at or session.created_at
    if flag_time is None:
        return written
    for attribute, kind, prefix in _FLAGS:
        value = getattr(session, attribute)
        if not value or any(known.startswith(prefix) for known in existing):
            continue
        row = record_event_sync(
            kind, occurred_at=flag_time, precision=TimelinePrecision.ESTIMATED,
            dedupe_key=f"{BACKFILL_PREFIX}{kind}",
            data={"scope": value} if attribute == "pinned" else None, **common,
        )
        written += row is not None
    return written


def backfill_batch_sync(after_id: str, batch_size: int = BATCH_SIZE) -> tuple[str | None, int]:
    """Backfill the next ``batch_size`` sessions after ``after_id`` (keyset by id).

    Returns ``(last session id seen, rows written)``; ``(None, 0)`` when no
    session is left. Sync: call under the DB write lock from a worker thread.
    The ``existing`` snapshot is read under the same lock every live write
    takes, so no live row can appear between the read and the inserts.
    """
    sessions = list(
        Session.objects.filter(type=SessionType.SESSION, id__gt=after_id).order_by("id")[:batch_size]
    )
    if not sessions:
        return None, 0
    existing = _existing_kinds([s.id for s in sessions])
    written = 0
    with transaction.atomic():
        for session in sessions:
            written += _backfill_session(session, existing[session.id])
    return sessions[-1].id, written


async def run_timeline_backfill() -> None:
    """Backfill every session, one locked batch at a time, yielding in between."""
    after_id = ""
    total = 0
    while True:
        last_id, written = await run_under_db_write_lock(
            lambda current=after_id: asyncio.to_thread(backfill_batch_sync, current)
        )
        if last_id is None:
            break
        after_id = last_id
        total += written
        await asyncio.sleep(0)
    if total:
        logger.info("Timeline backfill wrote %d row(s)", total)
```

Inside a batch, `record_event_sync` uses a savepoint per row, so one bad row never aborts the batch.

- [ ] **Step 4: Start the task at boot**

In `src/twicc/cli/run.py`, define next to `_cancel_task` (line ~112):

```python
async def _guarded_timeline_backfill() -> None:
    from twicc.core.services.timeline_backfill import run_timeline_backfill

    try:
        await run_timeline_backfill()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Timeline backfill failed")
```

Three edits, mirroring `title_auto_task` (the stale-run cleanup runs earlier in the same function, ~line 242, so the DB writer is up and the boot reconciliation is done):

1. Right after `title_auto_task = asyncio.create_task(start_title_auto_task(shutdown_event))` (~line 278):

```python
    timeline_backfill_task = asyncio.create_task(_guarded_timeline_backfill(), name="timeline-backfill")
```

2. In the shutdown sequence, right after `await _cancel_task(title_auto_task, "Automatic title task")` (~line 412):

```python
            await _cancel_task(timeline_backfill_task, "Timeline backfill")
```

3. In the outer `finally:` (~line 524), so an early failure of `start_all` does not leak the task:

```python
            if not timeline_backfill_task.done():
                await _cancel_task(timeline_backfill_task, "Timeline backfill")
```

placed next to the identical `title_auto_task` guard, inside the same `try`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_timeline_backfill.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/twicc/core/services/timeline_backfill.py src/twicc/cli/run.py tests/test_timeline_backfill.py
git commit   # subject: feat(timeline): backfill session creation and flag states
```

---

### Task 11: Spec clarifications and documentation

**Files:**
- Modify: `docs/plans/2026-10-13-session-timeline-design.md`
- Modify: `CLAUDE.md` (section "Database Models")
- Modify: `AGENTS.md` (same section, if it mirrors CLAUDE.md)

- [ ] **Step 1: Amend the spec for the details this plan settled**

In `docs/plans/2026-10-13-session-timeline-design.md`:

1. Section 5, `title.changed` row: add "The title set when the session row is created is part of `session.created`; it writes no `title.changed`."
2. Section 8, "Mechanism": replace "A background job in the style of `compute_version` (idempotent, batched, resumable)" with "An idempotent background task started at boot after the stale-run cleanup; it walks sessions by id in locked batches and skips any subject that already has a row".
3. Section 9, "No cascade": replace "Sessions are never deleted by policy, so this is acceptable; a purge path, if one appears, must delete the timeline rows explicitly" with "The one session delete in the code base (`_apply_delete_sessions_payload`, internal Codex rollouts) deletes the timeline rows of the purged sessions explicitly; any future purge path must do the same".

- [ ] **Step 2: Document the model**

In `CLAUDE.md` "Database Models", add after the `PeerMessage` bullet:

```markdown
- **`TimelineEvent`** — append-only history of what happened to a session (and to the backend instance: `session_id`/`project` NULL). `session_id` is a plain CharField (like `ProcessRun`), `project` a raw FK. Kinds are a closed `subject.verb` enum (`<subject>.started`/`.ended` intervals, past-participle points); payloads are validated per kind (`core/timeline.py`). Written **only** through `core/services/session_timeline.record_event[_sync]` — never inserted directly, never raising, always under the DB write lock, with an explicit hook at every writer of the fact (never Django signals: the code base uses `queryset.update()`). `turn.ended` carries the background-work snapshot (non-null = waiting for the user while work still runs) and is re-emitted when it changes while idle; an `ended` row repeats the data of its open `started` row (`STARTED_OF`, applied by the recording service). Boot reconciliation (`timeline_boot.py`) writes `process.ended(reason="lost")` for processes a crash left behind; the backfill (`timeline_backfill.py`) writes only `session.created` and flag states, never process rows. Design: `docs/plans/2026-10-13-session-timeline-design.md`.
```

Mirror the bullet in `AGENTS.md` if it has a "Database Models" section (`grep -n "PeerMessage" AGENTS.md`); if it does not mirror that section, say so in the commit body and skip it.

- [ ] **Step 3: Commit**

```bash
git add docs/plans/2026-10-13-session-timeline-design.md CLAUDE.md AGENTS.md
git commit   # subject: docs(timeline): settle the spec details and document the model
```

---

### Task 12: Full verification

- [ ] **Step 1: Run the timeline suite and the whole suite**

Run: `uv run pytest tests -k "timeline" -v` then `uv run pytest -q` (about five minutes). `tests/test_migration_process.py` has timing-sensitive tests (subprocess timeouts and deadlines) that can flake under full-suite load: re-run that file alone before treating a failure as a regression.
Expected: all PASS. A failure in an existing test is a regression of a hook: fix the hook, not the test.

- [ ] **Step 2: Lint the new files**

Run: `uvx ruff check src/twicc/core/timeline.py src/twicc/core/services/session_timeline.py src/twicc/core/services/timeline_boot.py src/twicc/core/services/timeline_backfill.py src/twicc/agent/timeline_hooks.py tests/test_timeline_*.py tests/timeline_helpers.py`
Expected, after adding `# noqa: F811` to the tests that reuse the `env`/`session` fixtures re-imported from `tests/test_title_automation.py` (the import line carries `# noqa: F401`): no `F` or `B` violation in the new files. The project has no lint baseline, so style-only codes such as `C408` (`dict(...)` calls) are not fixed.

- [ ] **Step 3: Verify the migration state**

Run: `TWICC_DATA_DIR=$(mktemp -d) uv run python -m django makemigrations --check --dry-run --settings=twicc.settings_test`
Expected: `No changes detected`.

- [ ] **Step 4: Report to the user**

State, without running them: the user must `migrate` their instance and restart via `devctl.py` (new table `0156_timeline_event`, new boot code). Do not run either.

---

## Self-Review

**Spec coverage**

| Spec item | Task |
|---|---|
| §3 model, indexes, dedupe constraint | 1 |
| §4 naming, §5 catalogue (Tier 1) | 1, 2, 4-9 |
| §5 `session.created` `settings` / `spawned_by`, `title.changed` `old`, `awaiting_input` `tool_name`, `process.started` `hybrid` | 3, 6, 7, 8, 10 |
| §5 initial flags at creation | 3, 6 |
| §6 single entry point, lock, savepoint, never raises | 3 |
| §6 hook sites: process, flags, bulk archive, creation, title, settings | 4-8 |
| §6 inner intervals first, archive `caused_by`, archive-unpin | 4, 7 |
| §7 lost processes, `backend.*`, `boot_id`, clean shutdown | 9 |
| §8 backfill (no process rows) | 10 |
| §9 live-vs-backfill duplicate rule | 10 |
| Tier 2 and Tier 3 events | out of scope by the spec |
| §3 `ended` rows repeat `started` data (`STARTED_OF`) | 1, 3, 7, 9 |
| §5 `turn.ended` background work, re-emission while idle | 2, 7 |
| Open points §10 | Display pairing: out of scope. Project resolution: `agent.project_id` (Task 7). Actor/`via`, `awaiting_input.ended` `outcome`, Tier 2 and the `cron_restart`/`peer` causes: future scope, not in this plan |

**Placeholder scan:** the only deliberately open spots are the "adjust to the real signature" notes in Tasks 3, 4, 6 and 8 (test fixtures that must match code read at execution time) and the Step-1 enumeration of Task 8. Each names the exact file and command.

**Type consistency:** `record_event_sync` / `record_event` signatures are used identically in Tasks 3-10. `EventSpec`, `process_transition_events`, `ProcessSnapshot`, `BACKFILL_PREFIX` are defined once (Tasks 2, 7, 9) and imported where used. `persist_session_settings(..., actor=, reason=)` is defined in Task 8 and used by the hybrid agent in the same task.
