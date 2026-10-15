# Timeline Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Timeline" page that shows the recorded `TimelineEvent`s, newest first, one line per event, updated in real time. It is the first tab and the default tab of every scope page (global, workspace, project, worktree). Stats moves to its own `/stats` route.

**Architecture:** The backend adds a nullable `provider` column (filled by one central resolver), a read-only feed `GET /api/timeline/` (row-value keyset paging, `after_id` live fetch, a side table of cited sessions) and a light `timeline_changed` WebSocket signal (requested after commit, trailing throttle). The frontend keeps every decision in pure, unit-tested modules under `frontend/src/utils/timeline/` (wording and icons of the 19 kinds, author labels, grouping and day separators, the single fetch queue with its cursors). Thin Vue components show what those modules return.

**Tech Stack:** Python 3.13, Django 6 (async views, ORM, migration), SQLite, Channels (`updates` group), pytest + pytest-django. Vue 3 (`<script setup>`), Pinia, Web Awesome, node:test. No new dependency.

**Spec:** `docs/plans/2026-10-15-timeline-display-design.md` (reviewed over 3 rounds, PASS; decisions D1..D42 are binding). The implemented system it extends: `docs/plans/2026-10-13-session-timeline-design.md`, `docs/plans/2026-10-14-timeline-actor-origin-design.md`. Do not edit those documents. Do not commit `docs/plans/2026-10-14-session-timeline-handoff.md` (untracked on purpose).

---

## Working conventions (every task)

- **Worktree.** All work happens in `/home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline` (branch `feature/session-timeline`). Prefix EVERY Bash command with `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && `. Every command block of this plan starts with the full path (`cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && ...`): a fresh shell has no variable, and a `cd` to an unset one would run the command in `$HOME`.
- **Backend tests.** `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest <paths> -q`. Never plain `pytest`, never `uv run --active`, never `uv pip`.
- **Frontend tests.** `frontend/node_modules` exists in the worktree (checked: `vue`, `@vue/compiler-sfc`, `vue-router`, `pinia` are present). Run `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test <paths>` (or `npm test` for the whole suite, which is `node --test`). **Never run `npm install` or `npm ci`**: the project rule forbids it, and a parallel install corrupts devctl's `npm ci`. The pure modules (`frontend/src/utils/timeline/*`, `utils/projectTabs.js`) import nothing but other pure modules, so their tests run with node alone. The component tests compile the `.vue` files with `@vue/compiler-sfc` through `frontend/tests/helpers/scrollerComponentHarness.js` (already in the repo); they need `node_modules` to be present, nothing else. If `node_modules` were missing, the pure tests would still run and the user would run the component tests after his own `npm ci`; report it, do not install.
- **Lint.** `cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uvx ruff check <changed .py files>`. The branch writes zero findings on its own lines, including `C408` (use `{}` and `[]` literals) and `SIM117` (merge nested `with`). There is no frontend linter.
- **Migrations.** The migration FILE is created with `TWICC_DATA_DIR=$PWD uv run python -m django makemigrations core --settings=twicc.settings -n timeline_event_provider`. Sanity-check the data dir first (below). **NEVER run `migrate`** and never restart the servers: the user applies migrations and restarts himself.
- **Commits.** One commit per task. Conventional subject (`type(scope): lowercase summary`), a descriptive body, an explicit `git add <files>` list (never `-A`, never a directory), and, as the last line, the `Co-Authored-By` trailer of the model actually running (currently `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; the commit blocks below show that one). No CHANGELOG entry. No plugin version bump (no skill changes, see Task 11).
- **Language.** All code, comments, UI strings, docs and test names are English.
- **Forbidden.** `migrate`, `npm install`, `npm ci`, a server restart, editing historical design or plan documents, `Project.objects.create` outside tests, importing application code in a migration.

Data directory sanity check (run once, before `makemigrations`; the printed path must be inside the worktree):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings'); django.setup()
from django.conf import settings; print(settings.DATABASES['default']['NAME'])"
```

## Global Constraints

These are the spec's project-wide requirements. Every task obeys them.

**Data model (spec section 8)**

- `TimelineEvent.provider` = `CharField(max_length=50, null=True, blank=True)`. It stores the `Provider` value (`claude_code`, `codex`). NULL for an instance event and for a row whose session no source could name.
- Two new indexes, names fixed: `idx_timeline_occurred` on `(occurred_at, id)` and `idx_timeline_provider` on `(provider, occurred_at, id)`. The four existing indexes stay.
- One new migration, number **`0159`** (after `0158_timeline_event_origin`), file `0159_timeline_event_provider.py`: one `AddField` and two `AddIndex`, nothing else, no data migration.
- Resolution order of the `provider` column, in the recording service only: (1) an explicit value from the caller (free sources: the agent's provider, the loaded session, `ProcessRun.provider`); (2) `data["provider"]` of the validated payload (`session.created`, `process.started`; `process.ended` inherits it through `STARTED_OF`); (3) a bounded in-memory cache of session id to provider; (4) one primary-key query on `Session`. No session id, or a session with no row and no hint, gives NULL. Recording never raises: a failed resolution leaves NULL.
- Every write path carries the column: single path, bulk path (including the bulk archive, which reads `provider` from its `UPDATE ... RETURNING`), batch fallback, boot reconciliation, backfill.
- A guard test fails when a row with a `session_id` has a NULL `provider` after any writer ran (the session existing).

**Endpoint (spec section 7)**

- `GET /api/timeline/`, owner-only (the global API auth applies), read-only, GET only.
- Parameters: `project_ids` (comma separated, empty items dropped, empty or missing = global), `provider` (unknown = 400), `limit` (integer, default **50**, **1..100**, larger values capped to 100, zero, negative or non-integer = 400), `before` = `<occurred_at>,<id>` (the exact string the server returned, URL-encoded by the client), `after_id` (integer, `limit` does not apply, **cap 500** rows with `truncated: true`, negative = 400). `before` and `after_id` together = 400. Errors are `{"error": "..."}` with status 400.
- Response: `{"events", "has_more", "truncated", "latest_id", "sessions"}`. Each event: `id`, `kind`, `occurred_at`, `precision`, `actor`, `via`, `actor_ref`, `actor_name`, `reason`, `data`, `session_id`, `project_id`, `provider`. `recorded_at`, `dedupe_key`, `caused_by` are not sent.
- `occurred_at` format: UTC, six fractional digits, explicit `Z`: `YYYY-MM-DDTHH:MM:SS.ffffffZ`. The string orders like the time.
- Ordering `occurred_at DESC, id DESC`, never `recorded_at`. The keyset is a **row-value comparison** `(occurred_at, id) < (cursor)`, never the expanded `OR` form, and the query never has `OR project_id IS NULL`.
- Instance events (`session_id IS NULL`): the main query returns them only on the global scope without a provider; every other case runs a second query for them (same keyset, no provider filter); merge by `(occurred_at, id)` descending, deduplicate by id, cut at the limit (limit + 1 rows are read for `has_more`).
- `after_id`: events with a greater id, **id ascending**, scope and provider as in the table above, instance events included, cap 500 on the merged set; `has_more` is not meaningful for it.
- `latest_id` = highest id of the whole table, read **first**. An empty table gives `null`.
- Side table `sessions`: one entry per session cited by an event (`session_id`), by `actor_ref` when the actor is an agent, and by `data.spawned_by`. Entry = `title` (pending-title override applied, untitled = `null`), `project_id`, `provider`, `hidden`, `archived`. No `type`. A session id with no row has no entry. One query on `Session`, chunked.

**Live signal (spec section 9.1)**

- WebSocket frame `{"type": "timeline_changed", "latest_id": N}`, sent to the `updates` group inside the usual `{"type": "broadcast", "data": {...}}` envelope. It carries no session, project or content.
- **Throttle: `THROTTLE_SECONDS = 0.3`**, trailing: a burst gives one signal after the burst, a sustained stream gives about three per second.
- Requested **after commit** (`transaction.on_commit(..., robust=True)`), after a successful single write (not on a dedupe hit or a failure) and after a non-empty bulk insert. The emission (read of the latest id and send) never runs under the DB write lock. Backfill: silent per session, one signal at the end of a run that wrote rows. No running loop: no-op. A closed loop and a failed send are caught and logged. The signal never fails a writer.

**Client rules (spec section 9.2)**

- One queue serializes the first page, `before` pages and `after_id` fetches. Signals during a load coalesce into one follow-up fetch. A null live cursor counts as 0. The live cursor advances only on a first-page or `after_id` response (to its `latest_id`, never when `truncated`); a `before` response never advances it. The `before` cursor is the last row of the last fetched page. Fetched rows are inserted by `(occurred_at, id)` descending; while `has_more` is true a live row older than the loaded tail is not inserted. `truncated` discards the list and reloads the first page. A change of the project-id SET discards list, cursors and queue; a response for another set is ignored.
- Next page requested when the visible range is within **10 rows** of the end; loading is not re-entrant; a failed load shows an inline error with Retry; no "load more" button and no end marker.
- Catch-up (`after_id` from the live cursor, or the first page when there is no list) on `twicc:ws-reconnected`, on the first OPEN of the socket (`twicc:ws-first-open`, new) and when the Timeline tab becomes active again. `resync_required` is NOT handled by the Timeline.

**Routing and keys (spec sections 3.1, 10)**

- New routes `project-stats` (`/project/:projectId/stats`) and `projects-stats` (`/projects/stats`, workspace through `?workspace=`). The bare routes (`project`, `projects-all`) are the Timeline.
- Tab strip: Timeline, Stats, Files, Git (only with a git root), Terminal. Fixed shortcut map Alt+Shift+**1 Timeline, 2 Stats, 3 Files, 4 Git, 5 Terminal**; key 4 does nothing without a git root, key 5 is still Terminal. Settings panel row "Project home tabs": keys `1–5`, text `Jump to tab (Timeline, Stats, Files, Git, Terminal)`. Palette: `nav.project-tab.timeline` ("Switch to Timeline Tab", bare route) and `nav.project-tab.stats` ("Switch to Stats Tab", `/stats`), both gated by `isOnProjectDetail()`.
- The band carries the label "Timeline" and nothing else (no filter control). Strings are hardcoded English; the arrow between an old and a new value is the ASCII ` -> `. Every string from data is rendered as text, never as HTML (no `v-html`).

## Decisions left to the plan

| # | Decision | Why (one line) |
|---|---|---|
| P1 | The query logic lives in `core/services/timeline_feed.py` (sync, read-only); `views.timeline` stays a thin async wrapper. | Testable without HTTP, and `views.py` is already huge. |
| P2 | The `after_id` fetch is ONE primary-key range query: the scope columns carry a unary plus (`+project_id`, `+provider`, `+session_id`) so SQLite cannot pick a scope index. Instance events are part of the same `WHERE`. | Measured on 60 000 rows: 1.3 ms with the project index, 0.003 ms with the plus. A live fetch reads a handful of rows, not the whole scope. |
| P3 | `before` is parsed with `rpartition(",")`; an offset (`+02:00`) is accepted, a naive timestamp is a 400; a negative `after_id` is a 400. | The spec asks for the exact server string; a lenient parse of equivalent forms hurts nothing, a naive one is ambiguous. |
| P4 | Provider hints: writers that hold a free source pass it (hooks, boot, `session.created`, title, flags, bulk archive via `RETURNING`, backfill). The settings recorder and any caller with only a session id rely on the central cache plus one query. | `record_settings_change` only knows the id; widening `project_id_of` would touch every caller for one cached query. |
| P5 | The signal module keeps the loop in module state (`start_timeline_signal` / `stop_timeline_signal`), arms a `call_later` window on the first poke and sends when it fires. `poke_on_commit()` swallows every error. | One window per burst gives "trailing, about 3 per second"; a failing `on_commit` registration must not undo a written event. |
| P6 | The feed state is **per page** (one `createTimelineFeed` per `TimelinePanel`, no Pinia store). | `ProjectDetailPanel` is kept alive per scope, so the page keeps its own list; a store keyed by scope would add nothing and a leak. |
| P7 | The compact-height copy of the navigation list is the **first row of the list** (a synthetic `nav` row), not the scroller's `before` slot. | `useVirtualScroll` positions assume the first rendered item starts at the top of the scroll space; a tall `before` block would shift every position. A real row scrolls away and the band, outside the scroller, stays. |
| P8 | The scroller gets the class `timeline-scroller` with `flex: 1; min-height: 0` and `overflow-anchor: none` on `.at-bottom`. | The scroller's native bottom pin (`.at-bottom` re-enables scroll anchoring on its sentinel) would drag a newest-first list down at every older page. |
| P9 | Tooltips are native `title` attributes (several lines), not `AppTooltip`. | Hundreds of rows would need an id and a component each; a virtual list recycles rows. |
| P10 | `session.created` wording: the base follows `origin` ("Session created", "... from a terminal", "Session imported"); a parent adds " by <parent>" to "Session created" and " (spawned by <parent>)" to the others; never next to an agent author label. | Keeps the spec's literal case and a correct sentence for the combined cases the spec does not spell out. |
| P11 | A subagent session cannot be told from the side table (it has no `type`): `isSubagent(id)` is a best-effort predicate fed by the data store; the mention is plain text when it says yes. | Spec 5.4 asks for plain text; the store knows the subagents it loaded, the rest links. |
| P12 | A failed live (`after_id`) fetch is silent (logged); the next signal, reconnect, first OPEN or tab return retries it. | The spec defines an inline error for loads only; the cursor does not move, so nothing is lost. |
| P13 | `TimelinePanel` is mounted in its `wa-tab-panel` whatever the active tab and keeps fetching while hidden; `active` only triggers the catch-up when it turns true. | Matches "a kept-alive page that is not visible updates its data the same way". |
| P14 | Per-row-type height estimates (`day` 40, `event` 34, `nav` 96) are given to the scroller with `seedItemHeight`, in a post-render watcher on `[items, scrollerRef]` (the scroller does not exist before the first page), only for keys whose height the scroller does not know yet (`getItemHeight` is `undefined`: seeding a measured row would overwrite its measure and no resize would correct it); the `seeded` set is cleared when the scroller goes away and trimmed to the list. `minItemHeight` is 28. | The scroller takes one `minItemHeight`; `seedItemHeight` is its per-key hook, and it only suits rows not measured yet. |
| P15 | `compareNewestFirst` compares the `occurred_at` strings, never `Date.parse`. | The wire format is fixed-width, so the string orders like the time and keeps the microseconds; `Date.parse` would merge rows of the same millisecond. |
| P16 | The rail keeps a scope selected on its Stats tab as well as on its Timeline (`SidebarRail.scopeSelected` also matches `project-stats` / `projects-stats`). | The bare route used to be Stats; without this the highlight would vanish on that tab. Files, Git and Terminal never had it, and still do not. |
| P17 | A scope page without a git root that sits on the Git route is redirected to the scope home, which is now the Timeline (it was Stats). | The home is the Timeline everywhere; pinned by a test and a code comment. |

## Review Focus

Five failure modes the spec implies but no ordinary test would exercise. Each has a named test in the owning task.

1. **Cursor round trip on text datetimes (T2).** SQLite stores `2026-10-14 10:00:00` (no fraction) for a whole second and `...10:00:00.000900` otherwise; the keyset must adapt the cursor with `connection.ops.adapt_datetimefield_value` and the endpoint must emit six digits, or rows are skipped or repeated at page boundaries. Tests: `test_paging_by_before_returns_every_row_exactly_once_with_many_equal_timestamps` (whole seconds; its loop is bounded and fails instead of hanging if the cursor regresses), `test_the_cursor_round_trips_microseconds_for_rows_equal_to_the_millisecond`.
2. **A signal for a row that never persisted, or before the commit (T3).** `poke` must run on commit only; a rolled-back write, a dedupe hit and a failed insert send nothing; a failing signal never fails the writer. Tests: `test_a_single_write_requests_the_signal_only_after_the_commit`, `test_a_rolled_back_write_sends_nothing`, `test_a_failing_poke_does_not_fail_the_writer`, `test_the_emission_does_not_need_the_db_write_lock`.
3. **The queue race (T6).** A row committed after a `before` query read, with a time inside the range that page brings, must not be lost to a concurrent `after_id` fetch that drops it as "older than the old tail". Test: `the queue race: a row committed after a before query read but inside the new range is not lost`.
4. **The scroller drags or jumps (T8, T10).** The native bottom pin of the scroller would carry a newest-first list down; rows inserted above a scrolled-down view must not move it, while pagination below must not touch the scroll. Tests: the `.at-bottom` source pin (T8), `scrolled down, rows inserted above put the first visible row back; at the top nothing moves` and `scrolled down, rows added below (an older page): the scroll is left alone` (T10).
5. **A hidden, kept-alive page (T10).** A page that is not visible must keep listening, catch up once when it comes back, and release its window listeners when destroyed (a leak would keep every closed scope fetching forever). Tests: `coming back to the Timeline tab catches up once`, `an unmounted page stops listening: its window listeners are removed and nothing is fetched`.

## File Structure

New files:

| Path | Role |
|---|---|
| `src/twicc/core/migrations/0159_timeline_event_provider.py` | Generated by `makemigrations`: column + two indexes. |
| `src/twicc/core/services/timeline_feed.py` | Read side: request parsing, the two query shapes, side table, serialization. |
| `src/twicc/core/services/timeline_signal.py` | The throttled `timeline_changed` signal. |
| `tests/test_timeline_provider.py`, `tests/test_timeline_api.py`, `tests/test_timeline_signal.py` | Backend tests. |
| `frontend/src/utils/timeline/eventTime.js` | Time ordering, formatting, truncation helpers. |
| `frontend/src/utils/timeline/settingsDiff.js`, `backgroundBadge.js` | `settings.changed` wording, `turn.ended` badge. |
| `frontend/src/utils/timeline/sessions.js`, `authorLabel.js` | Session mention and link rules, "by ..." label. |
| `frontend/src/utils/timeline/describeEvent.js` | The view-model of one event row (19 kinds, fallbacks, tooltip). |
| `frontend/src/utils/timeline/rows.js`, `scope.js` | Grouping, separators, scope resolution. |
| `frontend/src/utils/timeline/feed.js` | Fetch queue, cursors, insertion rules, URL builder. |
| `frontend/src/utils/timeline/providerLabels.js` | Adapter from provider helpers to the settings diff. |
| `frontend/src/utils/timeline/liveEvents.js`, `scrollKeeper.js` | Window events binding, scroll keeping. |
| `frontend/src/utils/projectTabs.js` | Tab list, numbering and routes of a scope page. |
| `frontend/src/components/timeline/TimelineSessionMention.vue`, `TimelineEventRow.vue`, `TimelinePanel.vue` | The components. |
| `frontend/src/composables/useTimelineFeed.js` | Reactive feed + the real fetch. |
| one `*.test.js` beside each module above | node:test suites. |
| `frontend/src/utils/projectTabsRouting.test.js` | Routes, scope memory, rail, palette, settings text pins. |

Modified files, by task:

| Path | Task |
|---|---|
| `src/twicc/core/models.py`, `core/services/session_timeline.py`, `agent/timeline_hooks.py`, `core/services/timeline_boot.py`, `timeline_backfill.py`, `session_update.py`, `session_visibility.py` | T1 |
| `src/twicc/views.py`, `src/twicc/urls.py` | T2 |
| `src/twicc/core/services/session_timeline.py`, `timeline_backfill.py`, `src/twicc/cli/run.py` | T3 |
| `frontend/src/styles/glass.test.js`, `panel-veil.test.js` | T8 |
| `frontend/src/router.js`, `utils/tabRoutes.js`, `utils/scopeMemory.js`, `App.vue`, `commands/staticCommands.js`, `components/app/SettingsPopover.vue`, `components/sidebar/SidebarRail.vue`, `components/project/ProjectDetailPanel.vue` | T9 |
| `frontend/src/components/timeline/TimelinePanel.vue` (+ test), `composables/useWebSocket.js`, `ProjectDetailPanel.vue`, `utils/projectTabsRouting.test.js` (the mount pin) | T10 |
| `CLAUDE.md`, `AGENTS.md`, `frontend/public/tips/project-page.md` | T11 |

Verified facts the tasks rely on (checked against the repo on 2026-10-15; the code of this plan was applied task by task in a scratch copy and the tests passed at every task):

- `TimelineEvent` writers: `record_event_sync`, `record_events_bulk_sync`, `insert_event_rows` (raw `executemany` built from `_meta.concrete_fields`), `record_session_created`, `record_title_change` in `core/services/session_timeline.py`; the backfill builds `TimelineEvent(...)` rows in `timeline_backfill._row`; there is no other constructor of the model in `src/`.
- `daily_activity` is the model for a `project_ids` endpoint (`views.py`), but it does not drop empty items; `timeline_feed.parse_request` does.
- `VirtualScroller` exposes `getScrollAnchor`, `scrollToAnchor`, `scrollToTop`, `getScrollState`, `getVisibleRange`, `getItemHeight`, `seedItemHeight`; it emits `update` with `{startIndex, endIndex, visibleStartIndex, visibleEndIndex}` and `became-visible`; its root has `overflow-anchor: none`, re-enabled by `.at-bottom`.
- `useWebSocket.js` dispatches `twicc:ws-reconnected` only when `wasConnected` is true (`isReconnection`); it has no event for the first OPEN.
- `ProjectDetailPanel` renders `ProjectDetailNavList` in the Stats tab (`.stats-nav-list`, shown only under `html.compact-height`); the page header omits it in that mode.
- SQLite plans (3.53.1, no ANALYZE): `(occurred_at, id) < (?, ?)` uses `idx_timeline_occurred`; with `project_id IN (...)` it uses `idx_timeline_project` (single `SEARCH`, a temp b-tree only for several ids); `session_id IS NULL` uses `idx_timeline_session` without a sort. T2 pins these with `EXPLAIN QUERY PLAN`.

---

## Task 1: The `provider` column and its central resolution

**Files:**
- Modify: `src/twicc/core/models.py` (column + two indexes)
- Create: `src/twicc/core/migrations/0159_timeline_event_provider.py` (generated)
- Modify: `src/twicc/core/services/session_timeline.py` (resolver, cache, `provider=` plumbing)
- Modify: `src/twicc/agent/timeline_hooks.py`, `src/twicc/core/services/timeline_boot.py`, `src/twicc/core/services/timeline_backfill.py`, `src/twicc/core/services/session_update.py`, `src/twicc/core/services/session_visibility.py` (pass the free source)
- Create: `tests/test_timeline_provider.py`
- Modify: `tests/conftest.py` (an autouse fixture clears the resolver cache around every test)

**Interfaces:**
- Consumes: nothing new.
- Produces (used by T2, T3):
  - `TimelineEvent.provider` (`str | None`), indexes `idx_timeline_occurred`, `idx_timeline_provider`.
  - In `twicc.core.services.session_timeline`: `PROVIDER_MAX_LENGTH = 50`; `resolve_provider(session_id: str | None, *, explicit=None, data: dict | None = None) -> str | None`; `clear_provider_cache() -> None`; `record_event_sync(..., provider=None)`; `EventSpec(..., provider: str | None = None)` (last field).

- [ ] **Step 1: Write the failing tests**

Create the test module. It covers the model and indexes, the migration content, the resolver order and its cache, every writer path (single, bulk, bulk archive without a per-session query, batch fallback, boot with and without a Session row, process hooks, `session.created` + flag rows, title, settings, flag hooks, backfill) and the guard.

`tests/test_timeline_provider.py` (new file):

```python
# tests/test_timeline_provider.py
"""The ``provider`` column of ``TimelineEvent`` (display design 2026-10-15, section 8)."""

import importlib
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from django.core.management import call_command
from django.db import connection, migrations
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from twicc.agent.timeline_hooks import (
    ProcessSnapshot, record_process_changes, record_process_ended, record_process_started,
)
from twicc.core.enums import Provider
from twicc.core.models import (
    ProcessRun, Project, Session, TimelineActor, TimelineEvent, TimelineKind,
)
from twicc.core.services import session_timeline
from twicc.core.services.session_timeline import (
    PROVIDER_MAX_LENGTH, EventSpec, record_event_sync, record_events_bulk_sync,
    record_session_created, record_title_change, resolve_provider,
)
from twicc.core.services.session_update import (
    apply_session_archived_change, apply_session_mute_on_user_turn_change, apply_session_pinned_change,
    archive_sessions_bulk, record_settings_change,
)
from twicc.core.services.timeline_backfill import run_timeline_backfill
from twicc.core.services.timeline_boot import reconcile_at_boot
from twicc.request_origin import UI_ORIGIN
from tests.timeline_helpers import run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-provider", directory="/tmp/tl-provider")


def make_session(project, sid="s1", provider="codex", **kw):
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider=provider, **kw)


def select_session_queries(captured):
    """The SELECTs that read the ``core_session`` table, from a ``CaptureQueriesContext``."""
    return [
        q["sql"] for q in captured.captured_queries
        if q["sql"].lstrip().upper().startswith("SELECT") and '"core_session"' in q["sql"]
    ]


@pytest.fixture
def explicit_hints(monkeypatch):
    """Record the ``explicit`` hint that every writer hands to ``resolve_provider``."""
    seen = []
    original = session_timeline.resolve_provider

    def spy(session_id, *, explicit=None, data=None):
        seen.append((session_id, explicit))
        return original(session_id, explicit=explicit, data=data)

    monkeypatch.setattr(session_timeline, "resolve_provider", spy)
    return seen


def rows_without_provider():
    """The guard: rows that name a session but have no provider."""
    return list(TimelineEvent.objects.filter(session_id__isnull=False, provider__isnull=True))


# -- Model, indexes, migration ----------------------------------------------------------------------------


def test_the_column_is_nullable_and_as_wide_as_the_constant():
    field = TimelineEvent._meta.get_field("provider")
    assert field.null and field.blank and field.max_length == PROVIDER_MAX_LENGTH == 50


def test_the_two_display_indexes_exist_in_the_model_and_in_the_database():
    declared = {index.name: list(index.fields) for index in TimelineEvent._meta.indexes}
    assert declared["idx_timeline_occurred"] == ["occurred_at", "id"]
    assert declared["idx_timeline_provider"] == ["provider", "occurred_at", "id"]
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, TimelineEvent._meta.db_table)
    assert constraints["idx_timeline_occurred"]["columns"] == ["occurred_at", "id"]
    assert constraints["idx_timeline_provider"]["columns"] == ["provider", "occurred_at", "id"]
    # The earlier indexes stay.
    for name in ("idx_timeline_session", "idx_timeline_project", "idx_timeline_kind", "idx_timeline_session_kind"):
        assert name in constraints


def test_migration_0159_adds_the_column_and_the_two_indexes_only():
    module = importlib.import_module("twicc.core.migrations.0159_timeline_event_provider")
    migration = module.Migration
    assert migration.dependencies == [("core", "0158_timeline_event_origin")]
    assert [type(op) for op in migration.operations] == [
        migrations.AddField, migrations.AddIndex, migrations.AddIndex,
    ]
    assert {op.model_name for op in migration.operations} == {"timelineevent"}
    assert migration.operations[0].name == "provider"
    field = migration.operations[0].field
    # Nullable and without a default: the rows that already exist read NULL, nothing is rewritten.
    assert field.null is True and not field.has_default() and field.max_length == 50
    assert [op.index.name for op in migration.operations[1:]] == ["idx_timeline_occurred", "idx_timeline_provider"]


def test_the_models_and_the_migrations_agree():
    # ``--check`` exits with status 1 when the models differ from the migrations.
    call_command("makemigrations", "core", check=True, dry_run=True, verbosity=0)


# Existing rows: the migration is a nullable AddField without a default (checked above), so rows written before
# it read NULL; here only the model default of a new row is checked.
def test_the_column_defaults_to_null(project):
    row = TimelineEvent.objects.create(
        session_id="old", kind=TimelineKind.TURN_STARTED, occurred_at=timezone.now(), actor=TimelineActor.AGENT,
    )
    row.refresh_from_db()
    assert row.provider is None


# -- Central resolution -----------------------------------------------------------------------------------


def test_an_explicit_value_wins_over_the_payload_the_cache_and_the_row(project):
    make_session(project, provider="claude_code")
    session_timeline._remember_provider("s1", "claude_code")
    with CaptureQueriesContext(connection) as captured:
        assert resolve_provider("s1", explicit="codex", data={"provider": "claude_code"}) == "codex"
    assert select_session_queries(captured) == []


def test_the_payload_wins_over_the_cache_and_the_row(project):
    make_session(project, provider="claude_code")
    session_timeline._remember_provider("s1", "claude_code")
    with CaptureQueriesContext(connection) as captured:
        assert resolve_provider("s1", data={"provider": "codex"}) == "codex"
    assert select_session_queries(captured) == []


def test_the_cache_wins_over_the_row(project):
    make_session(project, provider="claude_code")
    session_timeline._remember_provider("s1", "codex")
    with CaptureQueriesContext(connection) as captured:
        assert resolve_provider("s1") == "codex"
    assert select_session_queries(captured) == []


def test_the_row_is_read_once_then_cached(project):
    make_session(project, provider="codex")
    with CaptureQueriesContext(connection) as captured:
        assert resolve_provider("s1") == "codex"
        assert resolve_provider("s1") == "codex"
    assert len(select_session_queries(captured)) == 1


def test_a_provider_enum_member_is_stored_by_value(project):
    assert resolve_provider("s1", explicit=Provider.CODEX) == "codex"


def test_a_hint_that_is_not_a_string_is_ignored(project):
    make_session(project, provider="codex")
    assert resolve_provider("s1", explicit=7, data={"provider": ""}) == "codex"


def test_an_instance_event_has_no_provider_and_no_query():
    with CaptureQueriesContext(connection) as captured:
        assert resolve_provider(None, explicit="codex", data={"provider": "codex"}) is None
    assert len(captured) == 0


def test_a_session_with_no_row_and_no_hint_gets_null_and_is_not_cached(project):
    assert resolve_provider("ghost") is None
    make_session(project, sid="ghost", provider="codex")
    assert resolve_provider("ghost") == "codex"


def test_the_cache_is_bounded_and_drops_the_least_recently_used(monkeypatch):
    monkeypatch.setattr(session_timeline, "_PROVIDER_CACHE_MAX", 2)
    for sid in ("a", "b"):
        session_timeline._remember_provider(sid, "codex")
    session_timeline._remember_provider("c", "codex")
    assert list(session_timeline._provider_cache) == ["b", "c"]
    assert resolve_provider("b") == "codex"
    session_timeline._remember_provider("d", "codex")
    assert list(session_timeline._provider_cache) == ["b", "d"]


def test_a_failing_lookup_gives_null_and_never_raises(project, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(session_timeline.Session.objects, "filter", boom)
    assert resolve_provider("s1") is None


def test_the_event_is_written_when_the_resolution_fails(project, monkeypatch):
    monkeypatch.setattr(session_timeline.Session.objects, "filter", lambda *a, **k: 1 / 0)
    event = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
    assert event is not None and event.provider is None


# -- Every writer -----------------------------------------------------------------------------------------


def test_the_single_path_resolves_the_provider_from_the_session_row(project):
    make_session(project)
    event = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
    event.refresh_from_db()
    assert event.provider == "codex"


def test_an_instance_event_is_stored_without_provider():
    event = record_event_sync(
        TimelineKind.BACKEND_STARTED, actor=TimelineActor.SYSTEM, data={"pid": 1},
    )
    assert event.provider is None


def test_the_bulk_path_resolves_each_spec_and_honours_a_hint(project):
    make_session(project, "a", provider="codex")
    make_session(project, "b", provider="claude_code")
    written = record_events_bulk_sync([
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="a"),
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="b"),
        EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="ghost", provider="codex"),
        EventSpec(TimelineKind.BACKEND_STARTED, data={"pid": 1}),
    ])
    assert written == 4
    assert dict(TimelineEvent.objects.values_list("session_id", "provider")) == {
        "a": "codex", "b": "claude_code", "ghost": "codex", None: None,
    }


def test_the_batch_fallback_carries_the_provider(project, monkeypatch):
    make_session(project)

    def failing_batch(rows):
        raise RuntimeError("batch insert failed")

    monkeypatch.setattr(session_timeline, "insert_event_rows", failing_batch)
    assert record_events_bulk_sync([EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="s1")]) == 1
    assert TimelineEvent.objects.get(session_id="s1").provider == "codex"
    # A session with no row: the hint of the spec is the only source. The batch remembered it in the cache;
    # evict it, as a busy process could, so the fallback row must carry the value it was built with.
    monkeypatch.setattr(
        session_timeline, "insert_event_rows", lambda rows: (session_timeline.clear_provider_cache(), failing_batch(rows)),
    )
    spec = EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="ghost", provider="claude_code")
    assert record_events_bulk_sync([spec]) == 1
    assert TimelineEvent.objects.get(session_id="ghost").provider == "claude_code"


def test_a_provider_longer_than_the_column_is_cut_not_rejected(project):
    assert resolve_provider("s1", explicit="p" * 80) == "p" * PROVIDER_MAX_LENGTH
    event = record_event_sync(TimelineKind.TURN_STARTED, session_id="s2", actor=TimelineActor.AGENT, provider="q" * 80)
    assert event is not None
    event.refresh_from_db()
    assert event.provider == "q" * PROVIDER_MAX_LENGTH


def test_the_bulk_archive_takes_the_provider_from_the_update_not_from_a_query(project):
    make_session(project, "a", provider="codex")
    make_session(project, "b", provider="claude_code")
    with CaptureQueriesContext(connection) as captured:
        assert sorted(archive_sessions_bulk({"a", "b"})) == ["a", "b"]
    assert dict(TimelineEvent.objects.values_list("session_id", "provider")) == {"a": "codex", "b": "claude_code"}
    assert select_session_queries(captured) == []


def test_the_boot_reconciliation_uses_the_process_run_provider_even_without_a_session_row(project):
    now = timezone.now()
    ProcessRun.objects.create(
        provider="codex", session_id="lost", started_at=now - timedelta(hours=3),
        state="assistant_turn", last_state_change_at=now - timedelta(hours=1),
    )
    reconcile_at_boot(now=now, boot_id="b1")
    lost = list(TimelineEvent.objects.filter(session_id="lost").order_by("id"))
    assert [row.kind for row in lost] == ["turn.ended", "process.ended"]
    assert {row.provider for row in lost} == {"codex"}
    assert TimelineEvent.objects.get(kind=TimelineKind.BACKEND_STARTED).provider is None


def test_the_process_hooks_pass_the_agent_provider(project, explicit_hints):
    agent = SimpleNamespace(
        session_id="nosession", project_id=None, provider=Provider.CODEX, ephemeral=False, process_run=None,
        pending_requests=(), kill_reason=None, error=None, is_hybrid=False,
    )
    record_process_started(agent, resume=False)
    record_process_changes(
        agent, ProcessSnapshot("user_turn", False, None), new_state="assistant_turn", new_awaiting=False, new_work=None,
    )
    record_process_ended(agent, ProcessSnapshot("assistant_turn", False, None))
    rows = TimelineEvent.objects.filter(session_id="nosession")
    assert rows.count() == 4
    assert {row.provider for row in rows} == {"codex"}
    assert {explicit for sid, explicit in explicit_hints if sid == "nosession"} == {"codex"}


def test_session_created_and_its_flag_rows_pass_the_session_provider(project, explicit_hints):
    session = Session(
        id="fresh", file_path="fresh.jsonl", project=project, provider="claude_code", hidden=True,
        mute_on_user_turn=True,
    )
    record_session_created(session, origin="twicc")
    rows = list(TimelineEvent.objects.filter(session_id="fresh").order_by("id"))
    assert [row.kind for row in rows] == ["session.created", "hidden.started", "muted.started"]
    assert {row.provider for row in rows} == {"claude_code"}
    assert {explicit for sid, explicit in explicit_hints if sid == "fresh"} == {"claude_code"}


def test_the_title_recorder_passes_the_session_provider(project, explicit_hints):
    make_session(project)
    record_title_change("s1", "New", source="user", old="Old", origin=UI_ORIGIN)
    assert TimelineEvent.objects.get(session_id="s1").provider == "codex"
    assert [explicit for sid, explicit in explicit_hints if sid == "s1"] == ["codex"]


def test_the_settings_recorder_resolves_through_the_central_rule(project):
    make_session(project)
    record_settings_change("s1", {"effort": "low"}, {"effort": "high"})
    assert TimelineEvent.objects.get(session_id="s1").provider == "codex"


@pytest.fixture
def no_agent_side_effects(monkeypatch):
    from twicc import terminal
    from twicc.agent import registry

    monkeypatch.setattr(registry, "get_agent_manager_registry",
                        lambda: SimpleNamespace(kill_agent=AsyncMock(), get_agent_info=lambda session_id: None))
    monkeypatch.setattr(terminal, "kill_all_tmux_terminals", lambda scope: None)


def test_the_flag_hooks_pass_the_session_provider(project, explicit_hints, no_agent_side_effects):
    from twicc.core.services.session_visibility import _apply_visibility_flag

    session = make_session(project)
    run_with_writer(lambda: apply_session_pinned_change(session, "project"))
    run_with_writer(lambda: apply_session_mute_on_user_turn_change(session, True))
    run_with_writer(lambda: apply_session_archived_change(session, True, also_unpin=True))
    _apply_visibility_flag("s1", True)
    rows = TimelineEvent.objects.filter(session_id="s1")
    assert sorted(row.kind for row in rows) == sorted([
        "pinned.started", "muted.started", "archived.started", "pinned.ended", "hidden.started",
    ])
    assert {row.provider for row in rows} == {"codex"}
    assert {explicit for sid, explicit in explicit_hints if sid == "s1"} == {"codex"}


def test_the_backfill_writes_the_session_provider(project):
    make_session(project, "a", provider="codex", created_at=timezone.now(), archived=True)
    make_session(project, "b", provider="claude_code", created_at=timezone.now())
    run_with_writer(run_timeline_backfill)
    assert dict(TimelineEvent.objects.filter(kind="session.created").values_list("session_id", "provider")) == {
        "a": "codex", "b": "claude_code",
    }
    assert {row.provider for row in TimelineEvent.objects.filter(session_id="a")} == {"codex"}


def test_guard_no_row_with_a_session_keeps_a_null_provider_after_any_writer_ran(project, no_agent_side_effects):
    """Every writer path on an existing session, then the guard (display design, section 8)."""
    from twicc.core.services.session_visibility import _apply_visibility_flag

    session = make_session(project, created_at=timezone.now(), archived=False)
    make_session(project, "bulk-a")
    make_session(project, "backfilled", created_at=timezone.now(), archived=True)
    record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
    record_events_bulk_sync([EventSpec(TimelineKind.MUTED_STARTED, session_id="s1")])
    archive_sessions_bulk({"bulk-a"})
    record_title_change("s1", "T", source="auto")
    record_settings_change("s1", {"effort": "low"}, {"effort": "high"})
    run_with_writer(lambda: apply_session_pinned_change(session, "all"))
    _apply_visibility_flag("s1", True)
    run_with_writer(run_timeline_backfill)
    assert TimelineEvent.objects.filter(session_id__isnull=False).count() >= 9
    assert rows_without_provider() == []


def test_every_timeline_event_constructor_in_src_passes_the_provider():
    """Source guard (pattern of ``test_timeline_origin_guard.py``): a new writer that builds a row must pass it.

    Covers ``TimelineEvent(...)`` and ``TimelineEvent.objects.create / bulk_create / get_or_create``.
    """
    import ast
    from pathlib import Path

    import twicc

    src = Path(twicc.__file__).resolve().parent
    found, missing = 0, []
    for path in src.rglob("*.py"):
        if path == src / "core" / "models.py" or "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            direct = isinstance(func, ast.Name) and func.id == "TimelineEvent"
            manager = (
                isinstance(func, ast.Attribute) and func.attr in {"create", "bulk_create", "get_or_create"}
                and isinstance(func.value, ast.Attribute) and func.value.attr == "objects"
                and isinstance(func.value.value, ast.Name) and func.value.value.id == "TimelineEvent"
            )
            if direct or manager:
                found += 1
                if "provider" not in {keyword.arg for keyword in node.keywords}:
                    missing.append(f"{path.relative_to(src).as_posix()}:{node.lineno}")
    assert found >= 3, "the guard no longer finds the constructors it guards"
    assert missing == []


def test_the_guard_itself_detects_a_null_provider():
    TimelineEvent.objects.create(
        session_id="raw", kind=TimelineKind.TURN_STARTED, occurred_at=timezone.now(), actor=TimelineActor.AGENT,
    )
    TimelineEvent.objects.create(
        kind=TimelineKind.BACKEND_STARTED, occurred_at=timezone.now(), actor=TimelineActor.SYSTEM, data={"pid": 1},
    )
    assert [row.session_id for row in rows_without_provider()] == ["raw"]
```


The cache is process-wide and the tests reuse ids such as `s1` with different providers (in production an id is never reused). One autouse fixture in the shared `conftest.py` empties it around every test, so no suite sees another's sessions:

`tests/conftest.py` (modify):

```diff
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -60,8 +60,22 @@ def pytest_configure():
         settings.configure()
     django.setup()
 
 
+@pytest.fixture(autouse=True)
+def clear_timeline_provider_cache():
+    """The timeline's session-to-provider cache is process-wide: a test must not see another test's sessions.
+
+    Session ids are reused by the tests ("s1") with different providers, which the cache (ids are never reused
+    in production) would otherwise answer from a previous test.
+    """
+    from twicc.core.services.session_timeline import clear_provider_cache
+
+    clear_provider_cache()
+    yield
+    clear_provider_cache()
+
+
 @pytest.fixture(autouse=True)
 def cheap_password_hashes(monkeypatch):
     """Hash new passwords with 1 000 PBKDF2 rounds instead of 600 000.
 
```


- [ ] **Step 2: Run the tests and see them fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_provider.py -q
```

Expected: a collection error, `ImportError: cannot import name 'PROVIDER_MAX_LENGTH' from 'twicc.core.services.session_timeline'`.

- [ ] **Step 3: Add the column and the two indexes, then generate the migration**

Edit the model:

`src/twicc/core/models.py` (modify):

```diff
--- a/src/twicc/core/models.py
+++ b/src/twicc/core/models.py
@@ -2254,8 +2254,12 @@ class TimelineEvent(models.Model):
     # ``actor_name`` is a snapshot. The widths mirror ``twicc.request_origin``; a test pins them.
     via = models.CharField(max_length=20, null=True, blank=True)
     actor_ref = models.CharField(max_length=255, null=True, blank=True)
     actor_name = models.CharField(max_length=80, null=True, blank=True)
+    # Provider of the session (``Provider`` value), filled by the recording service
+    # (``resolve_provider``). NULL for an instance event and for a row whose session no source could name.
+    # Design: ``docs/plans/2026-10-15-timeline-display-design.md`` section 8.
+    provider = models.CharField(max_length=50, null=True, blank=True)
     reason = models.CharField(max_length=30, blank=True, default="")
     caused_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="effects")
     data = models.JSONField(default=dict, blank=True)
     dedupe_key = models.CharField(max_length=100, null=True, blank=True)
@@ -2265,8 +2269,11 @@ class TimelineEvent(models.Model):
             models.Index(fields=["session_id", "occurred_at", "id"], name="idx_timeline_session"),
             models.Index(fields=["project", "occurred_at", "id"], name="idx_timeline_project"),
             models.Index(fields=["kind", "occurred_at"], name="idx_timeline_kind"),
             models.Index(fields=["session_id", "kind"], name="idx_timeline_session_kind"),
+            # The display feed (design 2026-10-15, section 8): the global feed and the provider filter.
+            models.Index(fields=["occurred_at", "id"], name="idx_timeline_occurred"),
+            models.Index(fields=["provider", "occurred_at", "id"], name="idx_timeline_provider"),
         ]
         constraints = [
             models.UniqueConstraint(
                 fields=["session_id", "dedupe_key"],
```


Generate the migration file (sanity-check the data dir first, see Working conventions). **Do not run `migrate`.**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run python -m django makemigrations core --settings=twicc.settings -n timeline_event_provider
```

Expected output: `0159_timeline_event_provider.py` with `Add field provider to timelineevent` and the two `Create index` lines, nothing else. The file must read as follows (only the "Generated by" date line differs):

`src/twicc/core/migrations/0159_timeline_event_provider.py` (new file):

```python
# Generated by Django 6.1.2 on 2026-10-15 07:51

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0158_timeline_event_origin'),
    ]

    operations = [
        migrations.AddField(
            model_name='timelineevent',
            name='provider',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddIndex(
            model_name='timelineevent',
            index=models.Index(fields=['occurred_at', 'id'], name='idx_timeline_occurred'),
        ),
        migrations.AddIndex(
            model_name='timelineevent',
            index=models.Index(fields=['provider', 'occurred_at', 'id'], name='idx_timeline_provider'),
        ),
    ]
```


- [ ] **Step 4: Write the resolver and pass the free sources**

`session_timeline.py` gets the cache, `resolve_provider`, the `provider` keyword of `record_event_sync`, the `EventSpec.provider` field, the bulk path and fallback, and the hints of `record_session_created` and `record_title_change`:

`src/twicc/core/services/session_timeline.py` (modify):

```diff
--- a/src/twicc/core/services/session_timeline.py
+++ b/src/twicc/core/services/session_timeline.py
@@ -10,9 +10,10 @@ never block or undo the user action it describes.
 from __future__ import annotations
 
 import asyncio
 import logging
-
+import threading
+from collections import OrderedDict
 from typing import NamedTuple
 
 from django.db import IntegrityError, connection, transaction
 from django.utils import timezone
@@ -28,8 +29,11 @@ from twicc.request_origin import (
 )
 
 logger = logging.getLogger(__name__)
 
+#: Width of ``TimelineEvent.provider``; a test pins it to the model.
+PROVIDER_MAX_LENGTH = 50
+
 
 def _truncate(value, limit: int) -> str | None:
     return None if value is None else str(value)[:limit]
 
@@ -51,8 +55,69 @@ def origin_columns(resolved: RequestOrigin) -> dict:
         "actor_name": _truncate(resolved.actor_name, ACTOR_NAME_MAX_LENGTH),
     }
 
 
+#: Provider of a session id, filled on demand. A session's provider never changes and ids are never
+#: reused, so an entry never goes stale. Bounded (least recently used out) so a long-lived backend
+#: that sees many sessions keeps a flat memory.
+_PROVIDER_CACHE_MAX = 4096
+_provider_cache: OrderedDict[str, str] = OrderedDict()
+_provider_cache_lock = threading.Lock()
+
+
+def clear_provider_cache() -> None:
+    """Forget every cached provider (tests)."""
+    with _provider_cache_lock:
+        _provider_cache.clear()
+
+
+def _provider_value(raw) -> str | None:
+    """A ``Provider`` member or a string as a non-empty string cut to the column width, else ``None``."""
+    value = getattr(raw, "value", raw)
+    if not isinstance(value, str) or not value:
+        return None
+    return value[:PROVIDER_MAX_LENGTH]
+
+
+def _remember_provider(session_id: str, provider: str) -> None:
+    with _provider_cache_lock:
+        _provider_cache[session_id] = provider
+        _provider_cache.move_to_end(session_id)
+        while len(_provider_cache) > _PROVIDER_CACHE_MAX:
+            _provider_cache.popitem(last=False)
+
+
+def resolve_provider(session_id: str | None, *, explicit=None, data: dict | None = None) -> str | None:
+    """The ``provider`` column of an event (display design, section 8). Never raises.
+
+    Order: an explicit value from the caller (free sources are preferred: the agent's provider, the
+    loaded session, ``ProcessRun.provider``); then ``data["provider"]`` when the payload carries one
+    (``session.created``, ``process.started``, and ``process.ended`` through ``STARTED_OF``); then the
+    bounded cache; then ONE primary-key query on ``Session``. An instance event (no ``session_id``) and
+    a session with no row and no hint give ``None``.
+    """
+    if session_id is None:
+        return None
+    try:
+        for hint in (explicit, (data or {}).get("provider")):
+            value = _provider_value(hint)
+            if value is not None:
+                _remember_provider(session_id, value)
+                return value
+        with _provider_cache_lock:
+            cached = _provider_cache.get(session_id)
+            if cached is not None:
+                _provider_cache.move_to_end(session_id)
+                return cached
+        value = _provider_value(Session.objects.filter(id=session_id).values_list("provider", flat=True).first())
+        if value is not None:
+            _remember_provider(session_id, value)
+        return value
+    except Exception:
+        logger.exception("Timeline provider could not be resolved for session %s", session_id)
+        return None
+
+
 def record_event_sync(
     kind: str,
     *,
     session_id: str | None = None,
@@ -60,8 +125,9 @@ def record_event_sync(
     occurred_at=None,
     precision: str = TimelinePrecision.EXACT,
     actor: str | None = None,
     origin: RequestOrigin | None = None,
+    provider=None,
     reason: str = "",
     caused_by: TimelineEvent | int | None = None,
     data: dict | None = None,
     dedupe_key: str | None = None,
@@ -77,33 +143,39 @@ def record_event_sync(
     transaction, a failed insert rolls back only this row. For an ``ended`` kind
     listed in ``STARTED_OF``, the payload fields the caller did not provide are
     copied from the open ``started`` row of the same session (design §3);
     the origin fields are never copied.
+
+    ``provider`` is an optional hint (a free source the caller already holds); the ``provider`` column is
+    resolved by :func:`resolve_provider` whatever the caller passes.
     """
     try:
         resolved = resolve_event_origin(actor, origin)
         if kind in STARTED_OF:
             data = inherit_started_fields(kind, data, _open_started_data(session_id, kind))
         payload = validate_data(kind, data)
         caused_by_id = caused_by.pk if isinstance(caused_by, TimelineEvent) else caused_by
+        resolved_provider = resolve_provider(session_id, explicit=provider, data=payload)
         with transaction.atomic():
             if (
                 dedupe_key is not None
                 and TimelineEvent.objects.filter(session_id=session_id, dedupe_key=dedupe_key).exists()
             ):
                 return None
-            return TimelineEvent.objects.create(
+            event = TimelineEvent.objects.create(
                 session_id=session_id,
                 project_id=project_id,
                 kind=kind,
                 occurred_at=occurred_at or timezone.now(),
                 precision=precision,
+                provider=resolved_provider,
                 reason=reason,
                 caused_by_id=caused_by_id,
                 data=payload,
                 dedupe_key=dedupe_key,
                 **origin_columns(resolved),
             )
+        return event
     except IntegrityError:
         if dedupe_key is not None:
             logger.debug("Timeline event %s deduplicated by the database for %s", kind, session_id)
         else:
@@ -117,9 +189,10 @@ def record_event_sync(
 class EventSpec(NamedTuple):
     """One event of a batch given to :func:`record_events_bulk_sync` (same fields as ``record_event_sync``).
 
     ``actor`` defaults to ``system`` (NULL origin fields). A request-caused batch passes ``origin=``
-    (resolved once by the caller, so every row carries the same origin).
+    (resolved once by the caller, so every row carries the same origin). ``provider`` is the optional
+    hint of :func:`resolve_provider` (the bulk archive passes the provider it already read).
     """
 
     kind: str
     session_id: str | None = None
@@ -131,8 +204,9 @@ class EventSpec(NamedTuple):
     caused_by_id: int | None = None
     data: dict | None = None
     dedupe_key: str | None = None
     origin: RequestOrigin | None = None
+    provider: str | None = None
 
 
 def _conflict_clause() -> str:
     """``ON CONFLICT ... DO NOTHING`` matching the partial unique constraint on ``(session_id, dedupe_key)``.
@@ -206,12 +280,14 @@ def record_events_bulk_sync(specs: list[EventSpec]) -> int:
                 resolved = resolve_event_origin(spec.actor, spec.origin)
                 data = spec.data
                 if spec.kind in STARTED_OF:
                     data = inherit_started_fields(spec.kind, data, _open_started_data(spec.session_id, spec.kind))
+                payload = validate_data(spec.kind, data)
                 built.append((spec, TimelineEvent(
                     session_id=spec.session_id, project_id=spec.project_id, kind=spec.kind,
                     occurred_at=spec.occurred_at or now, precision=spec.precision,
-                    reason=spec.reason, caused_by_id=spec.caused_by_id, data=validate_data(spec.kind, data),
+                    provider=resolve_provider(spec.session_id, explicit=spec.provider, data=payload),
+                    reason=spec.reason, caused_by_id=spec.caused_by_id, data=payload,
                     dedupe_key=spec.dedupe_key, **origin_columns(resolved),
                 ), resolved))
             except Exception:
                 logger.exception("Timeline event %s could not be built for session %s", spec.kind, spec.session_id)
@@ -223,13 +299,14 @@ def record_events_bulk_sync(specs: list[EventSpec]) -> int:
                 return insert_event_rows(rows)
         except Exception:
             logger.exception("Timeline batch insert failed (%s rows); retrying row by row", len(rows))
             return sum(
-                1 for spec, _, resolved in built
+                1 for spec, row, resolved in built
                 if record_event_sync(
                     spec.kind, session_id=spec.session_id, project_id=spec.project_id,
                     occurred_at=spec.occurred_at or now, precision=spec.precision, origin=resolved,
-                    reason=spec.reason, caused_by=spec.caused_by_id, data=spec.data, dedupe_key=spec.dedupe_key,
+                    provider=row.provider, reason=spec.reason, caused_by=spec.caused_by_id, data=spec.data,
+                    dedupe_key=spec.dedupe_key,
                 ) is not None
             )
     except Exception:
         logger.exception("Timeline batch of %s events could not be written", len(specs))
@@ -309,9 +386,12 @@ def record_session_created(
     from twicc.providers.helpers import AgentSettings
 
     try:
         now = timezone.now()
-        common = {"session_id": session.id, "project_id": session.project_id, "occurred_at": now}
+        common = {
+            "session_id": session.id, "project_id": session.project_id, "occurred_at": now,
+            "provider": session.provider,
+        }
         who = {"origin": creator or DEFAULT_ORIGIN} if origin == "twicc" else {"actor": TimelineActor.SYSTEM}
         settings = None
         if origin == "twicc":
             settings = {
@@ -349,14 +429,14 @@ def record_title_change(
     Subagent sessions get no event (decision D2): the compute fills titles for
     every session it processes, subagents included.
     """
     try:
-        row = Session.objects.filter(id=session_id).values_list("project_id", "type").first()
+        row = Session.objects.filter(id=session_id).values_list("project_id", "type", "provider").first()
         if row is None or row[1] == SessionType.SUBAGENT:
             return
         who = {"origin": origin} if source == "user" else {"actor": TimelineActor.AGENT}
         record_event_sync(
-            TimelineKind.TITLE_CHANGED, session_id=session_id, project_id=row[0],
+            TimelineKind.TITLE_CHANGED, session_id=session_id, project_id=row[0], provider=row[2],
             data={"source": source, "new": title, "old": old}, **who,
         )
     except Exception:
         logger.exception("Timeline title change could not be written for session %s", session_id)
```


The agent hooks pass `agent.provider` (a free source, and the only one for a session whose row does not exist yet):

`src/twicc/agent/timeline_hooks.py` (modify):

```diff
--- a/src/twicc/agent/timeline_hooks.py
+++ b/src/twicc/agent/timeline_hooks.py
@@ -62,8 +62,14 @@ def snapshot_of(process_run) -> ProcessSnapshot | None:
 def _enabled(agent) -> bool:
     return not getattr(agent, "ephemeral", False)
 
 
+def _provider_of(agent) -> str | None:
+    """The agent's provider as the ``provider`` hint of the recording service (a free source)."""
+    provider = getattr(agent, "provider", None)
+    return getattr(provider, "value", provider)
+
+
 def _first_pending(agent):
     pending = tuple(getattr(agent, "pending_requests", None) or ())
     return pending[0] if pending else None
 
@@ -71,9 +77,9 @@ def _first_pending(agent):
 def _write(agent, specs, *, occurred_at, actor=TimelineActor.AGENT, origin=None) -> None:
     for spec in specs:
         record_event_sync(
             spec.kind, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
-            occurred_at=occurred_at, actor=actor, origin=origin, data=spec.data,
+            occurred_at=occurred_at, actor=actor, origin=origin, provider=_provider_of(agent), data=spec.data,
         )
 
 
 @_never_raises
@@ -87,8 +93,9 @@ def record_process_started(agent, *, resume: bool) -> None:
         return
     run = agent.process_run
     record_event_sync(
         TimelineKind.PROCESS_STARTED, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
+        provider=_provider_of(agent),
         data={"provider": agent.provider.value, "cause": "resume" if resume else "new",
               "twicc_pid": run.twicc_pid if run is not None else None,
               # ``HybridClaudeAgent.is_hybrid`` is a class attribute; other agents lack it.
               "hybrid": bool(getattr(agent, "is_hybrid", False))},
@@ -143,7 +150,7 @@ def record_process_ended(agent, prev: ProcessSnapshot | None) -> None:
     error = getattr(agent, "error", None)
     caused_by = latest_event_id(agent.session_id, TimelineKind.ARCHIVED_STARTED) if reason == "archived" else None
     record_event_sync(
         TimelineKind.PROCESS_ENDED, session_id=agent.session_id, project_id=getattr(agent, "project_id", None),
-        occurred_at=now, reason=reason, caused_by=caused_by, origin=ended_origin,
+        occurred_at=now, reason=reason, caused_by=caused_by, origin=ended_origin, provider=_provider_of(agent),
         data={"error": str(error)[:_ERROR_TEXT_LIMIT]} if error else None,
     )
```


The boot reconciliation uses `ProcessRun.provider`, which exists even when the Session row does not:

`src/twicc/core/services/timeline_boot.py` (modify):

```diff
--- a/src/twicc/core/services/timeline_boot.py
+++ b/src/twicc/core/services/timeline_boot.py
@@ -60,9 +60,9 @@ def _reconcile_lost_processes(now: datetime) -> None:
         session = Session.objects.filter(id=run.session_id).first()
         common = {
             "session_id": run.session_id, "project_id": session.project_id if session else None,
             "occurred_at": _last_evidence(run, session, now),
-            "precision": TimelinePrecision.ESTIMATED, "actor": TimelineActor.SYSTEM,
+            "precision": TimelinePrecision.ESTIMATED, "actor": TimelineActor.SYSTEM, "provider": run.provider,
         }
         for spec in process_transition_events(
             prev_state=run.state, prev_awaiting=run.awaiting_user_input,
             prev_work=run.background_work_in_progress,
```


The backfill rows carry the loaded session's provider (this path never goes through the resolver, it inserts model objects directly):

`src/twicc/core/services/timeline_backfill.py` (modify):

```diff
--- a/src/twicc/core/services/timeline_backfill.py
+++ b/src/twicc/core/services/timeline_backfill.py
@@ -79,9 +79,9 @@ def candidate_ids() -> list[str]:
 
 def _row(session: Session, kind: str, occurred_at, precision: str, data: dict | None) -> TimelineEvent:
     return TimelineEvent(
         session_id=session.id, project_id=session.project_id, kind=kind, occurred_at=occurred_at,
-        precision=precision, actor=TimelineActor.SYSTEM, data=validate_data(kind, data),
+        precision=precision, actor=TimelineActor.SYSTEM, provider=session.provider, data=validate_data(kind, data),
         dedupe_key=f"{BACKFILL_PREFIX}{kind}",
     )
 
 
```


The flag writers and the bulk archive pass the provider they already hold. The bulk archive reads it from its own `UPDATE ... RETURNING`, so the batch needs no per-session query:

`src/twicc/core/services/session_update.py` (modify):

```diff
--- a/src/twicc/core/services/session_update.py
+++ b/src/twicc/core/services/session_update.py
@@ -163,9 +163,9 @@ def archive_sessions_bulk(ids: set[str]) -> list[str]:
     here. The write is two statements plus ONE batched timeline insert, whatever ``len(ids)``.
 
     Race analysis. ``ids`` was computed outside the lock, so another writer may have archived,
     unarchived or deleted any of them since. The flip is a conditional
-    ``UPDATE ... WHERE id IN (ids) AND archived = 0 ... RETURNING id, project_id``: the database
+    ``UPDATE ... WHERE id IN (ids) AND archived = 0 ... RETURNING id, project_id, provider``: the database
     decides atomically which rows flip, and the events are built from the rows it returns.
     A session archived meanwhile is not returned (no event: it did not flip here); one unarchived
     meanwhile is returned (flipped here, so an event is recorded). A deleted row is neither.
     The set is passed as one JSON parameter (``json_each``), so there is no variable limit.
@@ -185,9 +185,9 @@ def archive_sessions_bulk(ids: set[str]) -> list[str]:
     with transaction.atomic():
         with connection.cursor() as cursor:
             cursor.execute(
                 f"UPDATE {table} SET archived = 1, search_version = 0 "
-                f"WHERE id IN (SELECT value FROM json_each(%s)) AND archived = 0 RETURNING id, project_id",
+                f"WHERE id IN (SELECT value FROM json_each(%s)) AND archived = 0 RETURNING id, project_id, provider",
                 [ids_json],
             )
             flipped = cursor.fetchall()
             cursor.execute(
@@ -195,12 +195,15 @@ def archive_sessions_bulk(ids: set[str]) -> list[str]:
                 f"WHERE id IN (SELECT value FROM json_each(%s)) AND archived = 1 AND search_version IS NOT 0",
                 [ids_json],
             )
         record_events_bulk_sync([
-            EventSpec(TimelineKind.ARCHIVED_STARTED, session_id=session_id, project_id=project_id, origin=origin)
-            for session_id, project_id in flipped
+            EventSpec(
+                TimelineKind.ARCHIVED_STARTED, session_id=session_id, project_id=project_id, origin=origin,
+                provider=provider,
+            )
+            for session_id, project_id, provider in flipped
         ])
-    return [session_id for session_id, _ in flipped]
+    return [session_id for session_id, _, _ in flipped]
 
 
 def bundle_fields(updates: dict) -> list[str]:
     """Names in ``updates`` that belong to the ``AgentSettings`` bundle."""
@@ -547,16 +550,16 @@ async def apply_session_archived_change(
         if archived != was_archived:
             archive_event = await asyncio.to_thread(
                 record_event_sync,
                 TimelineKind.ARCHIVED_STARTED if archived else TimelineKind.ARCHIVED_ENDED,
-                session_id=session.id, project_id=session.project_id,
+                session_id=session.id, project_id=session.project_id, provider=session.provider,
             )
         if also_unpin and was_pinned is not None:
             # ``also_unpin`` clears ``pinned`` itself: the pin service is not called.
             # The ``scope`` of the row is inherited from ``pinned.started`` by the service.
             await asyncio.to_thread(
                 record_event_sync, TimelineKind.PINNED_ENDED,
-                session_id=session.id, project_id=session.project_id,
+                session_id=session.id, project_id=session.project_id, provider=session.provider,
                 caused_by=archive_event,
             )
 
     await run_under_db_write_lock(_save_and_record)
@@ -726,9 +729,9 @@ async def apply_session_pinned_change(session, pinned) -> None:
         # written first, so its inherited ``scope`` is the OLD one (the latest
         # ``pinned.started`` is still the old pin); the new ``started`` row is
         # ``caused_by`` it.
         common = {
-            "session_id": session.id, "project_id": session.project_id,
+            "session_id": session.id, "project_id": session.project_id, "provider": session.provider,
             "occurred_at": timezone.now(),
         }
         ended = None
         if was_pinned is not None:
@@ -756,9 +759,9 @@ async def apply_session_mute_on_user_turn_change(session, mute_on_user_turn: boo
         if mute_on_user_turn != was_muted:
             await asyncio.to_thread(
                 record_event_sync,
                 TimelineKind.MUTED_STARTED if mute_on_user_turn else TimelineKind.MUTED_ENDED,
-                session_id=session.id, project_id=session.project_id,
+                session_id=session.id, project_id=session.project_id, provider=session.provider,
             )
 
     await run_under_db_write_lock(_save_and_record)
 
```


`src/twicc/core/services/session_visibility.py` (modify):

```diff
--- a/src/twicc/core/services/session_visibility.py
+++ b/src/twicc/core/services/session_visibility.py
@@ -131,9 +131,9 @@ def _apply_visibility_flag(session_id: str, new_hidden: bool):
             after_sessions=[session_contribution(session)], repair=True)
         if was_hidden != new_hidden:
             record_event_sync(
                 TimelineKind.HIDDEN_STARTED if new_hidden else TimelineKind.HIDDEN_ENDED,
-                session_id=session.id, project_id=session.project_id,
+                session_id=session.id, project_id=session.project_id, provider=session.provider,
             )
         session.refresh_from_db()
     return session
 
```


- [ ] **Step 5: Run the tests and see them pass**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_provider.py -q
```

Expected: `32 passed`. Then the whole timeline family, to see that no earlier test depended on the old column set (the benchmark tests are slow and excluded):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest $(ls tests/test_timeline_*.py | grep -v bench) tests/test_title_automation.py -q
```

Expected: all pass (536 in the scratch run).

- [ ] **Step 6: Lint and commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uvx ruff check src/twicc/core/models.py src/twicc/core/services/session_timeline.py src/twicc/agent/timeline_hooks.py src/twicc/core/services/timeline_boot.py src/twicc/core/services/timeline_backfill.py src/twicc/core/services/session_update.py src/twicc/core/services/session_visibility.py tests/test_timeline_provider.py tests/conftest.py
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/core/models.py src/twicc/core/migrations/0159_timeline_event_provider.py src/twicc/core/services/session_timeline.py src/twicc/agent/timeline_hooks.py src/twicc/core/services/timeline_boot.py src/twicc/core/services/timeline_backfill.py src/twicc/core/services/session_update.py src/twicc/core/services/session_visibility.py tests/test_timeline_provider.py tests/conftest.py
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
feat(timeline): record the provider of every event

Add the nullable TimelineEvent.provider column with the two indexes the display feed
needs: (occurred_at, id) for the global feed and (provider, occurred_at, id) for the
provider filter (migration 0159, columns and indexes only, no data migration).

The recording service resolves the column in one place, resolve_provider: an explicit
value from the caller, then the payload, then a bounded cache, then one primary-key
query. A session with no row and no hint gets NULL and recording never raises. Writers
that already hold the provider pass it (agent hooks, boot reconciliation through
ProcessRun, session.created, titles, flag hooks, the bulk archive through its
RETURNING clause, the backfill). A guard test fails on a NULL provider with an
existing session after any writer ran.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 2: The feed endpoint `GET /api/timeline/`

**Files:**
- Create: `src/twicc/core/services/timeline_feed.py`
- Modify: `src/twicc/views.py` (the `timeline` view, one import), `src/twicc/urls.py` (one route)
- Create: `tests/test_timeline_api.py`

**Interfaces:**
- Consumes (T1): `TimelineEvent.provider`, `idx_timeline_occurred`, `idx_timeline_provider`.
- Produces: `GET /api/timeline/` with the contract of the Global Constraints. In `twicc.core.services.timeline_feed`: `DEFAULT_LIMIT = 50`, `MAX_LIMIT = 100`, `AFTER_ID_CAP = 500`, `_MAX_INTEGER_TEXT = 24` (a longer integer text is a 400 before `int()` sees it), `MAX_EVENT_ID = 2**63 - 1` (a larger `before` id or `after_id` is a 400: SQLite cannot bind it), `SESSION_LOOKUP_CHUNK = 500`; `FeedError(ValueError)`; `FeedRequest(project_ids: tuple[str, ...], provider: str | None, limit: int, before: tuple[datetime, int] | None, after_id: int | None)`; `format_occurred_at(value) -> str`; `parse_cursor(text) -> tuple[datetime, int]`; `parse_request(params) -> FeedRequest`; `build_sessions_table(session_ids: set[str]) -> dict[str, dict]`; `serialize_event(event) -> dict`; `latest_event_id() -> int | None`; `fetch_feed(request: FeedRequest) -> dict`. Private but pinned by tests: `_page_querysets(request)`, `_after_scope(request)`, `_fetch_page`, `_fetch_after`.
- Used by T6/T8 (frontend): the response shape; the `sessions` entry fields `title`, `project_id`, `provider`, `hidden`, `archived`.

- [ ] **Step 1: Write the failing tests**

The tests cover: ordering and id tie-break (also for a multi-project scope, where only the SQL `id DESC` orders equal timestamps), the exact public event shape, paging over equal timestamps (whole seconds and equal milliseconds; every paging loop is BOUNDED, so a regressed cursor fails the test instead of hanging the suite), cursor forms, scope and `project_ids` parsing, the four instance-event cases (once each, merged across a page boundary, no duplicate), provider filtering, the documented limits (50, 100, 500) and every 400 (including an id beyond 64 bits, a 5000-digit `limit`, `after_id` and `before` id which would make `int()` raise, and a timestamp whose UTC conversion overflows), the 100 cap, 405 on every other method, the auth path (the middleware path set AND a real 401 once an instance password is set, 200 again without it), 500, no writes (the table is unchanged after the view ran, and the service issues no write statement), `after_id` (ascending, back-dated, scope, truncation at 500 on the merged set, empty feed), `latest_id` read first, the side table (all three citation paths, pending title, an empty title read as untitled, missing id, chunking), and `EXPLAIN QUERY PLAN` of the three query shapes (the `after_id` plan is read from the real `_after_queryset` that `_fetch_after` uses).

`tests/test_timeline_api.py` (new file):

```python
# tests/test_timeline_api.py
"""``GET /api/timeline/`` (display design 2026-10-15, sections 7 and 14)."""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test import AsyncClient
from django.test.utils import CaptureQueriesContext

from twicc.auth.middleware import _is_data_path
from twicc.core.models import Project, Session, TimelineEvent
from twicc.core.services import timeline_feed
from twicc.core.services.timeline_feed import AFTER_ID_CAP, FeedError, parse_request
from twicc.pending_titles import pop_pending_title, set_pending_title

pytestmark = pytest.mark.django_db(transaction=True)

T0 = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def call(client=None, **params):
    """GET the endpoint; returns the response (``.json()`` for the body)."""
    return asyncio.run((client or AsyncClient()).get("/api/timeline/", params))


@pytest.fixture
def projects():
    return [Project.objects.create(id=f"-tl-api-{name}", directory=f"/tmp/tl-api-{name}") for name in "abc"]


def make_event(kind="turn.started", *, project=None, session_id="s1", at=0, provider="codex", **fields):
    """One event ``at`` seconds after T0 (a float keeps microseconds)."""
    fields.setdefault("actor", "agent")
    return TimelineEvent.objects.create(
        kind=kind, project=project, session_id=session_id, occurred_at=T0 + timedelta(seconds=at),
        provider=provider, **fields,
    )


def make_instance_event(at=0, kind="backend.started", **fields):
    return make_event(
        kind, project=None, session_id=None, at=at, provider=None, actor="system", data={"pid": 1}, **fields,
    )


def ids(body):
    return [event["id"] for event in body["events"]]


# -- Ordering, paging, cursor -----------------------------------------------------------------------------


def test_events_come_newest_first_and_the_id_breaks_ties():
    old = make_event(at=0)
    tie_a = make_event(at=5)
    tie_b = make_event(at=5)
    new = make_event(at=9)
    body = call().json()
    assert ids(body) == [new.id, tie_b.id, tie_a.id, old.id]


def test_the_event_shape_is_exactly_the_public_one():
    make_event(
        "title.changed", at=1.5, session_id="s1", actor="agent", via="mcp_internal", actor_ref="boss",
        actor_name=None, reason="r", precision="derived", data={"source": "agent", "new": "N", "old": None},
    )
    event = call().json()["events"][0]
    assert set(event) == {
        "id", "kind", "occurred_at", "precision", "actor", "via", "actor_ref", "actor_name", "reason", "data",
        "session_id", "project_id", "provider",
    }
    assert TIMESTAMP.match(event["occurred_at"]) and event["occurred_at"] == "2026-10-14T10:00:01.500000Z"
    assert event["precision"] == "derived" and event["provider"] == "codex" and event["data"]["new"] == "N"


def test_paging_by_before_returns_every_row_exactly_once_with_many_equal_timestamps():
    created = [make_event(at=0) for _ in range(7)] + [make_event(at=3) for _ in range(5)]
    seen, cursor, pages = [], None, 0
    # Bounded: a regression of the cursor would repeat the same rows forever and hang the suite.
    for _ in range(6):
        params = {"limit": 3}
        if cursor:
            params["before"] = cursor
        body = call(**params).json()
        seen += ids(body)
        pages += 1
        if not body["has_more"]:
            break
        last = body["events"][-1]
        cursor = f"{last['occurred_at']},{last['id']}"
    else:
        pytest.fail(f"the paging never reached the end: {seen}")
    expected = sorted((event.id for event in created), reverse=True)
    expected = [i for i in expected if i > created[6].id] + [i for i in expected if i <= created[6].id]
    assert seen == expected and len(set(seen)) == 12 and pages == 4


def test_paging_over_equal_timestamps_in_a_multi_project_scope(projects):
    # With several project ids the planner sorts in a temp b-tree: only the SQL ``id DESC`` tie-break keeps
    # the pages consistent, the index order does not.
    a, b, _ = projects
    rows = [make_event(project=(a if i % 2 else b), at=0) for i in range(8)]
    seen, cursor = [], None
    for _ in range(6):
        params = {"limit": 3, "project_ids": f"{a.id},{b.id}"}
        if cursor:
            params["before"] = cursor
        body = call(**params).json()
        seen += ids(body)
        if not body["has_more"]:
            break
        last = body["events"][-1]
        cursor = f"{last['occurred_at']},{last['id']}"
    else:
        pytest.fail(f"never reached the end: {seen}")
    assert seen == sorted((row.id for row in rows), reverse=True)


def test_the_cursor_round_trips_microseconds_for_rows_equal_to_the_millisecond():
    rows = [make_event(at=0.000100), make_event(at=0.000400), make_event(at=0.000900)]
    first = call(limit=1).json()
    assert first["events"][0]["occurred_at"] == "2026-10-14T10:00:00.000900Z"
    assert first["has_more"] is True
    cursor = f"{first['events'][-1]['occurred_at']},{first['events'][-1]['id']}"
    second = call(limit=1, before=cursor).json()
    cursor = f"{second['events'][-1]['occurred_at']},{second['events'][-1]['id']}"
    third = call(limit=1, before=cursor).json()
    assert ids(first) + ids(second) + ids(third) == [rows[2].id, rows[1].id, rows[0].id]
    assert third["has_more"] is False


def test_a_cursor_with_an_offset_instead_of_z_is_accepted():
    row = make_event(at=1)
    make_event(at=0)
    body = call(before=f"2026-10-14T12:00:01+02:00,{row.id + 1}").json()
    assert row.id in ids(body)


def test_has_more_uses_one_extra_row():
    for index in range(4):
        make_event(at=index)
    assert call(limit=3).json()["has_more"] is True
    assert call(limit=4).json()["has_more"] is False


# -- Scope --------------------------------------------------------------------------------------------------


def test_global_includes_null_project_rows_and_a_project_scope_excludes_them(projects):
    a, b, _ = projects
    in_a = make_event(project=a, at=1)
    orphan = make_event(project=None, session_id="lost", at=2)
    in_b = make_event(project=b, at=3)
    assert set(ids(call().json())) == {in_a.id, orphan.id, in_b.id}
    assert ids(call(project_ids=a.id).json()) == [in_a.id]


def test_a_project_with_worktrees_returns_both_when_the_ids_are_sent(projects):
    a, b, c = projects
    main, worktree, other = make_event(project=a, at=1), make_event(project=b, at=2), make_event(project=c, at=3)
    body = call(project_ids=f"{a.id},{b.id}").json()
    assert ids(body) == [worktree.id, main.id] and other.id not in ids(body)


def test_project_ids_drop_empty_items_and_an_empty_value_means_global(projects):
    a = projects[0]
    row = make_event(project=a, at=1)
    other = make_event(project=None, session_id="lost", at=2)
    assert ids(call(project_ids=f" {a.id} ,, ,").json()) == [row.id]
    assert set(ids(call(project_ids="").json())) == {row.id, other.id}
    assert set(ids(call(project_ids=",,").json())) == {row.id, other.id}


# -- Instance events: the four cases of the 7.4 table ------------------------------------------------------


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("provider", [None, "codex"])
def test_an_instance_event_appears_exactly_once_in_each_of_the_four_cases(projects, scoped, provider):
    a = projects[0]
    row = make_event(project=a, at=1)
    boot = make_instance_event(at=2)
    other_provider = make_event(project=a, at=3, provider="claude_code")
    params = {}
    if scoped:
        params["project_ids"] = a.id
    if provider:
        params["provider"] = provider
    body = call(**params).json()
    expected = [boot.id, row.id] if provider else [other_provider.id, boot.id, row.id]
    assert ids(body) == expected
    assert ids(body).count(boot.id) == 1


@pytest.mark.parametrize("scoped", [False, True])
def test_an_instance_event_is_merged_at_its_time_position_across_a_page_boundary(projects, scoped):
    a = projects[0]
    rows = [make_event(project=a, at=index * 10) for index in range(4)]  # at 0, 10, 20, 30
    boot = make_instance_event(at=15)
    params = {"limit": 2, "provider": "codex"}
    if scoped:
        params["project_ids"] = a.id
    first = call(**params).json()
    assert ids(first) == [rows[3].id, rows[2].id] and first["has_more"] is True
    last = first["events"][-1]
    second = call(before=f"{last['occurred_at']},{last['id']}", **params).json()
    assert ids(second) == [boot.id, rows[1].id] and second["has_more"] is True
    last = second["events"][-1]
    third = call(before=f"{last['occurred_at']},{last['id']}", **params).json()
    assert ids(third) == [rows[0].id] and third["has_more"] is False


def test_an_instance_event_and_a_session_event_with_the_same_time_are_ordered_by_id(projects):
    a = projects[0]
    first = make_event(project=a, at=5)
    boot = make_instance_event(at=5)
    assert ids(call(project_ids=a.id).json()) == [boot.id, first.id]


def test_a_row_that_both_queries_return_is_listed_once(projects):
    a = projects[0]
    both = make_event(project=a, session_id=None, at=1, actor="system", data={"pid": 1})
    body = call(project_ids=a.id, provider="codex").json()
    assert ids(body) == [both.id]


@pytest.mark.parametrize("params,queries", [
    ({}, 1),                                          # global, no provider: the main query already holds them
    ({"provider": "codex"}, 2),                       # global with a provider: a second query
    ({"project_ids": "-tl-api-a"}, 2),                # scoped
    ({"project_ids": "-tl-api-a", "provider": "codex"}, 2),
])
def test_the_instance_events_query_runs_in_every_case_but_the_global_one_without_provider(params, queries):
    assert len(timeline_feed._page_querysets(parse_request(params))) == queries


# -- Provider, parameters, errors ---------------------------------------------------------------------------


def test_the_provider_filter_keeps_only_that_provider_and_instance_events():
    codex = make_event(at=1, provider="codex")
    make_event(at=2, provider="claude_code")
    boot = make_instance_event(at=3)
    assert ids(call(provider="codex").json()) == [boot.id, codex.id]


@pytest.mark.parametrize("params", [
    {"provider": "nope"},
    {"before": "garbage"},
    {"before": "2026-10-14T10:00:00Z"},
    {"before": "2026-10-14T10:00:00Z,x"},
    {"before": "2026-10-14T10:00:00,5"},
    {"before": "2026-10-14T10:00:00 00:00,5"},
    {"after_id": "x"},
    {"after_id": "1.5"},
    {"after_id": "-1"},
    {"limit": "0"},
    {"limit": "-3"},
    {"limit": "x"},
    {"limit": "1.5"},
    {"limit": ""},
    {"limit": "1_0"},
    {"before": "2026-10-14T10:00:00Z,5", "after_id": "3"},
    {"after_id": "5", "limit": "0"},
    {"before": "2026-10-14T10:00:00Z,99999999999999999999"},   # beyond a 64-bit integer: SQLite cannot bind it
    {"before": "2026-10-14T10:00:00Z,-1"},
    {"before": "0001-01-01T00:00:00+05:00,1"},                  # the conversion to UTC overflows
    {"after_id": "99999999999999999999"},
    {"limit": "9" * 5000},                                      # beyond Python's int-from-string limit
    {"after_id": "9" * 5000},
    {"before": "2026-10-14T10:00:00Z," + "9" * 5000},
])
def test_bad_parameters_answer_400_with_an_error_body(params):
    response = call(**params)
    assert response.status_code == 400
    assert isinstance(response.json()["error"], str) and response.json()["error"]


def test_a_limit_over_100_is_capped():
    for index in range(105):
        make_event(at=index)
    body = call(limit=1000).json()
    assert len(body["events"]) == 100 and body["has_more"] is True
    assert len(call().json()["events"]) == 50


def test_the_documented_limits():
    assert (timeline_feed.DEFAULT_LIMIT, timeline_feed.MAX_LIMIT, AFTER_ID_CAP) == (50, 100, 500)


def test_parse_request_defaults_and_normalisation():
    request = parse_request({"project_ids": "a,b,a", "provider": "codex"})
    assert request == timeline_feed.FeedRequest(("a", "b"), "codex", 50, None, None)
    assert parse_request({"limit": " 7 "}).limit == 7
    with pytest.raises(FeedError):
        parse_request({"limit": "0"})


def test_the_endpoint_only_answers_get():
    response = asyncio.run(AsyncClient().post("/api/timeline/"))
    assert response.status_code == 405


def test_the_endpoint_is_behind_the_instance_authentication(settings):
    assert _is_data_path(SimpleNamespace(path="/api/timeline/")) is True
    make_event(at=1)
    settings.TWICC_PASSWORD_HASH = "pbkdf2_sha256$1$salt$hash"  # a fresh client builds the middleware chain again
    assert call().status_code == 401
    assert call(after_id=0).status_code == 401
    settings.TWICC_PASSWORD_HASH = ""
    assert call().status_code == 200


def test_a_database_error_answers_500(monkeypatch):
    def boom(request):
        raise RuntimeError("database down")

    monkeypatch.setattr(timeline_feed, "fetch_feed", boom)
    response = asyncio.run(AsyncClient(raise_request_exception=False).get("/api/timeline/"))
    assert response.status_code == 500


def test_the_view_never_writes_the_table_and_refuses_other_methods(projects):
    make_event(project=projects[0], at=1)
    before = list(TimelineEvent.objects.values_list("id", "kind", "occurred_at", "data"))
    assert call().status_code == 200 and call(after_id=0).status_code == 200
    client = AsyncClient()
    for method in ("post", "put", "patch", "delete"):
        assert asyncio.run(getattr(client, method)("/api/timeline/")).status_code == 405
    assert list(TimelineEvent.objects.values_list("id", "kind", "occurred_at", "data")) == before


def test_the_feed_service_issues_no_write_statement(projects):
    make_event(project=projects[0], at=1)
    # The view runs in another thread: read the statements of the service it calls, in this one.
    with CaptureQueriesContext(connection) as captured:
        for params in ({}, {"after_id": "0"}, {"project_ids": projects[0].id, "provider": "codex"}):
            timeline_feed.fetch_feed(parse_request(params))
    assert captured.captured_queries
    writes = [q["sql"] for q in captured.captured_queries if q["sql"].lstrip().split()[0].upper() in
              {"INSERT", "UPDATE", "DELETE", "REPLACE"}]
    assert writes == []


# -- after_id ------------------------------------------------------------------------------------------------


def test_after_id_returns_only_newer_ids_in_ascending_order_even_when_back_dated(projects):
    a = projects[0]
    first = make_event(project=a, at=10)
    back_dated = make_event(project=a, at=1)
    newest = make_event(project=a, at=20)
    body = call(after_id=first.id).json()
    assert ids(body) == [back_dated.id, newest.id] and body["truncated"] is False
    assert call(after_id=newest.id).json()["events"] == []


@pytest.mark.parametrize("scoped,provider,expected", [
    (False, None, ["a-codex", "b-claude", "boot"]),
    (False, "codex", ["a-codex", "boot"]),
    (True, None, ["a-codex", "boot"]),
    (True, "claude_code", ["boot"]),
])
def test_after_id_applies_the_scope_and_provider_and_includes_instance_events(projects, scoped, provider, expected):
    a, b, _ = projects
    names = {}
    names["a-codex"] = make_event(project=a, at=1, provider="codex").id
    names["b-claude"] = make_event(project=b, at=2, provider="claude_code").id
    names["boot"] = make_instance_event(at=3).id
    params = {"after_id": 0}
    if scoped:
        params["project_ids"] = a.id
    if provider:
        params["provider"] = provider
    assert ids(call(**params).json()) == [names[name] for name in expected]


def test_after_id_is_truncated_past_the_cap_and_keeps_the_first_rows(projects):
    a = projects[0]
    rows = [make_event(project=a, at=index) for index in range(AFTER_ID_CAP + 3)]
    body = call(after_id=0).json()
    assert body["truncated"] is True and ids(body) == [row.id for row in rows[:AFTER_ID_CAP]]
    exact = call(after_id=rows[2].id).json()
    assert len(exact["events"]) == AFTER_ID_CAP and exact["truncated"] is False


def test_the_cap_applies_to_the_merged_set_of_rows_and_instance_events(projects):
    a = projects[0]
    for index in range(AFTER_ID_CAP):
        make_event(project=a, at=index)
    make_instance_event(at=999)
    body = call(after_id=0, project_ids=a.id).json()
    assert body["truncated"] is True and len(body["events"]) == AFTER_ID_CAP


def test_an_empty_feed_has_a_null_latest_id_and_after_id_zero_fetches_the_first_event():
    body = call().json()
    assert body == {"events": [], "has_more": False, "truncated": False, "latest_id": None, "sessions": {}}
    assert call(after_id=0).json()["latest_id"] is None
    first = make_event(at=1)
    assert ids(call(after_id=0).json()) == [first.id]


def test_latest_id_is_the_highest_id_whatever_the_scope(projects):
    a, b, _ = projects
    make_event(project=a, at=1)
    make_event(project=b, at=2)
    last = make_instance_event(at=3)  # no project, no provider: still the highest id of the table
    assert call(project_ids=a.id).json()["latest_id"] == last.id
    assert call(provider="claude_code").json()["latest_id"] == last.id
    assert call().json()["latest_id"] == last.id


def test_latest_id_is_read_before_the_events(projects, monkeypatch):
    first = make_event(project=projects[0], at=1)
    original = timeline_feed._fetch_page
    created = []

    def racing(request):
        # A row committed between the two reads.
        created.append(make_event(project=projects[0], at=2))
        return original(request)

    monkeypatch.setattr(timeline_feed, "_fetch_page", racing)
    body = call().json()
    assert body["latest_id"] == first.id
    assert ids(body) == [created[0].id, first.id] and created[0].id > body["latest_id"]


# -- Side table -----------------------------------------------------------------------------------------------


def make_session(project, sid, **fields):
    fields.setdefault("provider", "codex")
    return Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, **fields)


def test_the_side_table_holds_every_cited_session_by_event_by_actor_ref_and_by_spawned_by(projects):
    a = projects[0]
    make_session(a, "s1", title="Mine", archived=True)
    make_session(a, "boss", title="The boss", provider="claude_code")
    make_session(a, "parent", title=None)
    make_session(a, "unrelated", title="Unrelated")
    make_event("title.changed", project=a, session_id="s1", actor="agent", actor_ref="boss", at=1)
    make_event("session.created", project=a, session_id="s1", at=2, actor="system",
               data={"origin": "twicc", "spawned_by": "parent"})
    sessions = call().json()["sessions"]
    assert set(sessions) == {"s1", "boss", "parent"}
    assert sessions["s1"] == {"title": "Mine", "project_id": a.id, "provider": "codex", "hidden": False,
                              "archived": True}
    assert sessions["boss"]["provider"] == "claude_code"
    assert sessions["parent"]["title"] is None


def test_a_user_actor_ref_is_not_looked_up_as_a_session(projects):
    make_session(projects[0], "tok_1", title="Not a token")
    make_event("settings.changed", project=projects[0], session_id="s1", actor="user", actor_ref="tok_1", at=1)
    assert "tok_1" not in call().json()["sessions"]


def test_the_side_table_applies_the_pending_title_override_and_flags(projects):
    make_session(projects[0], "s1", title="Stored", hidden=True)
    make_event(project=projects[0], at=1)
    set_pending_title("s1", "Pending")
    try:
        entry = call().json()["sessions"]["s1"]
    finally:
        pop_pending_title("s1")
    assert entry["title"] == "Pending" and entry["hidden"] is True and entry["archived"] is False


def test_an_untitled_session_has_a_null_title_in_the_side_table(projects):
    make_session(projects[0], "empty", title="")
    make_session(projects[0], "none", title=None)
    make_event(project=projects[0], session_id="empty", at=1)
    make_event(project=projects[0], session_id="none", at=2)
    sessions = call().json()["sessions"]
    assert sessions["empty"]["title"] is None and sessions["none"]["title"] is None


def test_a_session_id_with_no_row_has_no_entry(projects):
    make_event(project=projects[0], session_id="purged", at=1)
    body = call().json()
    assert ids(body) and body["sessions"] == {}


def test_the_side_table_lookup_is_chunked(projects, monkeypatch):
    monkeypatch.setattr(timeline_feed, "SESSION_LOOKUP_CHUNK", 2)
    for index in range(5):
        make_session(projects[0], f"c{index}", title=f"T{index}")
        make_event(project=projects[0], session_id=f"c{index}", at=index)
    with CaptureQueriesContext(connection) as captured:
        sessions = timeline_feed.build_sessions_table({f"c{index}" for index in range(5)})
    assert set(sessions) == {f"c{index}" for index in range(5)}
    lookups = [q["sql"] for q in captured.captured_queries if 'FROM "core_session"' in q["sql"]]
    assert len(lookups) == 3
    # No single ``IN`` carries more than the chunk: a slice that forgot its upper bound would send them all.
    assert all(len(re.search(r"IN \(([^)]*)\)", sql).group(1).split(",")) <= 2 for sql in lookups)


# -- Query plans --------------------------------------------------------------------------------------------------


def plan_of(queryset):
    sql, params = queryset.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN QUERY PLAN " + sql, params)
        return sql, " | ".join(str(row[-1]) for row in cursor.fetchall())


@pytest.fixture
def busy(projects):
    for index in range(30):
        make_event(project=projects[index % 3], at=index, provider="codex" if index % 2 else "claude_code")
    return projects


CURSOR = f"{(T0 + timedelta(seconds=15)).strftime('%Y-%m-%dT%H:%M:%S.%fZ')},12"


@pytest.mark.parametrize("params,index_name", [
    ({}, "idx_timeline_occurred"),
    ({"provider": "codex"}, "idx_timeline_provider"),
    ({"project_ids": "-tl-api-a"}, "idx_timeline_project"),
    ({"project_ids": "-tl-api-a,-tl-api-b"}, "idx_timeline_project"),
])
def test_the_page_query_uses_an_index_with_the_row_value_keyset_and_no_or(busy, params, index_name):
    request = parse_request({**params, "before": CURSOR})
    main = timeline_feed._page_querysets(request)[0]
    sql, plan = plan_of(main)
    assert index_name in plan and "MULTI-INDEX OR" not in plan
    assert "(occurred_at, id) < (" in sql and " OR " not in sql.upper()


def test_the_instance_query_uses_the_session_index(busy):
    request = parse_request({"project_ids": "-tl-api-a", "before": CURSOR})
    _, plan = plan_of(timeline_feed._page_querysets(request)[1])
    assert "idx_timeline_session" in plan and "USE TEMP B-TREE" not in plan


@pytest.mark.parametrize("params", [
    {}, {"project_ids": "-tl-api-a,-tl-api-b"}, {"provider": "codex"},
    {"project_ids": "-tl-api-a", "provider": "codex"},
])
def test_the_after_id_query_scans_a_primary_key_range_never_a_scope_index(busy, params):
    request = parse_request({**params, "after_id": "5"})
    # The real builder of ``_fetch_after``: a regression inside it shows up in the plan.
    _, plan = plan_of(timeline_feed._after_queryset(request))
    assert "INTEGER PRIMARY KEY" in plan
    assert "idx_timeline_project" not in plan and "idx_timeline_provider" not in plan
```


- [ ] **Step 2: Run the tests and see them fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_api.py -q
```

Expected: a collection error, `ImportError: cannot import name 'timeline_feed' from 'twicc.core.services'`.

- [ ] **Step 3: Write the feed service**

`src/twicc/core/services/timeline_feed.py` (new file):

```python
# src/twicc/core/services/timeline_feed.py
"""Read side of the timeline: the feed behind ``GET /api/timeline/``.

Design: ``docs/plans/2026-10-15-timeline-display-design.md`` section 7. Everything here is sync and
read-only; the view runs it in a worker thread.

Two query shapes matter for speed (measured on SQLite, no ANALYZE as in production):

* ``before`` and the first page use a ROW-VALUE keyset ``(occurred_at, id) < (cursor)``. The expanded
  ``OR`` form turns a multi-project ``IN`` into a multi-index OR plus a sort of the whole scope.
* ``after_id`` reads a primary-key range. The scope columns carry a unary plus (``+project_id``) so the
  planner cannot pick a project or provider index, which would read every event of the scope for a
  handful of new rows.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import NamedTuple

from django.db import connection
from django.db.models import BooleanField
from django.db.models.expressions import RawSQL

from twicc.core.enums import Provider
from twicc.core.models import Session, TimelineActor, TimelineEvent, TimelineKind

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
AFTER_ID_CAP = 500
#: The largest id SQLite can bind (a signed 64-bit integer): a bigger one is a 400, not a 500.
MAX_EVENT_ID = 2**63 - 1
#: Session ids per ``IN`` of the side-table lookup (an ``after_id`` page can cite more than 1000 ids).
SESSION_LOOKUP_CHUNK = 500

_INTEGER = re.compile(r"[+-]?\d+")
_MAX_INTEGER_TEXT = 24


class FeedError(ValueError):
    """A bad request parameter. The message goes into the ``{"error": ...}`` body of the 400."""


class FeedRequest(NamedTuple):
    project_ids: tuple[str, ...]  # empty = the global scope
    provider: str | None
    limit: int
    before: tuple[datetime, int] | None  # (occurred_at in UTC, id)
    after_id: int | None


def format_occurred_at(value: datetime) -> str:
    """UTC, always six fractional digits, always ``Z``: ``2026-10-14T10:00:00.123456Z``.

    One fixed format: the string orders like the time, and the cursor round-trips without loss.
    """
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _integer(name: str, text: str) -> int:
    text = text.strip()
    # Longer than any id (2**63 - 1 has 19 digits): also keeps ``int()`` away from Python's limit on huge
    # digit strings, which raises a plain ValueError and would turn a 400 into a 500.
    if len(text) > _MAX_INTEGER_TEXT or not _INTEGER.fullmatch(text):
        raise FeedError(f"{name} must be an integer, got {text[:40]!r}.")
    return int(text)


def parse_cursor(text: str) -> tuple[datetime, int]:
    """Parse ``<occurred_at>,<id>`` (the timestamp is the exact string the server returned)."""
    timestamp, separator, raw_id = text.rpartition(",")
    if not separator:
        raise FeedError("before must be '<occurred_at>,<id>'.")
    try:
        occurred_at = datetime.fromisoformat(timestamp)
    except ValueError:
        raise FeedError(f"before has an unreadable timestamp: {timestamp!r}.") from None
    if occurred_at.tzinfo is None:
        raise FeedError("before must carry a UTC offset or Z.")
    try:
        occurred_at = occurred_at.astimezone(UTC)
    except OverflowError:
        raise FeedError(f"before has a timestamp out of range: {timestamp!r}.") from None
    event_id = _integer("before id", raw_id)
    if not 0 <= event_id <= MAX_EVENT_ID:
        raise FeedError("before id is out of range.")
    return occurred_at, event_id


def parse_request(params: Mapping[str, str]) -> FeedRequest:
    """Validate the query string. Raises :class:`FeedError` (HTTP 400)."""
    project_ids = tuple(dict.fromkeys(
        item.strip() for item in (params.get("project_ids") or "").split(",") if item.strip()
    ))
    provider = None
    if params.get("provider"):
        try:
            provider = Provider(params["provider"]).value
        except ValueError:
            raise FeedError(f"Unknown provider: {params['provider']!r}.") from None
    limit = DEFAULT_LIMIT
    if "limit" in params:
        limit = _integer("limit", params["limit"])
        if limit < 1:
            raise FeedError("limit must be at least 1.")
        limit = min(limit, MAX_LIMIT)
    if "before" in params and "after_id" in params:
        raise FeedError("before and after_id are mutually exclusive.")
    before = parse_cursor(params["before"]) if "before" in params else None
    after_id = None
    if "after_id" in params:
        after_id = _integer("after_id", params["after_id"])
        if not 0 <= after_id <= MAX_EVENT_ID:
            raise FeedError("after_id must be between 0 and 2**63 - 1.")
    return FeedRequest(project_ids, provider, limit, before, after_id)


def _keyset(before: tuple[datetime, int]) -> RawSQL:
    occurred_at, event_id = before
    return RawSQL(
        "(occurred_at, id) < (%s, %s)",
        [connection.ops.adapt_datetimefield_value(occurred_at), event_id],
        output_field=BooleanField(),
    )


def _page_querysets(request: FeedRequest) -> list:
    """The main query and, when the main one cannot return them, the instance-events query."""
    ordered = TimelineEvent.objects.order_by("-occurred_at", "-id")
    keyset = [_keyset(request.before)] if request.before else []
    main = ordered.filter(*keyset)
    if request.project_ids:
        main = main.filter(project_id__in=request.project_ids)
    if request.provider:
        main = main.filter(provider=request.provider)
    queries = [main]
    if request.project_ids or request.provider:
        # Instance events have no project and no provider: the filters above exclude them.
        queries.append(ordered.filter(*keyset, session_id__isnull=True))
    return queries


def _fetch_page(request: FeedRequest) -> tuple[list[TimelineEvent], bool]:
    """Newest-first events of one page and ``has_more`` (one extra row is read, as the sessions page does)."""
    wanted = request.limit + 1
    merged: dict[int, TimelineEvent] = {}
    for queryset in _page_querysets(request):
        for event in queryset[:wanted]:
            merged[event.id] = event
    rows = sorted(merged.values(), key=lambda event: (event.occurred_at, event.id), reverse=True)[:wanted]
    return rows[: request.limit], len(rows) > request.limit


def _after_scope(request: FeedRequest) -> RawSQL | None:
    """Scope of an ``after_id`` fetch, instance events included, with unary-plus columns (see the module doc)."""
    clauses: list[str] = []
    params: list[str] = []
    if request.project_ids:
        clauses.append(f"+project_id IN ({', '.join(['%s'] * len(request.project_ids))})")
        params.extend(request.project_ids)
    if request.provider:
        clauses.append("+provider = %s")
        params.append(request.provider)
    if not clauses:
        return None
    return RawSQL(f"(({' AND '.join(clauses)}) OR +session_id IS NULL)", params, output_field=BooleanField())


def _after_queryset(request: FeedRequest):
    """The ``after_id`` query: one extra row is read to know whether the cap was exceeded."""
    queryset = TimelineEvent.objects.filter(id__gt=request.after_id)
    scope = _after_scope(request)
    if scope is not None:
        queryset = queryset.filter(scope)
    return queryset.order_by("id")[: AFTER_ID_CAP + 1]


def _fetch_after(request: FeedRequest) -> tuple[list[TimelineEvent], bool]:
    """Events with an id above ``after_id``, id ascending, at most :data:`AFTER_ID_CAP`, and ``truncated``."""
    rows = list(_after_queryset(request))
    return rows[:AFTER_ID_CAP], len(rows) > AFTER_ID_CAP


def _cited_session_ids(events: list[TimelineEvent]) -> set[str]:
    cited: set[str] = set()
    for event in events:
        if event.session_id:
            cited.add(event.session_id)
        if event.actor == TimelineActor.AGENT and event.actor_ref:
            cited.add(event.actor_ref)
        if event.kind == TimelineKind.SESSION_CREATED and isinstance(event.data, dict):
            spawned_by = event.data.get("spawned_by")
            if isinstance(spawned_by, str) and spawned_by:
                cited.add(spawned_by)
    return cited


def build_sessions_table(session_ids: set[str]) -> dict[str, dict]:
    """The side table: one slim entry per session that has a row. An id with no row gets no entry."""
    from twicc.pending_titles import get_pending_title

    table: dict[str, dict] = {}
    ordered = sorted(session_ids)
    for start in range(0, len(ordered), SESSION_LOOKUP_CHUNK):
        rows = Session.objects.filter(id__in=ordered[start:start + SESSION_LOOKUP_CHUNK]).values(
            "id", "title", "project_id", "provider", "hidden", "archived",
        )
        for row in rows:
            table[row["id"]] = {
                "title": get_pending_title(row["id"]) or row["title"] or None,
                "project_id": row["project_id"],
                "provider": row["provider"],
                "hidden": bool(row["hidden"]),
                "archived": bool(row["archived"]),
            }
    return table


def serialize_event(event: TimelineEvent) -> dict:
    """The public shape of an event. ``recorded_at``, ``dedupe_key`` and ``caused_by`` are not sent."""
    return {
        "id": event.id,
        "kind": event.kind,
        "occurred_at": format_occurred_at(event.occurred_at),
        "precision": event.precision,
        "actor": event.actor,
        "via": event.via,
        "actor_ref": event.actor_ref,
        "actor_name": event.actor_name,
        "reason": event.reason,
        "data": event.data,
        "session_id": event.session_id,
        "project_id": event.project_id,
        "provider": event.provider,
    }


def latest_event_id() -> int | None:
    """The highest id of the whole table, whatever the scope."""
    return TimelineEvent.objects.order_by("-id").values_list("id", flat=True).first()


def fetch_feed(request: FeedRequest) -> dict:
    """The response body of ``GET /api/timeline/``.

    ``latest_id`` is read FIRST (display design 7.2): a row committed afterwards may appear in
    ``events`` with a higher id, which is harmless; a row can never be below ``latest_id`` and missing
    from a result that claims it.
    """
    latest_id = latest_event_id()
    if request.after_id is not None:
        events, truncated = _fetch_after(request)
        has_more = False
    else:
        events, has_more = _fetch_page(request)
        truncated = False
    return {
        "events": [serialize_event(event) for event in events],
        "has_more": has_more,
        "truncated": truncated,
        "latest_id": latest_id,
        "sessions": build_sessions_table(_cited_session_ids(events)),
    }
```


- [ ] **Step 4: Add the view and the route**

`src/twicc/views.py` (modify):

```diff
--- a/src/twicc/views.py
+++ b/src/twicc/views.py
@@ -41,8 +41,9 @@ from twicc.core.session_queries import (
 )
 from twicc.core.text_filter import match_text_query
 from twicc.core.services.project_mutation import clean_project_agent_defaults
 from twicc.core.services.session_timeline import record_title_change
+from twicc.core.services import timeline_feed
 from twicc.paths import path_to_project_id
 from twicc.projects import register_project
 from twicc.providers.db_writer import run_under_db_write_lock
 from twicc.providers.sessions_watcher import mark_session_search_version_current
@@ -3410,8 +3411,31 @@ async def daily_activity(request, project_id=None):
         },
     })
 
 
+async def timeline(request):
+    """GET /api/timeline/ - The timeline feed: events newest first, a side table of the sessions they cite.
+
+    Query params (all optional):
+        project_ids: Comma-separated project ids. Empty or missing means the global scope.
+        provider: Provider key; unknown value: 400. Instance events (``backend.*``) are always included.
+        limit: Page size, 1..100 (default 50, larger values are capped to 100); anything else: 400.
+        before: Keyset cursor ``<occurred_at>,<id>``, the exact string the server returned.
+        after_id: Live fetch: events with a greater id, id ascending, at most 500 (``truncated``).
+                  Mutually exclusive with ``before``.
+
+    Returns ``{"events", "has_more", "truncated", "latest_id", "sessions"}``. Read-only.
+    Design: ``docs/plans/2026-10-15-timeline-display-design.md`` section 7.
+    """
+    if request.method != "GET":
+        return HttpResponseNotAllowed(["GET"])
+    try:
+        feed_request = timeline_feed.parse_request(request.GET)
+    except timeline_feed.FeedError as error:
+        return JsonResponse({"error": str(error)}, status=400)
+    return JsonResponse(await sync_to_async(timeline_feed.fetch_feed)(feed_request))
+
+
 async def search_sessions(request):
     """GET /api/search/ - Full-text search across session messages."""
     if not search.is_initialized():
         return JsonResponse({"error": "Search index not ready"}, status=503)
```


`src/twicc/urls.py` (modify):

```diff
--- a/src/twicc/urls.py
+++ b/src/twicc/urls.py
@@ -30,8 +30,9 @@ urlpatterns = [
     path("api/bootstrap/", views.bootstrap),
     path("api/changelog/", views.changelog),
     path("api/home/", views.home_data),
     path("api/daily-activity/", views.daily_activity),  # Global daily activity
+    path("api/timeline/", views.timeline),
     path("api/sessions/", views.all_sessions),
     # Static route must come BEFORE the <str:session_id> catch-all, otherwise
     # `bulk-archive` is consumed as a session_id and matched by session_by_id.
     path("api/sessions/bulk-archive/", views.bulk_archive_sessions),
```


- [ ] **Step 5: Run the tests and see them pass**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_api.py -q
```

Expected: `80 passed`. The tests build their events with `TimelineEvent.objects.create(...)` (explicit `occurred_at`, with microseconds where needed), so they do not depend on T1 writers beyond the `provider` column.

- [ ] **Step 6: Lint and commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uvx ruff check src/twicc/core/services/timeline_feed.py src/twicc/views.py src/twicc/urls.py tests/test_timeline_api.py
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/core/services/timeline_feed.py src/twicc/views.py src/twicc/urls.py tests/test_timeline_api.py
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
feat(timeline): add the GET /api/timeline/ feed

Read-only, owner-only endpoint behind the Timeline page. Events come newest first by
(occurred_at, id) with a row-value keyset cursor (never the expanded OR form, which is
up to 100 times slower on busy projects), a limit of 1..100 (default 50) and a
has_more flag read with one extra row. occurred_at is sent in UTC with microseconds so
the cursor round-trips without loss.

Instance events (backend.*) have no project and no provider: a second query returns
them for every case except the global scope without a provider, then the two result
sets are merged and deduplicated. after_id is one primary-key range query (the scope
columns carry a unary plus so SQLite cannot read the whole project index), ascending by
id, capped at 500 rows with truncated=true. latest_id is read first. A slim side table
holds the sessions the events cite (event, agent actor_ref, spawned_by), with the
pending-title override.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 3: The live signal

**Files:**
- Create: `src/twicc/core/services/timeline_signal.py`
- Modify: `src/twicc/core/services/session_timeline.py` (request the signal after a successful write), `src/twicc/core/services/timeline_backfill.py` (one signal per run), `src/twicc/cli/run.py` (start and stop)
- Create: `tests/test_timeline_signal.py`

**Interfaces:**
- Consumes (T1): `record_event_sync`, `record_events_bulk_sync`.
- Produces: in `twicc.core.services.timeline_signal`: `THROTTLE_SECONDS = 0.3`, `MESSAGE_TYPE = "timeline_changed"`, `start_timeline_signal(loop=None)`, `stop_timeline_signal()`, `poke_on_commit()`, `poke()`. The WebSocket frame `{"type": "timeline_changed", "latest_id": int}` (T10 handles it in `useWebSocket.js`).

- [ ] **Step 1: Write the failing tests**

The tests cover the writer side (after commit only, outside any transaction at once, rollback, dedupe hit, failed insert, batch, empty batch, bulk archive, batch fallback, raw insert silent, backfill silent per session and one signal per run that wrote, none for a run that wrote nothing, failing `poke` and failing `on_commit` never fail the writer, pokes from worker threads reaching the loop in asyncio debug mode (which refuses a timer armed from another thread), and a `poke` that fails when the OUTER transaction exits does not raise out of it (`robust=True`); the throttle constant is pinned at 0.3), the emission (no loop, closed loop, one message per burst with the latest id, the throttle rate under a sustained stream, a later poke opens a new window, empty table, failed send logged and the next poke works, no DB write lock needed, stop drops the window, delivery through the real channel layer), the after-commit rule of the batch path, and the wiring in `cli/run.py`. That last test reads the SOURCE of the file (the server loop cannot be started in a test): start before the backfill task, stop after its cancellation, and in the partial-start `finally` stop FIRST so a failing cancel cannot skip it. A second check reads the normal shutdown block (from the backfill cancellation to the next section) so the shutdown stop cannot be dropped unnoticed.

`tests/test_timeline_signal.py` (new file):

```python
# tests/test_timeline_signal.py
"""The ``timeline_changed`` signal (display design 2026-10-15, sections 9.1 and 9.3)."""

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import transaction
from django.utils import timezone

from twicc.core.models import Project, Session, TimelineActor, TimelineEvent, TimelineKind
from twicc.core.services import timeline_signal
from twicc.core.services.session_timeline import (
    EventSpec, insert_event_rows, record_event_sync, record_events_bulk_sync,
)
from twicc.core.services.timeline_backfill import run_timeline_backfill
from twicc.providers import db_writer
from tests.timeline_helpers import run_with_writer

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def reset_signal():
    timeline_signal.stop_timeline_signal()
    yield
    timeline_signal.stop_timeline_signal()


@pytest.fixture
def project():
    return Project.objects.create(id="-tl-signal", directory="/tmp/tl-signal")


@pytest.fixture
def pokes(monkeypatch):
    """Count the pokes writers request (the module function, replaced where writers look it up)."""
    calls = []
    monkeypatch.setattr(timeline_signal, "poke", lambda: calls.append(1))
    return calls


@pytest.fixture
def sent(monkeypatch):
    """The messages the signal sends to the ``updates`` group."""
    messages = []

    async def group_send(group, message):
        messages.append((group, message))

    monkeypatch.setattr(timeline_signal, "get_channel_layer", lambda: SimpleNamespace(group_send=group_send))
    return messages


def make_event(**fields):
    return TimelineEvent.objects.create(
        kind=TimelineKind.TURN_STARTED, occurred_at=timezone.now(), actor=TimelineActor.AGENT,
        session_id="s1", **fields,
    )


def run_loop(scenario, debug=False):
    """Run ``scenario`` on a fresh loop with the signal started (the throttle comes from ``short_throttle``)."""
    async def runner():
        timeline_signal.start_timeline_signal(asyncio.get_running_loop())
        try:
            return await scenario()
        finally:
            timeline_signal.stop_timeline_signal()

    return asyncio.run(runner(), debug=debug)


@pytest.fixture
def short_throttle(monkeypatch):
    monkeypatch.setattr(timeline_signal, "THROTTLE_SECONDS", 0.1)
    return 0.1


# -- Writers request the signal after the commit ---------------------------------------------------------------


def test_a_single_write_requests_the_signal_only_after_the_commit(pokes):
    with transaction.atomic():
        assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
        assert pokes == []
    assert pokes == [1]


def test_a_write_outside_any_transaction_requests_the_signal_at_once(pokes):
    record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
    assert pokes == [1]


def test_a_rolled_back_write_sends_nothing(pokes):
    with pytest.raises(RuntimeError), transaction.atomic():
        record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
        raise RuntimeError("the caller fails afterwards")
    assert pokes == [] and TimelineEvent.objects.count() == 0


def test_a_dedupe_hit_sends_nothing(pokes):
    kwargs = {"session_id": "s1", "actor": TimelineActor.SYSTEM, "dedupe_key": "k", "data": {"origin": "sync"}}
    assert record_event_sync(TimelineKind.SESSION_CREATED, **kwargs) is not None
    assert pokes == [1]
    assert record_event_sync(TimelineKind.SESSION_CREATED, **kwargs) is None
    assert pokes == [1]


def test_a_failed_insert_sends_nothing(pokes):
    assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT,
                             data={"bogus": 1}) is None
    assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor="system", precision=None) is None
    assert pokes == []


def test_a_non_empty_batch_requests_one_signal_and_an_all_duplicate_batch_none(pokes):
    specs = [EventSpec(TimelineKind.ARCHIVED_STARTED, session_id=f"s{i}") for i in range(3)]
    assert record_events_bulk_sync(specs) == 3
    assert pokes == [1]
    keyed = [EventSpec(TimelineKind.SESSION_CREATED, session_id="k", dedupe_key="x", data={"origin": "sync"})]
    assert record_events_bulk_sync(keyed) == 1
    assert pokes == [1, 1]
    assert record_events_bulk_sync(keyed) == 0
    assert pokes == [1, 1]


def test_a_batch_requests_the_signal_only_after_the_commit(pokes):
    with transaction.atomic():
        assert record_events_bulk_sync([EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="s1")]) == 1
        assert pokes == []
    assert pokes == [1]


def test_an_empty_batch_sends_nothing(pokes):
    assert record_events_bulk_sync([]) == 0
    assert pokes == []


def test_the_bulk_archive_requests_the_signal(project, pokes):
    from twicc.core.services.session_update import archive_sessions_bulk

    for sid in ("a", "b"):
        Session.objects.create(id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex")
    archive_sessions_bulk({"a", "b"})
    assert pokes == [1]


def test_the_batch_fallback_requests_the_signal_per_row_written(pokes, monkeypatch):
    from twicc.core.services import session_timeline

    def failing_batch(rows):
        raise RuntimeError("batch insert failed")

    monkeypatch.setattr(session_timeline, "insert_event_rows", failing_batch)
    assert record_events_bulk_sync([EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="s1")]) == 1
    assert pokes == [1]


def test_the_raw_batch_insert_itself_is_silent(pokes):
    rows = [TimelineEvent(
        session_id="s1", kind=TimelineKind.TURN_STARTED, occurred_at=timezone.now(), actor=TimelineActor.AGENT,
        data={},
    )]
    assert insert_event_rows(rows) == 1
    assert pokes == []


def test_the_backfill_is_silent_per_session_and_signals_once_at_the_end_of_a_run_that_wrote(project, pokes):
    for sid in ("a", "b", "c"):
        Session.objects.create(
            id=sid, file_path=f"{sid}.jsonl", project=project, provider="codex",
            created_at=timezone.now() - timedelta(days=1), archived=True,
        )
    run_with_writer(run_timeline_backfill)
    assert TimelineEvent.objects.count() == 6
    assert pokes == [1]


def test_a_backfill_run_that_wrote_nothing_sends_nothing(project, pokes):
    run_with_writer(run_timeline_backfill)
    Session.objects.create(id="a", file_path="a.jsonl", project=project, provider="codex")  # no created_at
    run_with_writer(run_timeline_backfill)
    assert pokes == []


def test_a_failing_poke_does_not_fail_the_writer(monkeypatch):
    def boom():
        raise RuntimeError("signal broken")

    monkeypatch.setattr(timeline_signal, "poke", boom)
    event = record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT)
    assert event is not None and TimelineEvent.objects.count() == 1


def test_a_failing_on_commit_registration_does_not_fail_the_writer(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("on_commit broken")

    monkeypatch.setattr(timeline_signal.transaction, "on_commit", boom)
    assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT) is not None
    assert record_events_bulk_sync([EventSpec(TimelineKind.ARCHIVED_STARTED, session_id="s2")]) == 1


# -- The emission ---------------------------------------------------------------------------------------------------


def test_the_throttle_is_a_third_of_a_second():
    assert timeline_signal.THROTTLE_SECONDS == 0.3


def test_a_failing_poke_at_commit_time_does_not_break_the_transaction_exit(monkeypatch):
    # ``on_commit(..., robust=True)``: the callback runs at the exit of the outer atomic block, whose caller
    # must not see the failure.
    def boom():
        raise RuntimeError("signal broken")

    monkeypatch.setattr(timeline_signal, "poke", boom)
    with transaction.atomic():
        assert record_event_sync(TimelineKind.TURN_STARTED, session_id="s1", actor=TimelineActor.AGENT) is not None
    assert TimelineEvent.objects.count() == 1


def test_a_poke_without_a_running_loop_is_a_noop():
    timeline_signal.poke()
    timeline_signal.poke_on_commit()


def test_a_poke_on_a_closed_loop_is_caught():
    loop = asyncio.new_event_loop()
    timeline_signal.start_timeline_signal(loop)
    loop.close()
    timeline_signal.poke()


def test_a_burst_sends_one_message_with_the_latest_id(sent, short_throttle):
    made = [make_event() for _ in range(3)]

    async def scenario():
        for _ in range(20):
            await asyncio.to_thread(timeline_signal.poke)
        await asyncio.sleep(0.5)

    run_loop(scenario)
    assert sent == [("updates", {
        "type": "broadcast", "data": {"type": "timeline_changed", "latest_id": made[-1].id},
    })]


def test_pokes_from_worker_threads_reach_the_loop_safely(sent, short_throttle):
    # In debug mode asyncio refuses ``call_later`` from another thread: a poke that touched the timer directly
    # (instead of going through ``call_soon_threadsafe``) would be dropped and nothing would be sent.
    make_event()

    async def scenario():
        await asyncio.gather(*(asyncio.to_thread(timeline_signal.poke) for _ in range(5)))
        await asyncio.sleep(0.5)

    run_loop(scenario, debug=True)
    assert len(sent) == 1


def test_a_sustained_stream_sends_at_the_throttle_rate(sent, short_throttle):
    make_event()

    async def scenario():
        for _ in range(40):
            timeline_signal.poke()
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.3)

    run_loop(scenario)
    # 0.4 s of pokes with a 0.1 s window: well below one signal per poke (40), well above a single one.
    assert 2 <= len(sent) <= 8


def test_the_signal_waits_for_the_window_then_a_later_poke_opens_a_new_one(sent, short_throttle):
    make_event()

    async def scenario():
        timeline_signal.poke()
        await asyncio.sleep(0.01)
        early = len(sent)
        await asyncio.sleep(0.3)
        first = len(sent)
        timeline_signal.poke()
        await asyncio.sleep(0.3)
        return early, first, len(sent)

    assert run_loop(scenario) == (0, 1, 2)


def test_an_empty_table_sends_nothing(sent, short_throttle):
    async def scenario():
        timeline_signal.poke()
        await asyncio.sleep(0.3)

    run_loop(scenario)
    assert sent == []


def test_a_failed_send_is_logged_and_the_next_poke_still_works(monkeypatch, short_throttle):
    make_event()
    attempts = []
    logged = []

    async def group_send(group, message):
        attempts.append(message)
        if len(attempts) == 1:
            raise RuntimeError("layer down")

    monkeypatch.setattr(timeline_signal, "get_channel_layer", lambda: SimpleNamespace(group_send=group_send))
    # Spy on the logger itself: other tests of the suite reconfigure the ``twicc`` loggers, so ``caplog`` is not reliable.
    monkeypatch.setattr(timeline_signal.logger, "exception", lambda message, *args: logged.append(message % args))

    async def scenario():
        timeline_signal.poke()
        await asyncio.sleep(0.3)
        timeline_signal.poke()
        await asyncio.sleep(0.3)

    run_loop(scenario)
    assert len(attempts) == 2
    assert logged == ["Timeline signal could not be sent"]


def test_the_emission_does_not_need_the_db_write_lock(sent, short_throttle):
    make_event()

    async def scenario():
        db_writer.start_db_writer()
        release = asyncio.Event()

        async def hold_the_lock():
            async def inside():
                await release.wait()

            await db_writer.run_under_db_write_lock(inside)

        holder = asyncio.create_task(hold_the_lock())
        try:
            await asyncio.sleep(0.1)
            timeline_signal.poke()
            await asyncio.sleep(0.4)
            return len(sent)
        finally:
            release.set()
            await holder
            await db_writer.stop_db_writer()

    assert run_loop(scenario) == 1


def test_stopping_the_signal_drops_a_pending_window(sent, short_throttle):
    make_event()

    async def scenario():
        timeline_signal.poke()
        await asyncio.sleep(0.01)
        timeline_signal.stop_timeline_signal()
        await asyncio.sleep(0.3)

    run_loop(scenario)
    assert sent == []


def test_a_message_reaches_a_websocket_client_through_the_updates_group(short_throttle):
    """The real channel layer: a consumer in the ``updates`` group receives the unfiltered frame."""
    from channels.layers import get_channel_layer

    make_event()

    async def scenario():
        layer = get_channel_layer()
        channel = await layer.new_channel()
        await layer.group_add("updates", channel)
        timeline_signal.start_timeline_signal(asyncio.get_running_loop())
        timeline_signal.poke()
        message = await asyncio.wait_for(layer.receive(channel), timeout=2)
        timeline_signal.stop_timeline_signal()
        return message

    message = asyncio.run(scenario())
    assert message["type"] == "broadcast" and message["data"]["type"] == "timeline_changed"
    assert isinstance(message["data"]["latest_id"], int)


# -- Wiring ---------------------------------------------------------------------------------------------------------


def test_the_server_source_starts_the_signal_before_the_backfill_and_stops_it_at_shutdown():
    source = Path(__import__("twicc.cli.run", fromlist=["run"]).__file__).read_text()
    start = source.index("start_timeline_signal(asyncio.get_running_loop())")
    backfill = source.index('timeline_backfill_task = asyncio.create_task(_guarded_timeline_backfill()')
    cancel = source.index('await _cancel_task(timeline_backfill_task, "Timeline backfill")')
    stop = source.index("stop_timeline_signal()", cancel)
    shutdown = source[cancel:source.index("Stop cross-provider tasks first", cancel)]
    assert start < backfill and cancel < stop
    assert "stop_timeline_signal()" in shutdown, "the normal shutdown stops it right after the backfill is cancelled"
    # The outer ``finally`` (partial start) stops the signal FIRST, so a failing cancel cannot skip it.
    outer = source[source.index("# Own partial coordinator startup"):]
    assert 0 <= outer.index("stop_timeline_signal()") < outer.index("_cancel_task(")
    assert "from twicc.core.services.timeline_signal import start_timeline_signal, stop_timeline_signal" in source
```


- [ ] **Step 2: Run the tests and see them fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_signal.py -q
```

Expected: a collection error, `ImportError: cannot import name 'timeline_signal' from 'twicc.core.services'`.

- [ ] **Step 3: Write the signal module**

`src/twicc/core/services/timeline_signal.py` (new file):

```python
# src/twicc/core/services/timeline_signal.py
"""The light "a new timeline event exists" signal (display design 2026-10-15, section 9.1).

A writer calls :func:`poke_on_commit` after a successful insert. Once the transaction commits, the poke
arms a trailing throttle on the event loop; when it fires, ONE ``timeline_changed`` message carrying the
highest event id goes to the ``updates`` group. The message holds no session, project or content, so it
is not filtered by project and leaks nothing. Clients then fetch ``after_id`` themselves.

Rules of the module:

* Nothing here ever raises into a writer: a failure is logged and the event stays written.
* No running loop (tests, CLI, the boot before startup) means the poke is a no-op. A closed loop
  (shutdown) is caught.
* The emission (the read of the latest id and the send) never runs under the DB write lock.
* All timeline writes happen in the main process, where the loop lives.
"""

from __future__ import annotations

import asyncio
import logging

from channels.layers import get_channel_layer
from django.db import transaction

logger = logging.getLogger(__name__)

#: Seconds between two signals at most (trailing): about three per second.
THROTTLE_SECONDS = 0.3
#: ``type`` of the WebSocket message.
MESSAGE_TYPE = "timeline_changed"

_loop: asyncio.AbstractEventLoop | None = None
_timer: asyncio.TimerHandle | None = None
_flushes: set[asyncio.Task] = set()


def start_timeline_signal(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Remember the loop the signal runs on (the running one by default). Called at startup."""
    global _loop
    _loop = loop or asyncio.get_running_loop()


def stop_timeline_signal() -> None:
    """Forget the loop and drop a pending timer. Called at shutdown; safe to call twice."""
    global _loop, _timer
    timer, _timer, _loop = _timer, None, None
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            logger.debug("Timeline signal timer could not be cancelled", exc_info=True)


def poke_on_commit() -> None:
    """Request a signal once the current transaction commits (at once outside a transaction).

    A rolled-back transaction never runs the callback, so a row that did not persist sends nothing.
    Never raises.
    """
    try:
        transaction.on_commit(poke, robust=True)
    except Exception:
        logger.exception("Timeline signal could not be scheduled")


def poke() -> None:
    """Ask for a signal. Thread-safe; a no-op without a running loop. Never raises."""
    loop = _loop
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(_arm)
    except RuntimeError:
        logger.debug("Timeline signal dropped: the event loop is closed")
    except Exception:
        logger.exception("Timeline signal could not be requested")


def _arm() -> None:
    """On the loop: start the throttle window unless one is already running (the pokes coalesce)."""
    global _timer
    loop = _loop
    if loop is None or _timer is not None:
        return
    _timer = loop.call_later(THROTTLE_SECONDS, _fire)


def _fire() -> None:
    """On the loop: the window ended. A poke from now on opens the next window."""
    global _timer
    _timer = None
    loop = _loop
    if loop is None:
        return
    task = loop.create_task(_flush())
    _flushes.add(task)
    task.add_done_callback(_flushes.discard)


def _read_latest_id() -> int | None:
    from twicc.core.models import TimelineEvent

    return TimelineEvent.objects.order_by("-id").values_list("id", flat=True).first()


async def _flush() -> None:
    """Read the latest id (in a thread, outside the write lock) and send the message."""
    try:
        latest_id = await asyncio.to_thread(_read_latest_id)
        layer = get_channel_layer()
        if latest_id is None or layer is None:
            return
        await layer.group_send(
            "updates", {"type": "broadcast", "data": {"type": MESSAGE_TYPE, "latest_id": latest_id}},
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Timeline signal could not be sent")
```


- [ ] **Step 4: Request the signal from the writers and wire the server**

The single path requests it after its insert (not on a dedupe hit or a failure); the bulk path once per non-empty batch. The raw `insert_event_rows` stays silent on purpose (the backfill calls it):

`src/twicc/core/services/session_timeline.py` (modify):

```diff
--- a/src/twicc/core/services/session_timeline.py
+++ b/src/twicc/core/services/session_timeline.py
@@ -20,8 +20,9 @@ from django.utils import timezone
 
 from twicc.core.models import (
     Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
 )
+from twicc.core.services import timeline_signal
 from twicc.core.timeline import STARTED_OF, inherit_started_fields, validate_data
 from twicc.providers.db_writer import run_under_db_write_lock
 from twicc.request_origin import (
     ACTOR_NAME_MAX_LENGTH, ACTOR_REF_MAX_LENGTH, DEFAULT_ORIGIN, VIA_VALUES, RequestOrigin,
@@ -145,9 +146,10 @@ def record_event_sync(
     copied from the open ``started`` row of the same session (design §3);
     the origin fields are never copied.
 
     ``provider`` is an optional hint (a free source the caller already holds); the ``provider`` column is
-    resolved by :func:`resolve_provider` whatever the caller passes.
+    resolved by :func:`resolve_provider` whatever the caller passes. After a successful insert (not on a
+    dedupe hit, not on a failure) the timeline signal is requested for when the transaction commits.
     """
     try:
         resolved = resolve_event_origin(actor, origin)
         if kind in STARTED_OF:
@@ -173,8 +175,9 @@ def record_event_sync(
                 data=payload,
                 dedupe_key=dedupe_key,
                 **origin_columns(resolved),
             )
+        timeline_signal.poke_on_commit()
         return event
     except IntegrityError:
         if dedupe_key is not None:
             logger.debug("Timeline event %s deduplicated by the database for %s", kind, session_id)
@@ -268,8 +271,9 @@ def record_events_bulk_sync(specs: list[EventSpec]) -> int:
     logged and skipped, the others are kept), ``STARTED_OF`` inheritance, ``dedupe_key`` duplicates
     (against the table and inside the batch) skipped by the database itself. The valid rows go in
     ONE ``executemany`` inside a savepoint. If that batch fails, each row is retried alone through
     :func:`record_event_sync`, so one bad row costs only itself. Call it under the DB write lock.
+    A non-empty batch requests the timeline signal once, for when the transaction commits.
     """
     if not specs:
         return 0
     try:
@@ -295,9 +299,12 @@ def record_events_bulk_sync(specs: list[EventSpec]) -> int:
             return 0
         rows = [row for _, row, _ in built]
         try:
             with transaction.atomic():
-                return insert_event_rows(rows)
+                written = insert_event_rows(rows)
+            if written:
+                timeline_signal.poke_on_commit()
+            return written
         except Exception:
             logger.exception("Timeline batch insert failed (%s rows); retrying row by row", len(rows))
             return sum(
                 1 for spec, row, resolved in built
```


The backfill ends a run that wrote rows with one `poke()` (every session's transaction has committed by then):

`src/twicc/core/services/timeline_backfill.py` (modify):

```diff
--- a/src/twicc/core/services/timeline_backfill.py
+++ b/src/twicc/core/services/timeline_backfill.py
@@ -22,8 +22,9 @@ from django.db.models import Exists, OuterRef
 
 from twicc.core.models import (
     Session, SessionType, TimelineActor, TimelineEvent, TimelineKind, TimelinePrecision,
 )
+from twicc.core.services import timeline_signal
 from twicc.core.services.session_timeline import insert_event_rows
 from twicc.core.services.timeline_boot import BACKFILL_PREFIX
 from twicc.core.timeline import validate_data
 from twicc.providers.db_writer import is_db_writer_started, run_under_db_write_lock
@@ -203,8 +204,11 @@ async def run_timeline_backfill() -> None:
     for session_id, cause in failed.items():
         logger.error(
             "Timeline backfill gave up on session %s after %d retries: %s", session_id, len(RETRY_PAUSES), cause,
         )
+    if stats["rows"]:
+        # The sessions were written silently: ONE signal for the whole run, every transaction committed.
+        timeline_signal.poke()
     if session_ids:
         logger.info(
             "Timeline backfill: %d session(s) written (%d row(s)), %d skipped, %d failed in %.1f s",
             stats[BackfillStatus.WRITTEN], stats["rows"], stats[BackfillStatus.SKIPPED], len(failed),
```


The server starts the signal right before the backfill task (which needs it for its end-of-run signal) and stops it at shutdown. In the outer `finally` (partial start) it stops it before the awaited cancellations, so an exception there cannot skip it:

`src/twicc/cli/run.py` (modify):

```diff
--- a/src/twicc/cli/run.py
+++ b/src/twicc/cli/run.py
@@ -106,8 +106,9 @@ from twicc.search_indexing_task import (  # noqa: E402
 from twicc.version_check_task import start_version_check_task, stop_version_check_task  # noqa: E402
 from twicc.tips_manifest import init_manifest, start_tips_watcher_task  # noqa: E402
 from twicc.help_manifest import init_manifest as init_help_manifest, start_help_watcher_task  # noqa: E402
 from twicc.title_auto_task import start_title_auto_task  # noqa: E402
+from twicc.core.services.timeline_signal import start_timeline_signal, stop_timeline_signal  # noqa: E402
 
 
 async def _cancel_task(task: asyncio.Task | None, name: str) -> None:
     """Cancel an asyncio task and wait for it to finish."""
@@ -287,8 +288,10 @@ async def run_server(port: int):
     # Start before provider watchers can submit live requests. The runner owns
     # no boot scan; initial sync does not request automatic title checks.
     title_auto_task = asyncio.create_task(start_title_auto_task(shutdown_event))
     # The writer is already started (see above); the backfill also does nothing without it.
+    # The signal needs the loop before the backfill can end a run with its one signal.
+    start_timeline_signal(asyncio.get_running_loop())
     timeline_backfill_task = asyncio.create_task(_guarded_timeline_backfill(), name="timeline-backfill")
     inline_exports = None
     try:
         from twicc.inline_artifacts.share_exports import get_inline_export_coordinator
@@ -423,8 +426,9 @@ async def run_server(port: int):
             # Finish cancellation of checks and provider pushes before provider or
             # database infrastructure is torn down.
             await _cancel_task(title_auto_task, "Automatic title task")
             await _cancel_task(timeline_backfill_task, "Timeline backfill")
+            stop_timeline_signal()
 
             # Stop cross-provider tasks first. The price sync loop watches
             # ``shutdown_event`` directly (set above by the signal handler),
             # so we just wait for it to finish.
@@ -540,8 +544,9 @@ async def run_server(port: int):
             logger.info("Server shutdown complete")
     finally:
         # Own partial coordinator startup and provider/configuration failures too.
         try:
+            stop_timeline_signal()
             if not title_auto_task.done():
                 await _cancel_task(title_auto_task, "Automatic title task")
             if not timeline_backfill_task.done():
                 await _cancel_task(timeline_backfill_task, "Timeline backfill")
```


- [ ] **Step 5: Run the tests and see them pass**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_signal.py -q
```

Expected: `29 passed` (the timing tests use a 0.1 s window with at least a 3 times margin; repeat the run three times anyway). A mutation that changes the window changes `test_the_throttle_is_a_third_of_a_second`, because the other tests patch the constant. Then the writers' suites:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_timeline_signal.py tests/test_timeline_provider.py tests/test_timeline_api.py tests/test_timeline_backfill.py tests/test_timeline_bulk_archive.py tests/test_timeline_bulk_record.py -q
```

Expected: all pass (193 in the scratch run).

- [ ] **Step 6: Lint and commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uvx ruff check src/twicc/core/services/timeline_signal.py src/twicc/core/services/session_timeline.py src/twicc/core/services/timeline_backfill.py src/twicc/cli/run.py tests/test_timeline_signal.py
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add src/twicc/core/services/timeline_signal.py src/twicc/core/services/session_timeline.py src/twicc/core/services/timeline_backfill.py src/twicc/cli/run.py tests/test_timeline_signal.py
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
feat(timeline): signal clients when a new event exists

A light timeline_changed WebSocket message carrying only the latest event id goes to the
updates group, so clients fetch after_id themselves. Writers request it through
transaction.on_commit after a successful single write or a non-empty batch (a rolled-back
row, a dedupe hit or a failed insert sends nothing). A trailing 0.3 s window coalesces a
burst into one message and caps a sustained stream near three per second.

The emission reads the latest id in a thread and never takes the DB write lock. With no
running loop the request is a no-op; a closed loop, a failing on_commit registration or a
failed send are caught and logged and never fail the writer. The backfill stays silent per
session and signals once at the end of a run that wrote rows. The server starts the
signal before the backfill task and stops it at shutdown.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 4: The event catalogue (pure modules)

Everything that decides how ONE event reads: time and text helpers, the `settings.changed` diff, the background-work badge, how a session is named and linked, the "by ..." label, and the view-model of a row (19 kinds, 15 `process.ended` reasons, every fallback of section 6.1.3, the tooltip). No Vue, no store, no DOM: the suites run with node alone.

**Files:**
- Create: `frontend/src/utils/timeline/eventTime.js`, `settingsDiff.js`, `backgroundBadge.js`, `sessions.js`, `authorLabel.js`, `describeEvent.js`
- Create: one `*.test.js` beside each

**Interfaces:**
- Consumes: `utils/backgroundWork.js` (`pluralize`, `formatClockTime`, existing).
- Produces (used by T5 to T10):
  - `eventTime.js`: `ARROW = ' -> '`; `compareNewestFirst(a, b)` (negative when `a` sorts first; compares the `occurred_at` STRINGS, then `id` descending); `cursorOf(event) -> '<occurred_at>,<id>'`; `parseOccurredAt(text) -> ms | NaN`; `formatClock(ms, {locale, timeZone}) -> 'HH:MM'`; `formatFullTime(ms, {locale, timeZone})`; `truncateText(text, max) -> {text, truncated}`; `splitFirstLine(text) -> {first, rest}`.
  - `settingsDiff.js`: `MAX_SHOWN_CHANGES = 3`; `describeSettingsChange(changes, reason, labels) -> {text, full, severity}` where `labels` is `null` or `{field(name), choice(field, value), model(value)}`.
  - `backgroundBadge.js`: `describeBackgroundWork(work) -> {text, title} | null`.
  - `sessions.js`: `sessionMention(sessionId, sessions, {fullId, fallbackProvider, isSubagent}) -> {id, title, provider, projectId, link, hidden, archived}`; `groupHeadIdentity(event, sessions, scope, options) -> {mention, projectId, showProject}` with `scope = {kind: 'global'|'workspace'|'project'|'worktree', projectId}`.
  - `authorLabel.js`: `describeAuthor(event, {sessions, isSubagent}) -> {kind: 'none'|'text'|'session', parts, unknown}`; `parts` are `{type: 'text', text}` or `{type: 'session', mention}`.
  - `describeEvent.js`: `PROCESS_ENDED_REASONS`; `describeEvent(event, {sessions, labelsFor, isSubagent, time}) -> {id, kind, icon, severity, parts, tag, badge, detail, author, time: {text, estimated, caption, full}, tooltip}`.

- [ ] **Step 1: Write the failing tests**

Besides the wording tables, they pin that `isSubagent` reaches both the agent author label and the `spawned_by` parent (a known subagent is plain text), and the tooltips of process and backend rows (`twicc_pid`, `pid`, `version`, `boot_id`).

`frontend/src/utils/timeline/eventTime.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import {
    ARROW, compareNewestFirst, cursorOf, formatClock, formatFullTime, parseOccurredAt, splitFirstLine, truncateText,
} from './eventTime.js'

const event = (occurred_at, id) => ({ occurred_at, id })

test('newest first: the later time sorts first, the higher id breaks ties', () => {
    const rows = [
        event('2026-10-14T10:00:00.000000Z', 1),
        event('2026-10-14T10:00:05.000000Z', 2),
        event('2026-10-14T10:00:05.000000Z', 3),
        event('2026-10-14T09:59:59.999999Z', 4),
    ]
    assert.deepEqual(rows.sort(compareNewestFirst).map((row) => row.id), [3, 2, 1, 4])
})

test('the comparison keeps the microseconds that Date.parse would lose', () => {
    // The later row (by microseconds) has the LOWER id: an ordering that merged the two rows into one
    // millisecond would fall back to the id and put the wrong row first.
    const later = event('2026-10-14T10:00:00.000900Z', 1)
    const earlier = event('2026-10-14T10:00:00.000100Z', 2)
    assert.equal(Date.parse(later.occurred_at), Date.parse(earlier.occurred_at), 'the same millisecond')
    assert.ok(compareNewestFirst(later, earlier) < 0 && compareNewestFirst(earlier, later) > 0)
    assert.deepEqual([earlier, later].sort(compareNewestFirst).map((row) => row.id), [1, 2])
})

test('the cursor is the exact server string and the id', () => {
    assert.equal(cursorOf(event('2026-10-14T10:00:00.123456Z', 42)), '2026-10-14T10:00:00.123456Z,42')
})

test('parseOccurredAt reads the wire format and returns NaN for garbage', () => {
    assert.equal(parseOccurredAt('2026-10-14T10:00:00.500000Z'), Date.UTC(2026, 9, 14, 10, 0, 0, 500))
    assert.ok(Number.isNaN(parseOccurredAt('nope')) && Number.isNaN(parseOccurredAt(null)))
})

test('clock time is hour and minute, the tooltip time has the date and the seconds', () => {
    const ms = Date.UTC(2026, 9, 14, 14, 32, 7)
    assert.equal(formatClock(ms, { locale: 'en-GB', timeZone: 'UTC' }), '14:32')
    const full = formatFullTime(ms, { locale: 'en-GB', timeZone: 'UTC' })
    assert.match(full, /14 Oct 2026/)
    assert.match(full, /14:32:07/)
    assert.equal(formatClock(NaN), '')
    assert.equal(formatFullTime(NaN), '')
})

test('truncateText cuts near the limit with an ellipsis and says so', () => {
    assert.deepEqual(truncateText('short', 10), { text: 'short', truncated: false })
    const cut = truncateText('a'.repeat(100), 80)
    assert.equal(cut.truncated, true)
    assert.equal(cut.text.length, 80)
    assert.ok(cut.text.endsWith('…'))
    assert.deepEqual(truncateText(null, 5), { text: '', truncated: false })
})

test('splitFirstLine separates the first line from the rest', () => {
    assert.deepEqual(splitFirstLine('one'), { first: 'one', rest: '' })
    assert.deepEqual(splitFirstLine('one\ntwo\nthree'), { first: 'one', rest: 'two\nthree' })
})

test('the arrow is one constant', () => {
    assert.equal(ARROW, ' -> ')
})
```


`frontend/src/utils/timeline/settingsDiff.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import { MAX_SHOWN_CHANGES, describeSettingsChange } from './settingsDiff.js'

const labels = {
    field: (field) => ({ selected_model: 'Model', effort: 'Effort', permission_mode: 'Permission', fast_mode: 'Fast mode' }[field] ?? field),
    choice: (field, value) => ({ effort: { high: 'High', max: 'Max' }, permission_mode: { default: 'Default', yolo: 'YOLO' } }[field]?.[value] ?? null),
    model: (value) => ({ sonnet: 'Sonnet', opus: 'Opus' }[value] ?? value),
}

test('a change reads "<Field>: <old> -> <new>" with provider labels, joined by commas', () => {
    const result = describeSettingsChange(
        { selected_model: ['sonnet', 'opus'], effort: ['high', 'max'] }, '', labels,
    )
    assert.equal(result.text, 'Model: Sonnet -> Opus, Effort: High -> Max')
    assert.equal(result.severity, 'neutral')
})

test('booleans read on/off and null reads default', () => {
    const result = describeSettingsChange({ fast_mode: [false, true], effort: [null, 'high'] }, '', labels)
    assert.equal(result.text, 'Fast mode: off -> on, Effort: default -> High')
})

test('an unknown provider shows raw field names and raw values, still with on/off and default', () => {
    const result = describeSettingsChange({ selected_model: ['sonnet', 'opus'], fast_mode: [true, null] }, '', null)
    assert.equal(result.text, 'selected_model: sonnet -> opus, fast_mode: on -> default')
})

test('a field outside the bundle keeps its raw name and raw values even with labels', () => {
    const result = describeSettingsChange({ brand_new: ['a', 'b'] }, '', labels)
    assert.equal(result.text, 'brand_new: a -> b')
})

test('a value with no choice label falls back to the raw value', () => {
    assert.equal(describeSettingsChange({ effort: ['low', 'high'] }, '', labels).text, 'Effort: low -> High')
})

test('at most three changes are written, then "+N more"; the full text lists all', () => {
    const changes = {
        selected_model: ['sonnet', 'opus'], effort: ['high', 'max'], permission_mode: ['default', 'yolo'],
        fast_mode: [false, true], thinking_enabled: [true, false],
    }
    const result = describeSettingsChange(changes, '', labels)
    assert.equal(MAX_SHOWN_CHANGES, 3)
    assert.equal(result.text, 'Model: Sonnet -> Opus, Effort: High -> Max, Permission: Default -> YOLO, +2 more')
    assert.match(result.full, /Fast mode: off -> on/)
    assert.match(result.full, /thinking_enabled: on -> off/)
    assert.ok(!result.full.includes('more'))
})

test('exactly three changes get no "+N more"', () => {
    const changes = { selected_model: ['a', 'b'], effort: ['low', 'high'], fast_mode: [false, true] }
    assert.ok(!describeSettingsChange(changes, '', labels).text.includes('more'))
})

test('the reason prefixes', () => {
    const changes = { effort: ['high', 'max'] }
    assert.equal(describeSettingsChange(changes, 'model_retired', labels).text, 'Model upgraded (retired): Effort: High -> Max')
    assert.equal(describeSettingsChange(changes, 'set_mode', labels).text, 'Permission mode set: Effort: High -> Max')
    assert.equal(describeSettingsChange(changes, 'switch-hybrid', labels).text, 'Hybrid switch: Effort: High -> Max')
    assert.equal(describeSettingsChange(changes, 'something-new', labels).text, 'Effort: High -> Max')
    assert.equal(describeSettingsChange(changes, '', labels).text, 'Effort: High -> Max')
})

test('a new permission mode that runs without asking is a warning', () => {
    assert.equal(describeSettingsChange({ permission_mode: ['default', 'bypassPermissions'] }, '', labels).severity, 'warning')
    assert.equal(describeSettingsChange({ permission_mode: ['default', 'yolo'] }, '', labels).severity, 'warning')
    assert.equal(describeSettingsChange({ permission_mode: ['yolo', 'default'] }, '', labels).severity, 'neutral')
})

test('empty, missing or malformed changes read "Settings changed"', () => {
    for (const changes of [undefined, null, {}, [], 'x', { effort: 'oops' }, { effort: ['only-one'] }]) {
        assert.equal(describeSettingsChange(changes, '', labels).text, 'Settings changed', JSON.stringify(changes))
    }
})
```


`frontend/src/utils/timeline/backgroundBadge.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import { formatClockTime } from '../backgroundWork.js'
import { describeBackgroundWork } from './backgroundBadge.js'

const work = (fields) => ({ subagents: 0, shells: 0, monitors: 0, scheduled_wakeup_at: null, goal: false, ...fields })

test('no badge when nothing runs or the shape is unknown', () => {
    for (const value of [null, undefined, work({}), 'x', 3, [], true]) {
        assert.equal(describeBackgroundWork(value), null, JSON.stringify(value))
    }
})

test('the non-zero keys are listed in the fixed order, pluralised', () => {
    const badge = describeBackgroundWork(work({ goal: true, monitors: 1, shells: 2, subagents: 3 }))
    assert.equal(badge.text, '3 subagents, 2 shells, 1 monitor, goal')
})

test('a single subagent is singular', () => {
    assert.equal(describeBackgroundWork(work({ subagents: 1 })).text, '1 subagent')
})

test('the wake-up shows its local clock time, after the counters and before the goal', () => {
    const iso = '2026-10-14T14:05:00.000Z'
    const clock = formatClockTime(Date.parse(iso) / 1000)
    const badge = describeBackgroundWork(work({ shells: 1, scheduled_wakeup_at: iso, goal: true }))
    assert.equal(badge.text, `1 shell, wake-up at ${clock}, goal`)
})

test('an unreadable wake-up time is skipped', () => {
    assert.equal(describeBackgroundWork(work({ shells: 1, scheduled_wakeup_at: 'soon' })).text, '1 shell')
})

test('the tooltip is the exact dict', () => {
    const value = work({ monitors: 2 })
    assert.equal(describeBackgroundWork(value).title, JSON.stringify(value))
})
```


`frontend/src/utils/timeline/sessions.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import { groupHeadIdentity, sessionMention } from './sessions.js'

const entry = (fields = {}) => ({
    title: 'My session', project_id: 'proj', provider: 'codex', hidden: false, archived: false, ...fields,
})

test('a session with an entry is a link with its title, provider and flags', () => {
    const mention = sessionMention('s1', { s1: entry({ archived: true }) })
    assert.deepEqual(mention, {
        id: 's1', title: 'My session', provider: 'codex', projectId: 'proj', link: true, hidden: false, archived: true,
    })
})

test('a hidden session is plain text with its marker, even when archived too', () => {
    const mention = sessionMention('s1', { s1: entry({ hidden: true, archived: true }) })
    assert.equal(mention.link, false)
    assert.equal(mention.hidden, true)
    assert.equal(mention.archived, true)
})

test('a session with no entry is plain text: the full id for a head, 8 characters elsewhere', () => {
    const id = 'abcdefghijklmnop'
    const head = sessionMention(id, {}, { fullId: true, fallbackProvider: 'claude_code' })
    assert.equal(head.title, id)
    assert.equal(head.link, false)
    assert.equal(head.provider, 'claude_code')
    assert.equal(sessionMention(id, {}).title, 'abcdefgh')
})

test('an untitled session falls back to its id', () => {
    assert.equal(sessionMention('s1', { s1: entry({ title: null }) }, { fullId: true }).title, 's1')
    assert.equal(sessionMention('s1', { s1: entry({ title: '' }) }).title, 's1')
})

test('a known subagent is plain text', () => {
    const mention = sessionMention('s1', { s1: entry() }, { isSubagent: (id) => id === 's1' })
    assert.equal(mention.link, false)
})

test('an entry without a project cannot be opened', () => {
    assert.equal(sessionMention('s1', { s1: entry({ project_id: null }) }).link, false)
})

test('the entry provider wins over the fallback', () => {
    assert.equal(sessionMention('s1', { s1: entry() }, { fallbackProvider: 'claude_code' }).provider, 'codex')
})

const event = (fields = {}) => ({ id: 1, session_id: 's1', project_id: 'p1', provider: 'codex', data: {}, ...fields })

test('head identity: provider fallback order is entry, event column, payload, none', () => {
    const noEntry = (e) => groupHeadIdentity(e, {}, { kind: 'global' }).mention.provider
    assert.equal(noEntry(event()), 'codex')
    assert.equal(noEntry(event({ provider: null, data: { provider: 'claude_code' } })), 'claude_code')
    assert.equal(noEntry(event({ provider: null })), null)
    assert.equal(groupHeadIdentity(event(), { s1: entry({ provider: 'claude_code' }) }, { kind: 'global' }).mention.provider, 'claude_code')
})

test('head identity: the event project wins, the side-table project is the fallback', () => {
    assert.equal(groupHeadIdentity(event(), { s1: entry() }, { kind: 'global' }).projectId, 'p1')
    assert.equal(groupHeadIdentity(event({ project_id: null }), { s1: entry() }, { kind: 'global' }).projectId, 'proj')
    assert.equal(groupHeadIdentity(event({ project_id: null }), {}, { kind: 'global' }).projectId, null)
})

test('the project name shows on global and workspace pages, never on a worktree page', () => {
    const show = (scope, projectId = 'p1') => groupHeadIdentity(event({ project_id: projectId }), {}, scope).showProject
    assert.equal(show({ kind: 'global' }), true)
    assert.equal(show({ kind: 'workspace' }), true)
    assert.equal(show({ kind: 'worktree', projectId: 'p1' }), false)
    assert.equal(show({ kind: 'worktree', projectId: 'other' }), false)
    assert.equal(show({ kind: 'global' }, null), false)
})

test('on a project page only the rows of its worktrees carry the name', () => {
    const scope = { kind: 'project', projectId: 'main' }
    assert.equal(groupHeadIdentity(event({ project_id: 'main' }), {}, scope).showProject, false)
    assert.equal(groupHeadIdentity(event({ project_id: 'main-wt' }), {}, scope).showProject, true)
})
```


`frontend/src/utils/timeline/authorLabel.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import { describeAuthor } from './authorLabel.js'

const sessions = {
    boss: { title: 'The boss', project_id: 'p', provider: 'claude_code', hidden: false, archived: false },
    secret: { title: 'Secret', project_id: 'p', provider: 'codex', hidden: true, archived: false },
}
const label = (fields) => describeAuthor(
    { kind: 'archived.started', precision: 'exact', actor: 'user', via: 'ui', actor_ref: null, actor_name: null, ...fields },
    { sessions },
)
const plainText = (author) => author.parts.map((part) => (part.type === 'text' ? part.text : `[${part.mention.title}]`)).join('')

test('the table of section 6.3, row by row', () => {
    const cases = [
        [{ actor: 'user', via: 'ui' }, null],
        [{ actor: 'user', via: null }, null],
        [{ actor: 'user', via: 'cli' }, 'by you (CLI)'],
        [{ actor: 'user', via: 'mcp_internal', actor_ref: 'tok_1' }, 'by you (MCP)'],
        [{ actor: 'user', via: 'rpc' }, 'by you (API)'],
        [{ actor: 'agent', via: 'cli', actor_ref: 'boss' }, 'by [The boss] (CLI)'],
        [{ actor: 'agent', via: 'mcp_internal', actor_ref: 'boss' }, 'by [The boss] (MCP)'],
        [{ actor: 'external', via: 'mcp_external', actor_name: 'ChatGPT' }, 'by ChatGPT (external MCP)'],
        [{ actor: 'external', via: 'mcp_external', actor_name: null }, 'by an external MCP client'],
        [{ actor: 'system', via: null }, 'automatic'],
        [{ actor: 'agent', via: null }, null],
    ]
    for (const [fields, expected] of cases) {
        const author = label(fields)
        assert.equal(author.kind === 'none' ? null : plainText(author), expected, JSON.stringify(fields))
        assert.equal(author.unknown, false, JSON.stringify(fields))
    }
})

test('an agent label names a session; the kind says so', () => {
    assert.equal(label({ actor: 'agent', via: 'cli', actor_ref: 'boss' }).kind, 'session')
    assert.equal(label({ actor: 'user', via: 'cli' }).kind, 'text')
})

test('the session of an agent label follows the link rules: missing and hidden sessions are plain text', () => {
    const mentionOf = (ref) => label({ actor: 'agent', via: 'cli', actor_ref: ref }).parts[1].mention
    assert.equal(mentionOf('boss').link, true)
    assert.equal(mentionOf('secret').link, false)
    assert.equal(mentionOf('unknown-session-id').link, false)
    assert.equal(mentionOf('unknown-session-id').title, 'unknown-')
})

test('an agent label without a session reference still reads', () => {
    assert.equal(plainText(label({ actor: 'agent', via: 'mcp_internal', actor_ref: null })), 'by an agent (MCP)')
})

test('"automatic" is not written on backend rows nor on session.created', () => {
    assert.equal(label({ actor: 'system', via: null, kind: 'backend.started' }).kind, 'none')
    assert.equal(label({ actor: 'system', via: null, kind: 'backend.ended' }).kind, 'none')
    assert.equal(label({ actor: 'system', via: null, kind: 'session.created' }).kind, 'none')
    assert.equal(label({ actor: 'system', via: null, kind: 'process.ended' }).kind, 'text')
})

test('no label on a row whose precision is not exact, for every actor', () => {
    for (const precision of ['estimated', 'derived']) {
        for (const [actor, via] of [['system', null], ['user', 'cli'], ['agent', 'cli'], ['external', 'mcp_external']]) {
            const author = label({ actor, via, precision, actor_ref: 'boss', actor_name: 'x' })
            assert.equal(author.kind, 'none', `${actor}/${via}/${precision}`)
            assert.equal(author.unknown, false)
        }
    }
})

test('an unknown precision counts as exact', () => {
    assert.equal(label({ actor: 'user', via: 'cli', precision: 'approximate' }).kind, 'text')
})

test('any other actor/via combination has no label and is flagged for the tooltip', () => {
    for (const fields of [
        { actor: 'user', via: 'mcp_external' }, { actor: 'agent', via: 'rpc' }, { actor: 'system', via: 'cli' },
        { actor: 'external', via: 'cli' }, { actor: 'alien', via: null }, { actor: 'user', via: 'toString' },
    ]) {
        const author = label(fields)
        assert.deepEqual([author.kind, author.unknown], ['none', true], JSON.stringify(fields))
    }
})

test('an external client name is cut near 80 characters', () => {
    const author = label({ actor: 'external', via: 'mcp_external', actor_name: 'N'.repeat(200) })
    assert.ok(plainText(author).length < 100)
    assert.ok(plainText(author).includes('…'))
})
```


`frontend/src/utils/timeline/describeEvent.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import { PROCESS_ENDED_REASONS, describeEvent } from './describeEvent.js'

const TIME = { locale: 'en-GB', timeZone: 'UTC' }
const sessions = {
    boss: { title: 'The boss', project_id: 'p', provider: 'claude_code', hidden: false, archived: false },
    parent: { title: 'Parent session', project_id: 'p', provider: 'codex', hidden: false, archived: false },
}
let nextId = 1
const make = (kind, fields = {}) => ({
    id: nextId++, kind, occurred_at: '2026-10-14T14:32:07.000000Z', precision: 'exact', actor: 'agent', via: null,
    actor_ref: null, actor_name: null, reason: '', data: {}, session_id: 's1', project_id: 'p', provider: 'codex', ...fields,
})
const describe = (kind, fields, context = {}) => describeEvent(make(kind, fields), { sessions, time: TIME, ...context })
const words = (vm) => vm.parts.map((part) => (part.type === 'text' ? part.text : `<${part.mention.title}>`)).join('')

test('the 19 kinds: wording, icon and severity (section 6.1)', () => {
    const cases = [
        ['session.created', { data: { origin: 'twicc' } }, 'Session created', 'plus', 'neutral'],
        ['title.changed', { data: { source: 'user', new: 'B', old: 'A' } }, 'Renamed "A" -> "B"', 'pencil', 'neutral'],
        ['settings.changed', { data: { changes: { effort: ['low', 'high'] } } }, 'effort: low -> high', 'sliders', 'neutral'],
        ['process.started', { data: { cause: 'new' } }, 'Process started', 'play', 'success'],
        ['process.ended', { reason: 'manual' }, 'Process stopped', 'power-off', 'neutral'],
        ['turn.started', {}, 'Turn started', 'robot', 'neutral'],
        ['turn.ended', { data: { background_work: null } }, 'Turn ended', 'circle-check', 'success'],
        ['awaiting_input.started', { data: { request_type: 'tool_approval' } }, 'Waiting for approval', 'hand', 'warning'],
        ['awaiting_input.ended', { data: { request_type: 'tool_approval' } }, 'Approval handled', 'circle-check', 'neutral'],
        ['archived.started', {}, 'Archived', 'box-archive', 'neutral'],
        ['archived.ended', {}, 'Unarchived', 'box-open', 'neutral'],
        ['hidden.started', {}, 'Hidden', 'eye-slash', 'neutral'],
        ['hidden.ended', {}, 'Unhidden', 'eye', 'neutral'],
        ['muted.started', {}, 'Muted', 'bell-slash', 'neutral'],
        ['muted.ended', {}, 'Unmuted', 'bell', 'neutral'],
        ['pinned.started', { data: { scope: 'project' } }, 'Pinned (project)', 'thumbtack', 'neutral'],
        ['pinned.ended', { data: { scope: 'project' } }, 'Unpinned (was project)', 'thumbtack-slash', 'neutral'],
        ['backend.started', { session_id: null, data: { version: '1.2.3' } }, 'TwiCC started v1.2.3', 'server', 'neutral'],
        ['backend.ended', { session_id: null, data: { clean: true } }, 'TwiCC stopped', 'power-off', 'neutral'],
    ]
    assert.equal(cases.length, 19)
    for (const [kind, fields, wording, icon, severity] of cases) {
        const vm = describe(kind, fields)
        assert.deepEqual([words(vm), vm.icon, vm.severity], [wording, icon, severity], kind)
    }
})

test('an unknown kind shows its raw name with a generic icon and no detail', () => {
    const vm = describe('plan.approved', { data: { anything: 1 } })
    assert.deepEqual([words(vm), vm.icon, vm.severity, vm.tag, vm.badge, vm.detail], ['plan.approved', 'circle-info', 'neutral', null, null, null])
})

test('data missing or not an object is treated as empty and never throws', () => {
    for (const data of [undefined, null, 'text', 7, ['a']]) {
        for (const kind of ['session.created', 'title.changed', 'settings.changed', 'process.started', 'process.ended', 'turn.ended',
            'awaiting_input.started', 'pinned.started', 'backend.started', 'backend.ended', 'archived.started']) {
            assert.doesNotThrow(() => describe(kind, { data }), `${kind} / ${JSON.stringify(data)}`)
        }
    }
})

test('process.ended: every reason has its wording and severity; the table has 15 reasons', () => {
    assert.equal(Object.keys(PROCESS_ENDED_REASONS).length, 15)
    const expected = {
        manual: ['Process stopped', 'neutral'], force: ['Process killed', 'neutral'],
        archived: ['Process stopped (session archived)', 'neutral'], shutdown: ['Process stopped (TwiCC shutdown)', 'neutral'],
        'apply-settings': ['Process restarted to apply settings', 'neutral'],
        'switch-hybrid': ['Process stopped (switch to hybrid)', 'neutral'],
        timeout_user_turn: ['Process stopped (idle timeout)', 'neutral'], 'cli-exit': ['Hybrid CLI exited', 'neutral'],
        timeout_starting: ['Process stopped: start timed out', 'warning'],
        timeout_assistant_turn: ['Process stopped: no activity', 'warning'],
        cron_restart_timeout: ['Cron restart timed out', 'warning'], lost: ['Process lost (found dead at boot)', 'warning'],
        auth_required: ['Process stopped: login required', 'danger'], 'startup-failed': ['Process failed to start', 'danger'],
        error: ['Process crashed', 'danger'],
    }
    for (const [reason, [wording, severity]] of Object.entries(expected)) {
        const vm = describe('process.ended', { reason })
        assert.deepEqual([words(vm), vm.severity], [wording, severity], reason)
        assert.equal(vm.icon, 'power-off')
    }
})

test('process.ended: an unknown reason reads "Process stopped" with the raw reason in the tooltip', () => {
    for (const reason of ['brand_new', 'constructor', '']) {
        const vm = describe('process.ended', { reason })
        assert.deepEqual([words(vm), vm.severity], ['Process stopped', 'neutral'], reason)
    }
    assert.match(describe('process.ended', { reason: 'brand_new' }).tooltip, /Reason: brand_new/)
})

test('process.ended error: the first line is the detail, the rest sits behind the expand control', () => {
    const vm = describe('process.ended', { reason: 'error', data: { error: 'Boom: it broke\n  at file.js:1\n  at main' } })
    assert.deepEqual(vm.detail, { first: 'Boom: it broke', rest: 'at file.js:1\n  at main' })
    assert.equal(describe('process.ended', { reason: 'error', data: { error: null } }).detail, null)
    assert.equal(describe('process.ended', { reason: 'manual', data: { error: 'ignored' } }).detail, null)
})

test('process.ended lost: the upper bound adds "Detected at boot HH:MM" to the tooltip', () => {
    const vm = describe('process.ended', {
        reason: 'lost', precision: 'estimated', data: { upper_bound: '2026-10-14T16:45:00+00:00' },
    })
    assert.match(vm.tooltip, /Detected at boot 16:45/)
})

test('process.started: resumed, hybrid, unknown cause', () => {
    assert.equal(words(describe('process.started', { data: { cause: 'resume' } })), 'Process resumed')
    assert.equal(words(describe('process.started', { data: { cause: 'resume', hybrid: true } })), 'Process resumed (hybrid CLI)')
    assert.equal(words(describe('process.started', { data: { cause: 'new', hybrid: true } })), 'Process started (hybrid CLI)')
    assert.equal(words(describe('process.started', { data: { cause: 'cron_restart' } })), 'Process started')
    assert.match(describe('process.started', { data: { twicc_pid: 4242 } }).tooltip, /Process id: 4242/)
})

test('awaiting_input: request_type wins over tool_name; tool_name is cut near 40 characters', () => {
    const w = (kind, data) => words(describe(kind, { data }))
    const started = 'awaiting_input.started'
    const ended = 'awaiting_input.ended'
    assert.equal(w(started, { request_type: 'tool_approval', tool_name: 'Bash' }), 'Waiting for approval (Bash)')
    assert.equal(w(started, { request_type: 'tool_approval', tool_name: null }), 'Waiting for approval')
    assert.equal(w(ended, { request_type: 'tool_approval', tool_name: 'Bash' }), 'Approval handled (Bash)')
    assert.equal(w(ended, { request_type: 'tool_approval' }), 'Approval handled')
    assert.equal(w(started, { request_type: 'ask_user_question', tool_name: 'AskUserQuestion' }), 'Question for you')
    assert.equal(w(ended, { request_type: 'ask_user_question', tool_name: 'AskUserQuestion' }), 'Question answered')
    assert.equal(w(started, { request_type: 'hybrid_terminal', tool_name: 'Bash' }), 'Waiting in the terminal (Bash)')
    assert.equal(w(started, { request_type: 'hybrid_terminal' }), 'Waiting in the terminal')
    assert.equal(w(ended, { request_type: 'hybrid_terminal', tool_name: 'Bash' }), 'Terminal input handled')
    assert.equal(w(started, { request_type: null, tool_name: 'Bash' }), 'Waiting for input (Bash)')
    assert.equal(w(started, { request_type: 'future_type' }), 'Waiting for input')
    assert.equal(w(ended, { request_type: 'future_type', tool_name: 'Bash' }), 'Input handled')
    const long = w(started, { request_type: 'tool_approval', tool_name: 'mcp__server__' + 'x'.repeat(100) })
    assert.ok(long.length < 80 && long.includes('…'))
})

test('session.created: origin wording', () => {
    const w = (data) => words(describe('session.created', { actor: 'system', data }))
    assert.equal(w({ origin: 'twicc' }), 'Session created')
    assert.equal(w({ origin: 'external' }), 'Session created from a terminal')
    assert.equal(w({ origin: 'sync' }), 'Session imported')
    assert.equal(w({ origin: 'something-else' }), 'Session created')
    assert.equal(w({}), 'Session created')
})

test('session.created: the parent appears once, and never next to an agent author label', () => {
    const parentPart = (vm) => vm.parts.find((part) => part.type === 'session')
    const plain = describe('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })
    assert.equal(words(plain), 'Session created by <Parent session>')
    assert.equal(parentPart(plain).mention.link, true)
    const imported = describe('session.created', { actor: 'system', data: { origin: 'sync', spawned_by: 'parent' } })
    assert.equal(words(imported), 'Session imported (spawned by <Parent session>)')
    const byAgent = describe('session.created', {
        actor: 'agent', via: 'mcp_internal', actor_ref: 'boss', data: { origin: 'twicc', spawned_by: 'parent' },
    })
    assert.equal(words(byAgent), 'Session created')
    assert.equal(parentPart(byAgent), undefined)
    assert.equal(byAgent.author.kind, 'session')
    const missing = describe('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'purged-id-123' } })
    assert.equal(words(missing), 'Session created by <purged-i>')
    assert.equal(parentPart(missing).mention.link, false)
})

test('title.changed: renamed, titled, cleared, source tags', () => {
    const title = (data) => describe('title.changed', { data })
    assert.equal(words(title({ source: 'user', old: 'A', new: 'B' })), 'Renamed "A" -> "B"')
    assert.equal(words(title({ source: 'user', old: null, new: 'B' })), 'Titled "B"')
    assert.equal(words(title({ source: 'user', old: '', new: 'B' })), 'Titled "B"')
    assert.equal(words(title({ source: 'user', old: 'A', new: null })), 'Title cleared')
    assert.equal(words(title({ source: 'user', old: 'A', new: '' })), 'Title cleared')
    assert.equal(title({ source: 'user', new: 'B' }).tag, null)
    assert.equal(title({ source: 'auto', new: 'B' }).tag, 'automatic')
    assert.equal(title({ source: 'agent', new: 'B' }).tag, 'by the agent')
    assert.equal(title({ source: 'future', new: 'B' }).tag, null)
    assert.equal(title({ source: 'constructor', new: 'B' }).tag, null)
})

test('title.changed: long titles are cut near 80 characters and the full text goes to the tooltip', () => {
    const old = 'O'.repeat(200)
    const next = 'N'.repeat(200)
    const vm = describe('title.changed', { data: { source: 'user', old, new: next } })
    assert.ok(words(vm).length < 200 && words(vm).includes('…'))
    assert.ok(vm.tooltip.includes(`Old title: ${old}`) && vm.tooltip.includes(`New title: ${next}`))
    const short = describe('title.changed', { data: { source: 'user', old: 'a', new: 'b' } })
    assert.ok(!short.tooltip.includes('Old title'))
})

test('strings from data stay plain strings: markup is not interpreted here', () => {
    const vm = describe('title.changed', { data: { source: 'user', old: null, new: '<img src=x onerror=alert(1)>' } })
    assert.equal(words(vm), 'Titled "<img src=x onerror=alert(1)>"')
})

test('settings.changed: labels come from the provider of the row, then of the session', () => {
    const asked = []
    const labelsFor = (provider) => {
        asked.push(provider)
        return null
    }
    describeEvent(make('settings.changed', { provider: 'codex', data: { changes: { effort: ['a', 'b'] } } }), { sessions, labelsFor, time: TIME })
    describeEvent(make('settings.changed', { provider: null, session_id: 'boss', data: { changes: { effort: ['a', 'b'] } } }), { sessions, labelsFor, time: TIME })
    assert.deepEqual(asked, ['codex', 'claude_code'])
})

test('settings.changed: a long diff keeps all changes in the tooltip; opening permissions is a warning', () => {
    const changes = { selected_model: ['a', 'b'], effort: ['a', 'b'], fast_mode: [false, true], permission_mode: ['default', 'yolo'] }
    const vm = describe('settings.changed', { data: { changes } })
    assert.equal(vm.severity, 'warning')
    assert.match(words(vm), /\+1 more$/)
    assert.match(vm.tooltip, /permission_mode: default -> yolo/)
})

test('pinned: scope wording and fallbacks', () => {
    const w = (kind, scope) => words(describe(kind, { data: { scope } }))
    assert.equal(w('pinned.started', 'workspace'), 'Pinned (workspace)')
    assert.equal(w('pinned.started', 'all'), 'Pinned (all projects)')
    assert.equal(w('pinned.started', null), 'Pinned')
    assert.equal(w('pinned.started', 'galaxy'), 'Pinned')
    assert.equal(w('pinned.ended', 'all'), 'Unpinned (was all projects)')
    assert.equal(w('pinned.ended', null), 'Unpinned')
    assert.equal(w('pinned.ended', 'toString'), 'Unpinned')
})

test('backend rows: version, machine restart, clean or not, tooltip facts', () => {
    const started = (data) => describe('backend.started', { session_id: null, actor: 'system', data })
    assert.equal(words(started({ version: null })), 'TwiCC started')
    assert.equal(words(started({ version: '1.2.3', machine_restarted: true })), 'TwiCC started v1.2.3 after a machine restart')
    assert.equal(words(started({ version: '1.2.3', machine_restarted: false })), 'TwiCC started v1.2.3')
    assert.equal(words(started({ version: '1.2.3', machine_restarted: null })), 'TwiCC started v1.2.3')
    const tooltip = started({ pid: 77, version: '1.2.3', boot_id: 'abc-def' }).tooltip
    assert.ok(tooltip.includes('Process id: 77') && tooltip.includes('Version: 1.2.3') && tooltip.includes('Boot id: abc-def'))
    const ended = (clean) => describe('backend.ended', { session_id: null, actor: 'system', data: { clean } })
    assert.deepEqual([words(ended(true)), ended(true).severity], ['TwiCC stopped', 'neutral'])
    assert.deepEqual([words(ended(false)), ended(false).severity], ['TwiCC stopped unexpectedly', 'danger'])
    assert.deepEqual([words(ended(null)), ended(null).severity], ['TwiCC stopped', 'neutral'])
})

test('turn.ended: the background-work badge, plain when there is none or the shape is unknown', () => {
    const badge = describe('turn.ended', { data: { background_work: { subagents: 2, shells: 0, monitors: 0, scheduled_wakeup_at: null, goal: false } } }).badge
    assert.equal(badge.text, '2 subagents')
    assert.equal(describe('turn.ended', { data: { background_work: null } }).badge, null)
    assert.equal(describe('turn.ended', { data: { background_work: 'weird' } }).badge, null)
    assert.equal(words(describe('turn.ended', { data: { background_work: 'weird' } })), 'Turn ended')
})

test('time: clock in the locale, "~" and a flag for estimated and derived, different captions', () => {
    const exact = describe('turn.started', {})
    assert.deepEqual([exact.time.text, exact.time.estimated], ['14:32', false])
    const estimated = describe('turn.started', { precision: 'estimated' })
    assert.deepEqual([estimated.time.text, estimated.time.estimated], ['~14:32', true])
    const derived = describe('session.created', { precision: 'derived', actor: 'system', data: { origin: 'sync' } })
    assert.deepEqual([derived.time.text, derived.time.estimated], ['~14:32', true])
    assert.match(estimated.tooltip, /Estimated: it happened at or before this time/)
    assert.match(derived.tooltip, /Derived from the session's creation time/)
    assert.ok(!derived.tooltip.includes('Estimated:') && !estimated.tooltip.includes('Derived from'))
    const unknown = describe('turn.started', { precision: 'approximate' })
    assert.deepEqual([unknown.time.text, unknown.time.estimated], ['14:32', false])
})

test('tooltip: full time, precision, reason, via, actor ref and name; unknown combinations show the actor', () => {
    const vm = describe('settings.changed', {
        actor: 'external', via: 'mcp_external', actor_ref: 'conn-1', actor_name: 'ChatGPT', reason: 'set_mode',
        data: { changes: { effort: ['a', 'b'] } },
    })
    for (const line of ['14 Oct 2026', '14:32:07', 'Precision: exact', 'Reason: set_mode', 'Via: mcp_external', 'Actor ref: conn-1', 'Actor name: ChatGPT']) {
        assert.ok(vm.tooltip.includes(line), line)
    }
    assert.ok(!vm.tooltip.includes('Actor: '))
    const odd = describe('archived.started', { actor: 'user', via: 'mcp_external' })
    assert.ok(odd.tooltip.includes('Actor: user'))
})

test('a known subagent is plain text in an agent author label and as the parent of session.created', () => {
    const isSubagent = (id) => id === 'boss' || id === 'parent'
    const label = describeEvent(make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 'boss' }), { sessions, time: TIME, isSubagent })
    assert.equal(label.author.parts[1].mention.link, false)
    assert.equal(describeEvent(make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 'boss' }), { sessions, time: TIME }).author.parts[1].mention.link, true)
    const created = make('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })
    const mention = (vm) => vm.parts.find((part) => part.type === 'session').mention
    assert.equal(mention(describeEvent(created, { sessions, time: TIME, isSubagent })).link, false)
    assert.equal(mention(describeEvent(created, { sessions, time: TIME })).link, true)
})

test('the tooltips of process and backend rows carry their technical facts', () => {
    assert.match(describe('process.ended', { reason: 'manual', data: { twicc_pid: 99 } }).tooltip, /Process id: 99/)
    const ended = describe('backend.ended', { session_id: null, actor: 'system', data: { clean: true, pid: 5, version: '1.0.0', boot_id: 'boot-1' } })
    for (const line of ['Process id: 5', 'Version: 1.0.0', 'Boot id: boot-1']) assert.ok(ended.tooltip.includes(line), line)
})

test('the author label is part of the view-model', () => {
    const vm = describe('archived.started', { actor: 'user', via: 'cli' })
    assert.equal(vm.author.parts[0].text, 'by you (CLI)')
})
```


- [ ] **Step 2: Run the tests and see them fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/utils/timeline/*.test.js
```

Expected: every suite fails with `ERR_MODULE_NOT_FOUND` (its module does not exist yet).

- [ ] **Step 3: Write the modules**

`frontend/src/utils/timeline/eventTime.js` (new file):

```javascript
// frontend/src/utils/timeline/eventTime.js
//
// Time and text helpers of the timeline display (docs/plans/2026-10-15-timeline-display-design.md).
// Pure (no Vue, no store, no DOM) so the node:test suite covers them.
//
// The server sends `occurred_at` in ONE fixed format, `YYYY-MM-DDTHH:MM:SS.ffffffZ` (UTC, six
// fractional digits). The string therefore orders like the time, microseconds included, which
// `Date.parse` (milliseconds) would lose. Ordering and cursors use the string; only DISPLAY
// converts to a Date.

/** The arrow between an old and a new value ("Renamed ... -> ..."), kept in one place. */
export const ARROW = ' -> '

/**
 * Newest-first comparator `(occurred_at, id)` descending: negative when `a` sorts before `b`.
 *
 * @param {{occurred_at: string, id: number}} a
 * @param {{occurred_at: string, id: number}} b
 * @returns {number}
 */
export function compareNewestFirst(a, b) {
    if (a.occurred_at !== b.occurred_at) return a.occurred_at < b.occurred_at ? 1 : -1
    return b.id - a.id
}

/**
 * The `before` cursor of an event: the exact server string and the id.
 *
 * @param {{occurred_at: string, id: number}} event
 * @returns {string}
 */
export function cursorOf(event) {
    return `${event.occurred_at},${event.id}`
}

/**
 * Epoch milliseconds of a wire timestamp, NaN when unreadable.
 *
 * @param {string} text
 * @returns {number}
 */
export function parseOccurredAt(text) {
    return typeof text === 'string' ? Date.parse(text) : NaN
}

/**
 * Clock time in the browser locale, hour and minute ("14:32").
 *
 * @param {number} ms
 * @param {{locale?: string, timeZone?: string}} [options] - Tests pin both; the app passes nothing.
 * @returns {string}
 */
export function formatClock(ms, { locale, timeZone } = {}) {
    if (!Number.isFinite(ms)) return ''
    return new Date(ms).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', timeZone })
}

/**
 * Full date and time with seconds in the browser locale (the tooltip of the time).
 *
 * @param {number} ms
 * @param {{locale?: string, timeZone?: string}} [options]
 * @returns {string}
 */
export function formatFullTime(ms, { locale, timeZone } = {}) {
    if (!Number.isFinite(ms)) return ''
    return new Date(ms).toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'medium', timeZone })
}

/**
 * Cut `text` to about `max` characters, ending with an ellipsis. The full text is meant for the tooltip.
 *
 * @param {string} text
 * @param {number} max
 * @returns {{text: string, truncated: boolean}}
 */
export function truncateText(text, max) {
    const value = typeof text === 'string' ? text : String(text ?? '')
    if (value.length <= max) return { text: value, truncated: false }
    return { text: `${value.slice(0, max - 1).trimEnd()}…`, truncated: true }
}

/**
 * Split a multi-line text into its first line and the rest.
 *
 * @param {string} text
 * @returns {{first: string, rest: string}}
 */
export function splitFirstLine(text) {
    const value = typeof text === 'string' ? text : String(text ?? '')
    const newline = value.indexOf('\n')
    if (newline === -1) return { first: value, rest: '' }
    return { first: value.slice(0, newline), rest: value.slice(newline + 1).trim() }
}
```


`frontend/src/utils/timeline/settingsDiff.js` (new file):

```javascript
// frontend/src/utils/timeline/settingsDiff.js
//
// Wording of a `settings.changed` row (display design section 6.2). Pure: the provider labels
// come in through an adapter, so the module never imports a provider helper (those pull in
// stores and the DOM).

import { ARROW } from './eventTime.js'

/** The eight fields of the closed agent-settings bundle (the only keys a payload can carry). */
const KNOWN_FIELDS = new Set([
    'selected_model', 'permission_mode', 'effort', 'thinking_enabled', 'claude_in_chrome',
    'fast_mode', 'context_max', 'question_widget',
])

const REASON_PREFIXES = {
    model_retired: 'Model upgraded (retired): ',
    set_mode: 'Permission mode set: ',
    'switch-hybrid': 'Hybrid switch: ',
}

/** Permission modes that run without asking: the row is a warning. */
const OPEN_PERMISSION_MODES = new Set(['bypassPermissions', 'yolo'])

/** At most this many changes are written in the row; the tooltip lists them all. */
export const MAX_SHOWN_CHANGES = 3

/**
 * What the provider can say about a field. `null` (unknown provider) means raw values.
 *
 * @typedef {Object} SettingsLabels
 * @property {(field: string) => string} field
 * @property {(field: string, value: *) => (string|null)} choice
 * @property {(model: string) => string} model
 */

function formatValue(field, value, labels) {
    if (value === null || value === undefined) return 'default'
    if (typeof value === 'boolean') return value ? 'on' : 'off'
    if (!labels || !KNOWN_FIELDS.has(field)) return String(value)
    if (field === 'selected_model') return labels.model(value) || String(value)
    return labels.choice(field, value) ?? String(value)
}

function formatChange(field, [oldValue, newValue], labels) {
    const name = labels && KNOWN_FIELDS.has(field) ? labels.field(field) : field
    return `${name}: ${formatValue(field, oldValue, labels)}${ARROW}${formatValue(field, newValue, labels)}`
}

/**
 * @param {*} changes - `data.changes`: `{field: [old, new]}`.
 * @param {string} reason - The row's `reason` column.
 * @param {SettingsLabels|null} labels
 * @returns {{text: string, full: string, severity: 'neutral'|'warning'}}
 */
export function describeSettingsChange(changes, reason, labels) {
    const entries = changes && typeof changes === 'object' && !Array.isArray(changes)
        ? Object.entries(changes).filter(([, pair]) => Array.isArray(pair) && pair.length === 2)
        : []
    if (!entries.length) return { text: 'Settings changed', full: 'Settings changed', severity: 'neutral' }
    const prefix = REASON_PREFIXES[reason] ?? ''
    const lines = entries.map(([field, pair]) => formatChange(field, pair, labels))
    const shown = lines.slice(0, MAX_SHOWN_CHANGES).join(', ')
    const hidden = lines.length - MAX_SHOWN_CHANGES
    const permission = changes.permission_mode
    const opens = Array.isArray(permission) && OPEN_PERMISSION_MODES.has(permission[1])
    return {
        text: `${prefix}${shown}${hidden > 0 ? `, +${hidden} more` : ''}`,
        full: `${prefix}${lines.join(', ')}`,
        severity: opens ? 'warning' : 'neutral',
    }
}
```


`frontend/src/utils/timeline/backgroundBadge.js` (new file):

```javascript
// frontend/src/utils/timeline/backgroundBadge.js
//
// The badge of a `turn.ended` row that left work running (display design section 6.5).
// Reuses `pluralize` and `formatClockTime` of utils/backgroundWork.js.

import { formatClockTime, pluralize } from '../backgroundWork.js'

/**
 * @param {*} work - `data.background_work`: `{subagents, shells, monitors, scheduled_wakeup_at, goal}` or null.
 * @returns {{text: string, title: string}|null} null when nothing runs or the shape is unknown.
 */
export function describeBackgroundWork(work) {
    if (!work || typeof work !== 'object' || Array.isArray(work)) return null
    const parts = []
    if (work.subagents > 0) parts.push(pluralize(work.subagents, 'subagent'))
    if (work.shells > 0) parts.push(pluralize(work.shells, 'shell'))
    if (work.monitors > 0) parts.push(pluralize(work.monitors, 'monitor'))
    const wakeup = typeof work.scheduled_wakeup_at === 'string' ? Date.parse(work.scheduled_wakeup_at) : NaN
    if (Number.isFinite(wakeup)) parts.push(`wake-up at ${formatClockTime(wakeup / 1000)}`)
    if (work.goal === true) parts.push('goal')
    if (!parts.length) return null
    return { text: parts.join(', '), title: JSON.stringify(work) }
}
```


`frontend/src/utils/timeline/sessions.js` (new file):

```javascript
// frontend/src/utils/timeline/sessions.js
//
// How the timeline names a session (display design sections 5.3 and 5.4): the title, the provider,
// and whether the mention is a link. One function serves every mention of a session: the group head,
// the "by <session>" author label and the `spawned_by` parent of `session.created`.
// Pure: the side table (`sessions`) is the only source.

/**
 * @typedef {Object} SideTableEntry
 * @property {string|null} title
 * @property {string|null} project_id
 * @property {string|null} provider
 * @property {boolean} hidden
 * @property {boolean} archived
 */

/**
 * @typedef {Object} SessionMention
 * @property {string} id
 * @property {string} title - The side-table title, else the id (the full id for a group head, the
 *   first 8 characters in a label).
 * @property {string|null} provider
 * @property {string|null} projectId
 * @property {boolean} link - True only for a session that exists, is not hidden, is not a known
 *   subagent and has a project: the only ones that can be opened.
 * @property {boolean} hidden
 * @property {boolean} archived
 */

/**
 * @param {string} sessionId
 * @param {Object<string, SideTableEntry>} sessions - The side table; an id with no row has no entry.
 * @param {Object} [options]
 * @param {boolean} [options.fullId=false] - Fall back to the whole id (the group head) rather than 8 characters.
 * @param {string|null} [options.fallbackProvider=null] - Used when the session has no entry.
 * @param {(id: string) => boolean} [options.isSubagent] - Best-effort knowledge of the data store: a
 *   subagent has no openable route as a top-level session.
 * @returns {SessionMention}
 */
export function sessionMention(sessionId, sessions, { fullId = false, fallbackProvider = null, isSubagent = () => false } = {}) {
    const entry = sessions?.[sessionId] ?? null
    const hidden = entry?.hidden === true
    return {
        id: sessionId,
        title: entry?.title || (fullId ? sessionId : String(sessionId).slice(0, 8)),
        provider: entry?.provider ?? fallbackProvider ?? null,
        projectId: entry?.project_id ?? null,
        link: entry !== null && !hidden && !!entry.project_id && !isSubagent(sessionId),
        hidden,
        archived: entry?.archived === true,
    }
}

/**
 * The identity shown once, on the group head (display design 5.3).
 *
 * @param {Object} event
 * @param {Object<string, SideTableEntry>} sessions
 * @param {{kind: 'global'|'workspace'|'project'|'worktree', projectId?: string|null}} scope
 * @param {Object} [options] - See {@link sessionMention}.
 * @returns {{mention: SessionMention, projectId: string|null, showProject: boolean}}
 */
export function groupHeadIdentity(event, sessions, scope, options = {}) {
    const data = event.data && typeof event.data === 'object' ? event.data : {}
    const mention = sessionMention(event.session_id, sessions, {
        ...options, fullId: true, fallbackProvider: event.provider ?? data.provider ?? null,
    })
    const projectId = event.project_id ?? sessions?.[event.session_id]?.project_id ?? null
    let showProject = false
    if (projectId) {
        if (scope.kind === 'global' || scope.kind === 'workspace') showProject = true
        // On a project page the rows of the project itself carry no name; the rows of its worktrees do.
        else if (scope.kind === 'project') showProject = projectId !== scope.projectId
    }
    return { mention, projectId, showProject }
}
```


`frontend/src/utils/timeline/authorLabel.js` (new file):

```javascript
// frontend/src/utils/timeline/authorLabel.js
//
// The "by ..." label that follows a row's wording (display design section 6.3). Chosen from `actor`
// and `via` TOGETHER, never from a NULL `via` alone: NULL means "unknown" (old rows) or "automatic".

import { truncateText } from './eventTime.js'
import { sessionMention } from './sessions.js'

/** Kinds whose wording already says it was automatic. */
const SELF_EXPLAINING = /^(backend\.|session\.created$)/

const VIA_SUFFIX = { cli: '(CLI)', mcp_internal: '(MCP)', rpc: '(API)' }

const NO_LABEL = Object.freeze({ kind: 'none', parts: [], unknown: false })

function text(value) {
    return { type: 'text', text: value }
}

/**
 * @typedef {Object} AuthorLabel
 * @property {'none'|'text'|'session'} kind - `session` when the label names a session.
 * @property {Array<{type: 'text', text: string}|{type: 'session', mention: Object}>} parts
 * @property {boolean} unknown - An actor/via combination the table does not know: no label, and the
 *   tooltip must show the raw values.
 */

/**
 * @param {Object} event
 * @param {Object} context
 * @param {Object<string, Object>} context.sessions - The side table.
 * @param {(id: string) => boolean} [context.isSubagent]
 * @returns {AuthorLabel}
 */
export function describeAuthor(event, { sessions, isSubagent }) {
    // A backfilled flag row or a `lost` row is dated by estimation and its actor is `system`, although
    // a user set the flag: no label on any row whose precision is not exact. An unknown precision counts as exact.
    if (event.precision === 'estimated' || event.precision === 'derived') return NO_LABEL
    const { actor, via } = event
    const withVia = (suffix) => (suffix ? ` ${suffix}` : '')
    if (actor === 'user') {
        if (via === 'ui' || via === null || via === undefined) return NO_LABEL
        if (typeof via === 'string' && Object.hasOwn(VIA_SUFFIX, via)) return { kind: 'text', parts: [text(`by you${withVia(VIA_SUFFIX[via])}`)], unknown: false }
    } else if (actor === 'agent') {
        if (via === null || via === undefined) return NO_LABEL
        if (via === 'cli' || via === 'mcp_internal') {
            const suffix = withVia(VIA_SUFFIX[via])
            if (!event.actor_ref) return { kind: 'text', parts: [text(`by an agent${suffix}`)], unknown: false }
            const mention = sessionMention(event.actor_ref, sessions, { isSubagent })
            return { kind: 'session', parts: [text('by '), { type: 'session', mention }, text(suffix)], unknown: false }
        }
    } else if (actor === 'external') {
        if (via === 'mcp_external') {
            const name = event.actor_name ? truncateText(event.actor_name, 80).text : null
            const label = name ? `by ${name} (external MCP)` : 'by an external MCP client'
            return { kind: 'text', parts: [text(label)], unknown: false }
        }
    } else if (actor === 'system') {
        if (via === null || via === undefined) {
            if (SELF_EXPLAINING.test(event.kind)) return NO_LABEL
            return { kind: 'text', parts: [text('automatic')], unknown: false }
        }
    }
    return { kind: 'none', parts: [], unknown: true }
}
```


`frontend/src/utils/timeline/describeEvent.js` (new file):

```javascript
// frontend/src/utils/timeline/describeEvent.js
//
// The view-model of one timeline event row: icon, severity, wording, tags, badge, author label, time and
// tooltip (display design sections 5.1, 6 and 14). Pure: a Vue component only shows what this returns.
//
// Every field of every kind has a fallback (design 6.1.3): a malformed or future payload never breaks a row.
// Every string that comes from data stays a plain string here; the components render it as TEXT, never as HTML.

import { describeAuthor } from './authorLabel.js'
import { describeBackgroundWork } from './backgroundBadge.js'
import { ARROW, formatClock, formatFullTime, parseOccurredAt, splitFirstLine, truncateText } from './eventTime.js'
import { describeSettingsChange } from './settingsDiff.js'
import { sessionMention } from './sessions.js'

const TITLE_MAX = 80
const TOOL_MAX = 40

/** Wording of a `process.ended` reason: [text, severity]. The reason set is open (see `describeProcessEnded`). */
export const PROCESS_ENDED_REASONS = {
    manual: ['Process stopped', 'neutral'],
    force: ['Process killed', 'neutral'],
    archived: ['Process stopped (session archived)', 'neutral'],
    shutdown: ['Process stopped (TwiCC shutdown)', 'neutral'],
    'apply-settings': ['Process restarted to apply settings', 'neutral'],
    'switch-hybrid': ['Process stopped (switch to hybrid)', 'neutral'],
    timeout_user_turn: ['Process stopped (idle timeout)', 'neutral'],
    'cli-exit': ['Hybrid CLI exited', 'neutral'],
    timeout_starting: ['Process stopped: start timed out', 'warning'],
    timeout_assistant_turn: ['Process stopped: no activity', 'warning'],
    cron_restart_timeout: ['Cron restart timed out', 'warning'],
    lost: ['Process lost (found dead at boot)', 'warning'],
    auth_required: ['Process stopped: login required', 'danger'],
    'startup-failed': ['Process failed to start', 'danger'],
    error: ['Process crashed', 'danger'],
}

const PIN_SCOPES = { project: 'project', workspace: 'workspace', all: 'all projects' }

const FLAG_KINDS = {
    'archived.started': ['Archived', 'box-archive'],
    'archived.ended': ['Unarchived', 'box-open'],
    'hidden.started': ['Hidden', 'eye-slash'],
    'hidden.ended': ['Unhidden', 'eye'],
    'muted.started': ['Muted', 'bell-slash'],
    'muted.ended': ['Unmuted', 'bell'],
}

const SESSION_CREATED_BASE = {
    twicc: 'Session created',
    external: 'Session created from a terminal',
    sync: 'Session imported',
}

const TITLE_SOURCE_TAGS = { auto: 'automatic', agent: 'by the agent' }

function text(value) {
    return { type: 'text', text: value }
}

/** `map[key]` for an own key only: a reason named "constructor" must not read the prototype. */
function lookup(map, key) {
    return typeof key === 'string' && Object.hasOwn(map, key) ? map[key] : undefined
}

function plain(value) {
    return { parts: [text(value)] }
}

function asString(value) {
    return typeof value === 'string' && value !== '' ? value : null
}

function toolName(data) {
    const name = asString(data.tool_name)
    return name ? truncateText(name, TOOL_MAX).text : null
}

function withTool(label, tool) {
    return tool ? `${label} (${tool})` : label
}

function describeSessionCreated(event, data, context, author) {
    const base = lookup(SESSION_CREATED_BASE, data.origin) ?? 'Session created'
    const parent = asString(data.spawned_by)
    // The parent appears once: an agent author label already names a session.
    if (!parent || author.kind === 'session') return { icon: 'plus', severity: 'neutral', parts: [text(base)] }
    const mention = sessionMention(parent, context.sessions, { isSubagent: context.isSubagent })
    const parts = base === 'Session created'
        ? [text(`${base} by `), { type: 'session', mention }]
        : [text(`${base} (spawned by `), { type: 'session', mention }, text(')')]
    return { icon: 'plus', severity: 'neutral', parts }
}

function describeTitleChanged(event, data) {
    const next = asString(data.new)
    const previous = asString(data.old)
    const tooltip = []
    const quote = (value, label) => {
        const cut = truncateText(value, TITLE_MAX)
        if (cut.truncated) tooltip.push(`${label}: ${value}`)
        return `"${cut.text}"`
    }
    let wording
    if (next === null) wording = 'Title cleared'
    else if (previous === null) wording = `Titled ${quote(next, 'New title')}`
    else wording = `Renamed ${quote(previous, 'Old title')}${ARROW}${quote(next, 'New title')}`
    return {
        icon: 'pencil', severity: 'neutral', parts: [text(wording)], tag: lookup(TITLE_SOURCE_TAGS, data.source) ?? null, tooltip,
    }
}

function describeSettings(event, data, context) {
    const provider = event.provider ?? context.sessions?.[event.session_id]?.provider ?? null
    const change = describeSettingsChange(data.changes, event.reason, context.labelsFor?.(provider) ?? null)
    return {
        icon: 'sliders', severity: change.severity, parts: [text(change.text)],
        tooltip: change.full !== change.text ? [change.full] : [],
    }
}

function describeProcessStarted(event, data) {
    const label = data.cause === 'resume' ? 'Process resumed' : 'Process started'
    const tooltip = Number.isFinite(data.twicc_pid) ? [`Process id: ${data.twicc_pid}`] : []
    return { icon: 'play', severity: 'success', parts: [text(data.hybrid === true ? `${label} (hybrid CLI)` : label)], tooltip }
}

function describeProcessEnded(event, data, context) {
    const known = lookup(PROCESS_ENDED_REASONS, event.reason)
    const [label, severity] = known ?? ['Process stopped', 'neutral']
    const tooltip = Number.isFinite(data.twicc_pid) ? [`Process id: ${data.twicc_pid}`] : []
    if (event.reason === 'lost' && asString(data.upper_bound)) {
        const bound = formatClock(parseOccurredAt(data.upper_bound), context.time)
        if (bound) tooltip.push(`Detected at boot ${bound}`)
    }
    const error = event.reason === 'error' ? asString(data.error) : null
    return {
        icon: 'power-off', severity, parts: [text(label)], tooltip,
        detail: error ? splitFirstLine(error) : null,
    }
}

const AWAITING = {
    tool_approval: {
        started: (tool) => withTool('Waiting for approval', tool),
        ended: (tool) => withTool('Approval handled', tool),
    },
    ask_user_question: { started: () => 'Question for you', ended: () => 'Question answered' },
    hybrid_terminal: {
        started: (tool) => withTool('Waiting in the terminal', tool),
        ended: () => 'Terminal input handled',
    },
}
const AWAITING_OTHER = { started: (tool) => withTool('Waiting for input', tool), ended: () => 'Input handled' }

function describeAwaiting(event, data, phase) {
    const wording = (lookup(AWAITING, data.request_type) ?? AWAITING_OTHER)[phase]
    return {
        icon: phase === 'started' ? 'hand' : 'circle-check',
        severity: phase === 'started' ? 'warning' : 'neutral',
        parts: [text(wording(toolName(data)))],
    }
}

function describePinned(event, data, phase) {
    const scope = lookup(PIN_SCOPES, data.scope) ?? null
    if (phase === 'started') {
        return { icon: 'thumbtack', severity: 'neutral', parts: [text(scope ? `Pinned (${scope})` : 'Pinned')] }
    }
    return { icon: 'thumbtack-slash', severity: 'neutral', parts: [text(scope ? `Unpinned (was ${scope})` : 'Unpinned')] }
}

function describeBackendStarted(event, data) {
    const version = asString(data.version)
    let label = version ? `TwiCC started v${version}` : 'TwiCC started'
    if (data.machine_restarted === true) label += ' after a machine restart'
    return { icon: 'server', severity: 'neutral', parts: [text(label)], tooltip: backendTooltip(data) }
}

function describeBackendEnded(event, data) {
    const unclean = data.clean === false
    return {
        icon: 'power-off', severity: unclean ? 'danger' : 'neutral',
        parts: [text(unclean ? 'TwiCC stopped unexpectedly' : 'TwiCC stopped')], tooltip: backendTooltip(data),
    }
}

function backendTooltip(data) {
    const lines = []
    if (Number.isFinite(data.pid)) lines.push(`Process id: ${data.pid}`)
    if (asString(data.version)) lines.push(`Version: ${data.version}`)
    if (asString(data.boot_id)) lines.push(`Boot id: ${data.boot_id}`)
    return lines
}

function describeKind(event, data, context, author) {
    switch (event.kind) {
        case 'session.created': return describeSessionCreated(event, data, context, author)
        case 'title.changed': return describeTitleChanged(event, data)
        case 'settings.changed': return describeSettings(event, data, context)
        case 'process.started': return describeProcessStarted(event, data)
        case 'process.ended': return describeProcessEnded(event, data, context)
        case 'turn.started': return { icon: 'robot', severity: 'neutral', ...plain('Turn started') }
        case 'turn.ended': {
            const badge = describeBackgroundWork(data.background_work)
            return { icon: 'circle-check', severity: 'success', parts: [text('Turn ended')], badge }
        }
        case 'awaiting_input.started': return describeAwaiting(event, data, 'started')
        case 'awaiting_input.ended': return describeAwaiting(event, data, 'ended')
        case 'pinned.started': return describePinned(event, data, 'started')
        case 'pinned.ended': return describePinned(event, data, 'ended')
        case 'backend.started': return describeBackendStarted(event, data)
        case 'backend.ended': return describeBackendEnded(event, data)
        default: break
    }
    const flag = lookup(FLAG_KINDS, event.kind)
    if (flag) return { icon: flag[1], severity: 'neutral', ...plain(flag[0]) }
    // A future kind on an older frontend: its raw name, a generic icon, no detail.
    return { icon: 'circle-info', severity: 'neutral', ...plain(String(event.kind)) }
}

function describeTime(event, context) {
    const ms = parseOccurredAt(event.occurred_at)
    const estimated = event.precision === 'estimated' || event.precision === 'derived'
    const clock = formatClock(ms, context.time)
    const caption = event.precision === 'estimated'
        ? 'Estimated: it happened at or before this time'
        : event.precision === 'derived' ? "Derived from the session's creation time" : null
    return {
        text: estimated ? `~${clock}` : clock,
        estimated,
        caption,
        full: formatFullTime(ms, context.time),
    }
}

/**
 * @typedef {Object} EventViewModel
 * @property {number} id
 * @property {string} kind
 * @property {string} icon - Font Awesome Free solid name.
 * @property {'neutral'|'success'|'warning'|'danger'} severity
 * @property {Array<Object>} parts - Wording segments: `{type: 'text', text}` or `{type: 'session', mention}`.
 * @property {string|null} tag - Small muted tag after the wording ("automatic", "by the agent").
 * @property {{text: string, title: string}|null} badge - Background-work badge of `turn.ended`.
 * @property {{first: string, rest: string}|null} detail - Monospace first line of an error, with the rest behind an expand control.
 * @property {import('./authorLabel.js').AuthorLabel} author
 * @property {{text: string, estimated: boolean, caption: string|null, full: string}} time
 * @property {string} tooltip - Technical details, one per line.
 */

/**
 * @param {Object} event - One event of the API.
 * @param {Object} context
 * @param {Object<string, Object>} context.sessions - The side table.
 * @param {(provider: string|null) => (Object|null)} [context.labelsFor] - Settings labels of a provider.
 * @param {(id: string) => boolean} [context.isSubagent]
 * @param {{locale?: string, timeZone?: string}} [context.time] - Tests pin them.
 * @returns {EventViewModel}
 */
export function describeEvent(event, context) {
    const data = event.data && typeof event.data === 'object' && !Array.isArray(event.data) ? event.data : {}
    const author = describeAuthor(event, context)
    const described = describeKind(event, data, context, author)
    const time = describeTime(event, context)
    const lines = [time.full]
    if (event.precision) lines.push(`Precision: ${event.precision}`)
    if (time.caption) lines.push(time.caption)
    lines.push(...(described.tooltip ?? []))
    if (event.reason) lines.push(`Reason: ${event.reason}`)
    if (event.via) lines.push(`Via: ${event.via}`)
    if (event.actor_ref) lines.push(`Actor ref: ${event.actor_ref}`)
    if (event.actor_name) lines.push(`Actor name: ${event.actor_name}`)
    if (author.unknown) lines.push(`Actor: ${event.actor}`)
    return {
        id: event.id,
        kind: event.kind,
        icon: described.icon,
        severity: described.severity,
        parts: described.parts,
        tag: described.tag ?? null,
        badge: described.badge ?? null,
        detail: described.detail ?? null,
        author,
        time,
        tooltip: lines.join('\n'),
    }
}
```


- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/utils/timeline/*.test.js src/utils/backgroundWork.test.js
```

Expected: all pass (68 tests in `utils/timeline` in the scratch run).

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add frontend/src/utils/timeline/eventTime.js frontend/src/utils/timeline/eventTime.test.js frontend/src/utils/timeline/settingsDiff.js frontend/src/utils/timeline/settingsDiff.test.js frontend/src/utils/timeline/backgroundBadge.js frontend/src/utils/timeline/backgroundBadge.test.js frontend/src/utils/timeline/sessions.js frontend/src/utils/timeline/sessions.test.js frontend/src/utils/timeline/authorLabel.js frontend/src/utils/timeline/authorLabel.test.js frontend/src/utils/timeline/describeEvent.js frontend/src/utils/timeline/describeEvent.test.js
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
feat(timeline): describe every event kind for the display

Pure modules that decide how one timeline event reads: the wording, icon and severity of
the 19 kinds and of the 15 process.ended reasons, the awaiting_input wording by request
type, the settings.changed diff (provider labels, on/off, default, three changes then "+N
more", reason prefixes), the background-work badge, the author label chosen from actor
and via together (none on estimated or derived rows), and the link rules of a session
mention (hidden, missing and subagent sessions are plain text).

Every field has a fallback, so a malformed or future payload never breaks a row. Strings
from data stay plain strings for the components to render as text. Ordering compares the
fixed-width occurred_at strings so microseconds survive.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 5: Rows (grouping, separators) and scope resolution

**Files:**
- Create: `frontend/src/utils/timeline/rows.js`, `rows.test.js`, `scope.js`, `scope.test.js`

**Interfaces:**
- Consumes (T4): `parseOccurredAt`, `groupHeadIdentity`; existing `utils/date.js` (`formatDayKey`, `formatDaySeparatorLabel`); existing `utils/workspaceIds.js`.
- Produces (used by T8):
  - `rows.js`: `ROW_HEIGHT_ESTIMATES = {day: 40, event: 34, nav: 96}`; `buildTimelineRows(events, {sessions, scope, isSubagent, dayKeyOf, dayLabelOf}) -> Array<DayRow | EventRow>` with `DayRow = {key: 'daysep-<dayKey>', type: 'day', dayKey, label}` and `EventRow = {key: 'event-<id>', type: 'event', event, instance, head, indented, identity}`; `estimateRowHeight(row)`; `isNearEnd(rowCount, visibleEndIndex, threshold = 10)`.
  - `scope.js`: `resolveTimelineScope({pageId, allProjectsId, projectsLoaded, workspaceOf, projectOf, projectScopeIds}) -> {state: 'ready'|'loading'|'empty'|'not-found', kind: 'global'|'workspace'|'project'|'worktree', projectId, projectIds}`. `projectIds` is `[]` for the global scope (the server's convention), `null` when no request must be sent.

- [ ] **Step 1: Write the failing tests**

The rows suite sets `process.env.TZ = 'Europe/Paris'` on its first line so the local-day tests cross midnight and both daylight-saving changes.

`frontend/src/utils/timeline/rows.test.js` (new file):

```javascript
// The local-day tests need a time zone with a daylight-saving change.
process.env.TZ = 'Europe/Paris'

import assert from 'node:assert/strict'
import test from 'node:test'

import { formatDaySeparatorLabel } from '../date.js'
import { ROW_HEIGHT_ESTIMATES, buildTimelineRows, estimateRowHeight, isNearEnd } from './rows.js'

const GLOBAL = { kind: 'global' }
let nextId = 1
const ev = (occurred_at, session_id = 's1', fields = {}) => ({
    id: nextId++, kind: 'turn.started', occurred_at, session_id, project_id: 'p1', provider: 'codex', data: {}, ...fields,
})
const build = (events, extra = {}) => buildTimelineRows(events, { sessions: {}, scope: GLOBAL, ...extra })
const shape = (rows) => rows.map((row) => (row.type === 'day' ? `day:${row.dayKey}` : `${row.head ? 'H' : row.indented ? 'i' : row.instance ? 'I' : '-'}:${row.event.session_id ?? 'none'}`))

test('the list starts with a separator for the newest day and every row has a stable key', () => {
    const events = [ev('2026-10-14T12:00:00.000000Z'), ev('2026-10-14T11:00:00.000000Z')]
    const rows = build(events)
    assert.deepEqual(rows.map((row) => row.key), ['daysep-2026-10-14', `event-${events[0].id}`, `event-${events[1].id}`])
    assert.equal(rows[0].type, 'day')
})

test('an empty list has no rows', () => {
    assert.deepEqual(build([]), [])
})

test('the group head carries the identity once; the following rows of the session are indented', () => {
    const rows = build([
        ev('2026-10-14T12:00:03.000000Z', 'a'), ev('2026-10-14T12:00:02.000000Z', 'a'), ev('2026-10-14T12:00:01.000000Z', 'a'),
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'i:a', 'i:a'])
    assert.ok(rows[1].identity && rows[2].identity === null && rows[3].identity === null)
})

test('another session starts a new group, and the first session starts again after it', () => {
    const rows = build([
        ev('2026-10-14T12:00:04.000000Z', 'a'), ev('2026-10-14T12:00:03.000000Z', 'b'),
        ev('2026-10-14T12:00:02.000000Z', 'b'), ev('2026-10-14T12:00:01.000000Z', 'a'),
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'H:b', 'i:b', 'H:a'])
})

test('an instance event is never indented nor a head, and it breaks the surrounding group', () => {
    const rows = build([
        ev('2026-10-14T12:00:03.000000Z', 'a'), ev('2026-10-14T12:00:02.000000Z', null, { kind: 'backend.started', project_id: null }),
        ev('2026-10-14T12:00:01.000000Z', 'a'),
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'I:none', 'H:a'])
    assert.equal(rows[2].identity, null)
})

test('a day separator breaks the group: the row after it is a head again', () => {
    const rows = build([ev('2026-10-14T12:00:00.000000Z', 'a'), ev('2026-10-13T12:00:00.000000Z', 'a')])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'day:2026-10-13', 'H:a'])
})

test('the separator labels the rows BELOW it, one per local day', () => {
    const rows = build([
        ev('2026-10-14T12:00:00.000000Z', 'a'), ev('2026-10-14T08:00:00.000000Z', 'b'), ev('2026-10-12T08:00:00.000000Z', 'c'),
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'H:b', 'day:2026-10-12', 'H:c'])
})

test('the separator label is the chat one (formatDaySeparatorLabel) unless injected', () => {
    const at = '2026-10-14T12:00:00.000000Z'
    assert.equal(build([ev(at)])[0].label, formatDaySeparatorLabel(Date.parse(at)))
    const custom = build([ev(at)], { dayLabelOf: () => 'Today' })
    assert.equal(custom[0].label, 'Today')
})

test('day boundaries follow the local calendar at midnight', () => {
    const rows = build([
        ev('2026-10-24T22:00:00.000000Z'), // 00:00:00 on the 25th in Paris (UTC+2)
        ev('2026-10-24T21:59:59.999999Z'), // 23:59:59 on the 24th
    ])
    assert.deepEqual(rows.filter((row) => row.type === 'day').map((row) => row.dayKey), ['2026-10-25', '2026-10-24'])
})

test('day boundaries hold across the spring daylight-saving change (a 23-hour day)', () => {
    const rows = build([
        ev('2026-03-29T22:30:00.000000Z'), // 00:30 on the 30th (UTC+2)
        ev('2026-03-29T01:30:00.000000Z'), // 03:30 on the 29th, after the jump
        ev('2026-03-29T00:30:00.000000Z'), // 01:30 on the 29th, before the jump
        ev('2026-03-28T23:30:00.000000Z'), // 00:30 on the 29th (UTC+1)
        ev('2026-03-28T22:59:59.000000Z'), // 23:59:59 on the 28th
    ])
    assert.deepEqual(rows.filter((row) => row.type === 'day').map((row) => row.dayKey), ['2026-03-30', '2026-03-29', '2026-03-28'])
})

test('day boundaries hold across the autumn daylight-saving change (a 25-hour day)', () => {
    const rows = build([
        ev('2026-10-25T23:00:00.000000Z'), // 00:00 on the 26th (UTC+1)
        ev('2026-10-25T22:59:59.000000Z'), // 23:59:59 on the 25th
        ev('2026-10-25T01:30:00.000000Z'), // 03:30... still the 25th
        ev('2026-10-24T22:00:00.000000Z'), // 00:00 on the 25th (UTC+2)
    ])
    assert.deepEqual(rows.filter((row) => row.type === 'day').map((row) => row.dayKey), ['2026-10-26', '2026-10-25'])
})

test('a live insertion recomputes heads: a head can become a plain row and the reverse', () => {
    const a1 = ev('2026-10-14T12:00:01.000000Z', 'a')
    const b = ev('2026-10-14T12:00:00.000000Z', 'b')
    assert.deepEqual(shape(build([a1, b])), ['day:2026-10-14', 'H:a', 'H:b'])
    // A back-dated row of session b lands before the existing one: b now has a head and an indented row.
    const b2 = ev('2026-10-14T12:00:02.000000Z', 'b')
    assert.deepEqual(shape(build([b2, a1, b])), ['day:2026-10-14', 'H:b', 'H:a', 'H:b'])
    // A row of a lands between: the b group is split.
    const a2 = ev('2026-10-14T12:00:03.000000Z', 'a')
    assert.deepEqual(shape(build([a2, b2, a1, b])), ['day:2026-10-14', 'H:a', 'H:b', 'H:a', 'H:b'])
    assert.deepEqual(shape(build([b2, b])), ['day:2026-10-14', 'H:b', 'i:b'])
})

test('re-emissions of turn.ended are not collapsed: every event is its own row', () => {
    const rows = build([
        ev('2026-10-14T12:00:03.000000Z', 'a', { kind: 'turn.ended', data: { background_work: null } }),
        ev('2026-10-14T12:00:02.000000Z', 'a', { kind: 'turn.ended', data: { background_work: null } }),
        ev('2026-10-14T12:00:01.000000Z', 'a', { kind: 'turn.ended', data: { background_work: { shells: 1 } } }),
    ])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'i:a', 'i:a'])
})

test('building rows never mutates the events', () => {
    const events = [ev('2026-10-14T12:00:00.000000Z', 'a'), ev('2026-10-14T11:00:00.000000Z', 'a')]
    const copy = JSON.parse(JSON.stringify(events))
    for (const event of events) Object.freeze(event)
    build(events)
    assert.deepEqual(events, copy)
})

test('the keys do not depend on the position: the same event keeps its key', () => {
    const e = ev('2026-10-14T12:00:00.000000Z', 'a')
    const other = ev('2026-10-14T13:00:00.000000Z', 'b')
    assert.equal(build([e]).find((row) => row.type === 'event').key, build([other, e]).find((row) => row.key === `event-${e.id}`).key)
})

test('an unreadable timestamp adds no separator and keeps the current day', () => {
    const rows = build([ev('2026-10-14T12:00:00.000000Z', 'a'), ev('garbage', 'a')])
    assert.deepEqual(shape(rows), ['day:2026-10-14', 'H:a', 'i:a'])
})

test('the head identity follows the scope: project name on a workspace page, not on a worktree page', () => {
    const events = [ev('2026-10-14T12:00:00.000000Z', 'a')]
    assert.equal(build(events, { scope: { kind: 'workspace' } })[1].identity.showProject, true)
    assert.equal(build(events, { scope: { kind: 'worktree', projectId: 'p1' } })[1].identity.showProject, false)
})

test('row height estimates exist per row type; an unknown type uses the event one', () => {
    assert.equal(estimateRowHeight({ type: 'day' }), ROW_HEIGHT_ESTIMATES.day)
    assert.equal(estimateRowHeight({ type: 'event' }), ROW_HEIGHT_ESTIMATES.event)
    assert.equal(estimateRowHeight({ type: 'nav' }), ROW_HEIGHT_ESTIMATES.nav)
    assert.equal(estimateRowHeight({ type: 'mystery' }), ROW_HEIGHT_ESTIMATES.event)
    assert.ok(ROW_HEIGHT_ESTIMATES.day !== ROW_HEIGHT_ESTIMATES.event)
})

test('the next page is requested when the visible range is within ten rows of the end', () => {
    assert.equal(isNearEnd(100, 80), false)
    assert.equal(isNearEnd(100, 90), false)
    assert.equal(isNearEnd(100, 91), true)
    assert.equal(isNearEnd(5, 4), true)
    assert.equal(isNearEnd(0, 0), true)
    assert.equal(isNearEnd(100, 50, 60), true)
})
```


`frontend/src/utils/timeline/scope.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveTimelineScope } from './scope.js'

const ALL = '__all__'
const base = {
    allProjectsId: ALL,
    projectsLoaded: true,
    workspaceOf: (id) => ({ w1: { allProjectIds: ['a', 'a-wt', 'b'] }, empty: { allProjectIds: [] } }[id] ?? null),
    projectOf: (id) => ({ a: { worktree_of: null }, 'a-wt': { worktree_of: 'a' }, b: { worktree_of: null } }[id] ?? null),
    projectScopeIds: (id) => (id === 'a' ? ['a', 'a-wt'] : [id]),
}
const resolve = (pageId, overrides = {}) => resolveTimelineScope({ ...base, pageId, ...overrides })

test('the global page sends no project ids, even before the projects are loaded', () => {
    assert.deepEqual(resolve(ALL), { state: 'ready', kind: 'global', projectId: null, projectIds: [] })
    assert.equal(resolve(ALL, { projectsLoaded: false }).state, 'ready')
})

test('a workspace sends every member and worktree, archived ones included', () => {
    assert.deepEqual(resolve('ws:w1'), { state: 'ready', kind: 'workspace', projectId: null, projectIds: ['a', 'a-wt', 'b'] })
})

test('a workspace with no project is empty and sends nothing, never an empty list', () => {
    const scope = resolve('ws:empty')
    assert.deepEqual([scope.state, scope.projectIds], ['empty', null])
})

test('an unresolved workspace, or projects not loaded yet, is loading and sends nothing', () => {
    assert.deepEqual([resolve('ws:unknown').state, resolve('ws:unknown').projectIds], ['loading', null])
    assert.deepEqual([resolve('ws:w1', { projectsLoaded: false }).state, resolve('ws:w1', { projectsLoaded: false }).projectIds], ['loading', null])
})

test('a project sends itself and its worktrees', () => {
    assert.deepEqual(resolve('a'), { state: 'ready', kind: 'project', projectId: 'a', projectIds: ['a', 'a-wt'] })
})

test('a worktree is a project of its own: it sends only itself and keeps its kind', () => {
    assert.deepEqual(resolve('a-wt'), { state: 'ready', kind: 'worktree', projectId: 'a-wt', projectIds: ['a-wt'] })
})

test('a project page waits for the projects, then reports an unknown project', () => {
    assert.deepEqual([resolve('a', { projectsLoaded: false }).state, resolve('a', { projectsLoaded: false }).projectIds], ['loading', null])
    const missing = resolve('ghost')
    assert.deepEqual([missing.state, missing.projectId, missing.projectIds], ['not-found', 'ghost', null])
})

test('a project page never resolves to an empty list', () => {
    const scope = resolve('b', { projectScopeIds: () => [] })
    assert.deepEqual(scope.projectIds, ['b'])
})
```


- [ ] **Step 2: Run the tests and see them fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/utils/timeline/rows.test.js src/utils/timeline/scope.test.js
```

Expected: both fail with `ERR_MODULE_NOT_FOUND`.

- [ ] **Step 3: Write the modules**

`frontend/src/utils/timeline/rows.js` (new file):

```javascript
// frontend/src/utils/timeline/rows.js
//
// The rows of the list, derived from the ordered events at render time (display design section 5.2):
// day separators, group heads and indentation. Nothing here is ever stored on the events, so a live
// insertion or a page append recomputes it and a row's head status can change. Pure.

import { formatDayKey, formatDaySeparatorLabel } from '../date.js'
import { parseOccurredAt } from './eventTime.js'
import { groupHeadIdentity } from './sessions.js'

/** Estimated heights (px) the scroller starts from, per row type, before it measures. */
export const ROW_HEIGHT_ESTIMATES = { day: 40, event: 34, nav: 96 }

/**
 * @typedef {Object} DayRow
 * @property {string} key - `daysep-<dayKey>`
 * @property {'day'} type
 * @property {string} dayKey
 * @property {string} label
 *
 * @typedef {Object} EventRow
 * @property {string} key - `event-<id>`
 * @property {'event'} type
 * @property {Object} event
 * @property {boolean} instance - A `backend.*` event: no session, never indented, never a head.
 * @property {boolean} head - First event row of its group: it carries the session identity.
 * @property {boolean} indented - A following row of a group.
 * @property {Object|null} identity - `groupHeadIdentity` for a head, else null.
 */

/**
 * @param {Array<Object>} events - Newest first (`occurred_at` descending, then `id` descending).
 * @param {Object} options
 * @param {Object<string, Object>} options.sessions - The side table.
 * @param {{kind: string, projectId?: string|null}} options.scope
 * @param {(id: string) => boolean} [options.isSubagent]
 * @param {(ms: number) => string} [options.dayKeyOf] - Local calendar day of an instant.
 * @param {(ms: number) => string} [options.dayLabelOf]
 * @returns {Array<DayRow|EventRow>}
 */
export function buildTimelineRows(events, { sessions, scope, isSubagent, dayKeyOf = formatDayKey, dayLabelOf = formatDaySeparatorLabel }) {
    const rows = []
    let currentDay = null
    let groupSession = null
    for (const event of events) {
        const ms = parseOccurredAt(event.occurred_at)
        const dayKey = Number.isFinite(ms) ? dayKeyOf(ms) : null
        if (dayKey !== null && dayKey !== currentDay) {
            // A separator labels the rows BELOW it and breaks the group.
            rows.push({ key: `daysep-${dayKey}`, type: 'day', dayKey, label: dayLabelOf(ms) })
            currentDay = dayKey
            groupSession = null
        }
        const instance = !event.session_id
        const sameGroup = !instance && event.session_id === groupSession
        const head = !instance && !sameGroup
        groupSession = instance ? null : event.session_id
        rows.push({
            key: `event-${event.id}`,
            type: 'event',
            event,
            instance,
            head,
            indented: sameGroup,
            identity: head ? groupHeadIdentity(event, sessions, scope, { isSubagent }) : null,
        })
    }
    return rows
}

/**
 * @param {DayRow|EventRow|{type: 'nav'}} row
 * @returns {number} The height the scroller assumes until it measures the row.
 */
export function estimateRowHeight(row) {
    return ROW_HEIGHT_ESTIMATES[row.type] ?? ROW_HEIGHT_ESTIMATES.event
}

/**
 * Whether the list should ask for the next page: the visible range is within `threshold` rows of the end.
 *
 * @param {number} rowCount
 * @param {number} visibleEndIndex
 * @param {number} [threshold=10]
 * @returns {boolean}
 */
export function isNearEnd(rowCount, visibleEndIndex, threshold = 10) {
    return rowCount - visibleEndIndex < threshold
}
```


`frontend/src/utils/timeline/scope.js` (new file):

```javascript
// frontend/src/utils/timeline/scope.js
//
// Which events a Timeline page asks for (display design section 4). Pure: the page hands in plain facts
// read from its stores, and gets back what to show and which project ids to send.

import { extractWorkspaceId, isWorkspaceProjectId } from '../workspaceIds.js'

/**
 * @typedef {Object} TimelineScope
 * @property {'ready'|'loading'|'empty'|'not-found'} state
 *   `loading`: the projects or the workspace are not known yet (no request). `empty`: a workspace with no
 *   project (no request). `not-found`: a project id that does not exist (no request).
 * @property {'global'|'workspace'|'project'|'worktree'} kind
 * @property {string|null} projectId - The project of a `project` or `worktree` page.
 * @property {string[]|null} projectIds - What `GET /api/timeline/` is sent: `[]` is the global scope (the
 *   server's convention), `null` means no request. A workspace or project scope never sends an empty list.
 */

/**
 * @param {Object} facts
 * @param {string} facts.pageId - The panel's id: `allProjectsId`, `ws:<id>` or a project id.
 * @param {string} facts.allProjectsId
 * @param {boolean} facts.projectsLoaded
 * @param {(workspaceId: string) => ({allProjectIds: string[]}|null)} facts.workspaceOf - The workspace with
 *   EVERY member and worktree, archived ones included (the "show archived" setting does not apply).
 * @param {(projectId: string) => ({worktree_of?: (string|null)}|null)} facts.projectOf
 * @param {(projectId: string) => string[]} facts.projectScopeIds - The project and its worktrees.
 * @returns {TimelineScope}
 */
export function resolveTimelineScope({ pageId, allProjectsId, projectsLoaded, workspaceOf, projectOf, projectScopeIds }) {
    if (pageId === allProjectsId) return { state: 'ready', kind: 'global', projectId: null, projectIds: [] }
    if (isWorkspaceProjectId(pageId)) {
        const workspace = workspaceOf(extractWorkspaceId(pageId))
        if (!projectsLoaded || !workspace) return { state: 'loading', kind: 'workspace', projectId: null, projectIds: null }
        if (!workspace.allProjectIds.length) return { state: 'empty', kind: 'workspace', projectId: null, projectIds: null }
        return { state: 'ready', kind: 'workspace', projectId: null, projectIds: [...workspace.allProjectIds] }
    }
    if (!projectsLoaded) return { state: 'loading', kind: 'project', projectId: pageId, projectIds: null }
    const project = projectOf(pageId)
    if (!project) return { state: 'not-found', kind: 'project', projectId: pageId, projectIds: null }
    const scopeIds = projectScopeIds(pageId)
    return {
        state: 'ready',
        kind: project.worktree_of ? 'worktree' : 'project',
        projectId: pageId,
        // An empty list would mean "every project" to the server: a project page always names itself.
        projectIds: scopeIds.length ? scopeIds : [pageId],
    }
}
```


- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/utils/timeline/*.test.js
```

Expected: all pass (95 tests in the scratch run).

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add frontend/src/utils/timeline/rows.js frontend/src/utils/timeline/rows.test.js frontend/src/utils/timeline/scope.js frontend/src/utils/timeline/scope.test.js
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
feat(timeline): group events into rows and resolve the page scope

buildTimelineRows derives, from the ordered events, the day separators (local calendar
days, the same label as the chat, one at the top of the list, breaking the group), the
group heads that carry the session identity once, the indented following rows and the
instance rows that break a group. Nothing is stored on the events, so a live insertion
recomputes heads and splits groups. Keys are stable (event-<id>, daysep-<day>).

resolveTimelineScope turns the page (global, workspace, project, worktree) into the
project ids to request, or into a loading, empty or not-found state that sends nothing:
a workspace never sends an empty list, and an unknown project is reported.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 6: The feed controller (queue, cursors, insertion)

**Files:**
- Create: `frontend/src/utils/timeline/feed.js`, `feed.test.js`

**Interfaces:**
- Consumes (T4): `compareNewestFirst`, `cursorOf`. The response shape of T2 (injected as `fetchPage`).
- Produces (used by T8, T10): in `feed.js`: `createFeedState() -> FeedState` (`status: 'idle'|'loading'|'ready'|'error'`, `events`, `sessions`, `hasMore`, `liveCursor`, `pageCursor`, `loadingMore`, `loadError: {phase: 'first'|'before', error} | null`); `buildTimelineUrl({projectIds, before, afterId, limit, provider}) -> string`; `mergeEvents(list, incoming, {hasMore, live}) -> list`; `createTimelineFeed({fetchPage, state, onLiveError}) -> {state, setScope(projectIds | null), loadMore(), retry(), notifyLatest(latestId), refresh(), idle(), dispose()}`. `fetchPage({projectIds, before, afterId})` returns the response body and rejects on failure.

- [ ] **Step 1: Write the failing tests**

Beyond the rules of the Global Constraints, it pins what a scoped feed needs (`latest_id` is global, so the live cursor must follow it even when no row of the response is in scope), a signal while the first page failed (nothing happens, Retry reloads), that a disposed feed ignores every call and keeps its state as it was, and that a next page queued behind a live fetch that comes back truncated is dropped (the reset left no cursor, and the first page it queued follows: sending `before: null` would fetch the first page twice). The suite runs the controller against an in-memory server with the semantics of the endpoint. The server computes a response when the request STARTS (the SQL read) and delivers it when a gate opens, so a test can commit a row in between: that is how the queue race is reproduced.

`frontend/src/utils/timeline/feed.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import { compareNewestFirst } from './eventTime.js'
import { buildTimelineUrl, createFeedState, createTimelineFeed, mergeEvents } from './feed.js'

const PAGE = 2

/** Let the queue start the task it holds: a request reads the server when it STARTS. */
const started = () => new Promise((resolve) => setImmediate(resolve))

/** Time `n` seconds after the origin, in the wire format. */
const at = (n, micro = 0) => `2026-10-14T10:${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}.${String(micro).padStart(6, '0')}Z`
let counter = 0
const event = (id, time, fields = {}) => ({ id, occurred_at: time, kind: 'turn.started', session_id: 's1', project_id: 'p1', ...fields })

/**
 * A server in memory with the semantics of GET /api/timeline/. The response is computed when the request
 * STARTS (the SQL read) and delivered when the gate opens, so a test can commit a row in between.
 */
function createServer(initial = [], { pageSize = PAGE } = {}) {
    const table = [...initial]
    const calls = []
    const held = []
    let holding = false
    const inScope = (e, projectIds) => !projectIds.length || projectIds.includes(e.project_id)
    function compute({ projectIds, before, afterId }) {
        const latest_id = table.length ? Math.max(...table.map((e) => e.id)) : null
        const sessions = { s1: { title: `T${++counter}`, project_id: 'p1', provider: 'codex', hidden: false, archived: false } }
        if (afterId !== null) {
            const rows = table.filter((e) => e.id > afterId && inScope(e, projectIds)).sort((a, b) => a.id - b.id)
            return { events: rows.slice(0, 500), has_more: false, truncated: rows.length > 500, latest_id, sessions }
        }
        let rows = table.filter((e) => inScope(e, projectIds)).sort(compareNewestFirst)
        if (before) {
            const [time, id] = before.split(',')
            rows = rows.filter((e) => compareNewestFirst(e, { occurred_at: time, id: Number(id) }) > 0)
        }
        const page = rows.slice(0, pageSize + 1)
        return { events: page.slice(0, pageSize), has_more: page.length > pageSize, truncated: false, latest_id, sessions }
    }
    return {
        table,
        calls,
        commit(e) {
            table.push(e)
        },
        hold() {
            holding = true
        },
        /** Deliver every held response (in order), then stop holding. */
        release() {
            holding = false
            for (const resolve of held.splice(0)) resolve()
        },
        async fetchPage(params) {
            calls.push({ ...params })
            const body = compute(params)
            if (holding) await new Promise((resolve) => held.push(resolve))
            return JSON.parse(JSON.stringify(body))
        },
        failNext(error = new Error('boom')) {
            const original = this.fetchPage
            this.fetchPage = async (params) => {
                this.fetchPage = original
                calls.push({ ...params })
                throw error
            }
        },
    }
}

function setup(initial = [], options) {
    const server = createServer(initial, options)
    const liveErrors = []
    const feed = createTimelineFeed({
        fetchPage: (params) => server.fetchPage(params),
        state: createFeedState(),
        onLiveError: (error) => liveErrors.push(error),
    })
    return { server, feed, state: feed.state, liveErrors }
}

const ids = (state) => state.events.map((e) => e.id)
const kinds = (server) => server.calls.map((c) => (c.afterId !== null ? `after:${c.afterId}` : c.before ? 'before' : 'first'))

// Ids 1..5, increasing in time: the newest is 5.
const five = () => [1, 2, 3, 4, 5].map((id) => event(id, at(id)))

test('the first page fills the list newest first with the cursors and the side table', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    assert.equal(state.status, 'loading')
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4])
    assert.equal(state.status, 'ready')
    assert.equal(state.hasMore, true)
    assert.equal(state.liveCursor, 5)
    assert.equal(state.pageCursor, `${at(4)},4`)
    assert.ok(state.sessions.s1.title.startsWith('T'))
    assert.deepEqual(kinds(server), ['first'])
})

test('a scope that cannot be loaded yet sends nothing and stays idle', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope(null)
    await feed.idle()
    assert.deepEqual([state.status, server.calls.length], ['idle', 0])
    feed.notifyLatest(9)
    feed.refresh()
    feed.loadMore()
    await feed.idle()
    assert.equal(server.calls.length, 0)
})

test('loadMore pages with the last fetched row as cursor and stops at the end', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    feed.loadMore()
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4, 3, 2])
    assert.equal(server.calls[1].before, `${at(4)},4`)
    feed.loadMore()
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4, 3, 2, 1])
    assert.equal(state.hasMore, false)
    feed.loadMore()
    await feed.idle()
    assert.equal(server.calls.length, 3, 'no request at the end of the list')
})

test('loading is not re-entrant: repeated loadMore calls make one request', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.hold()
    feed.loadMore()
    feed.loadMore()
    feed.loadMore()
    assert.equal(state.loadingMore, true)
    server.release()
    await feed.idle()
    assert.equal(server.calls.filter((c) => c.before).length, 1)
    assert.equal(state.loadingMore, false)
})

test('loadMore while the first page loads does nothing', async () => {
    const { server, feed } = setup(five())
    server.hold()
    feed.setScope([])
    feed.loadMore()
    server.release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first'])
})

test('a before page never advances the live cursor', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(9, at(9)))
    feed.loadMore()
    await feed.idle()
    assert.equal(server.calls[1].before !== null, true)
    assert.equal(state.liveCursor, 5, 'the before response carried latest_id 9, the cursor stays')
})

test('the live cursor takes the latest_id of an after_id response even when its only row is dropped', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    assert.deepEqual([state.hasMore, state.liveCursor], [true, 5])
    // Row 9 is back-dated past the loaded tail: with older pages to come it is not inserted (the page that
    // reaches it brings it), yet the cursor must move on, or every later signal would fetch it again.
    server.commit(event(9, at(0)))
    feed.notifyLatest(9)
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4])
    assert.equal(state.liveCursor, 9)
    feed.notifyLatest(9)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5'], 'nothing left to fetch')
    feed.loadMore()
    feed.loadMore()
    await feed.idle()
    feed.loadMore()
    await feed.idle()
    assert.ok(ids(state).includes(9), 'the page that reaches it brings it')
})

test('the has_more of an after_id response is ignored: the list keeps its own', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    assert.equal(state.hasMore, true)
    const original = server.fetchPage
    server.fetchPage = async (params) => {
        server.fetchPage = original
        server.calls.push({ ...params })
        return { events: [event(6, at(6))], has_more: false, truncated: false, latest_id: 6, sessions: {} }
    }
    feed.notifyLatest(6)
    await feed.idle()
    assert.equal(state.hasMore, true, 'a live response said false: the older pages are still there')
    feed.loadMore()
    await feed.idle()
    assert.deepEqual(ids(state), [6, 5, 4, 3, 2])
})

test('the rows of an after_id response arrive in ascending id order and are sorted into the list', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(8, at(8)))
    server.commit(event(6, at(6)))
    server.commit(event(7, at(7)))
    feed.notifyLatest(8)
    await feed.idle()
    assert.deepEqual(ids(state), [8, 7, 6, 5, 4])
})

test('a next page queued behind a live fetch that comes back truncated is dropped: no wasted first-page request', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const original = server.fetchPage
    let release
    server.fetchPage = async (params) => {
        server.fetchPage = original
        server.calls.push({ ...params })
        await new Promise((resolve) => { release = resolve })
        return { events: [event(99, at(50))], has_more: false, truncated: true, latest_id: 700, sessions: {} }
    }
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    await started()
    feed.loadMore() // queued behind the live fetch
    release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5', 'first'], 'the before page of the dead list never went out')
    assert.deepEqual([state.status, state.loadingMore, state.loadError], ['ready', false, null])
    assert.deepEqual(ids(state), [6, 5])
})

test('the live cursor is the latest_id of the response, not the id of the newest event', async () => {
    // Row 9 is the newest row written, but back-dated: it sits at the far end of the feed.
    const { server, feed, state } = setup([...five(), event(9, at(0))])
    feed.setScope([])
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4])
    assert.equal(state.liveCursor, 9)
    feed.notifyLatest(9)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first'])
})

test('a signal with a known latest id does nothing', async () => {
    const { server, feed } = setup(five())
    feed.setScope([])
    await feed.idle()
    feed.notifyLatest(5)
    feed.notifyLatest(3)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first'])
})

test('a new id triggers one after_id fetch from the live cursor and advances it', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5'])
    assert.deepEqual(ids(state), [6, 5, 4])
    assert.equal(state.liveCursor, 6)
})

test('signals during a live fetch coalesce into exactly one follow-up fetch', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(6, at(6)))
    server.hold()
    feed.notifyLatest(6)
    await started()
    server.commit(event(7, at(7)))
    feed.notifyLatest(7)
    server.commit(event(8, at(8)))
    feed.notifyLatest(8)
    feed.notifyLatest(8)
    server.release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5', 'after:6'])
    assert.deepEqual(ids(state), [8, 7, 6, 5, 4])
    assert.equal(state.liveCursor, 8)
})

test('fetched rows are inserted by (occurred_at, id), duplicates are ignored, a back-dated row lands in place', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(6, at(2, 500)))  // back-dated: older than the loaded range [5, 4] ...
    server.commit(event(7, at(4, 500)))  // ... and this one falls between 5 and 4
    feed.notifyLatest(7)
    await feed.idle()
    // Row 6 is older than the tail while pages remain: left to the page that reaches it.
    assert.deepEqual(ids(state), [5, 7, 4])
    feed.loadMore()
    await feed.idle()
    assert.deepEqual(ids(state), [5, 7, 4, 3, 6], 'the page brings the back-dated row, in its place')
})

test('a live row older than the loaded tail is not inserted while has_more is true, and inserted once has_more is false', () => {
    const list = [event(5, at(5)), event(4, at(4))]
    const old = event(9, at(1))
    assert.deepEqual(mergeEvents(list, [old], { hasMore: true, live: true }), list)
    assert.deepEqual(mergeEvents(list, [old], { hasMore: false, live: true }).map((e) => e.id), [5, 4, 9])
    // The same row from a before page is always accepted.
    assert.deepEqual(mergeEvents(list, [old], { hasMore: true, live: false }).map((e) => e.id), [5, 4, 9])
})

test('mergeEvents ignores a duplicate id and keeps the same array when nothing is added', () => {
    const list = [event(5, at(5))]
    assert.equal(mergeEvents(list, [event(5, at(5))], { hasMore: false, live: true }), list)
    const merged = mergeEvents(list, [event(6, at(6)), event(6, at(6))], { hasMore: false, live: true })
    assert.deepEqual(merged.map((e) => e.id), [6, 5])
})

test('mergeEvents orders equal times by id and keeps microseconds', () => {
    const list = [event(2, at(1, 100))]
    const merged = mergeEvents(list, [event(3, at(1, 100)), event(1, at(1, 900))], { hasMore: false, live: true })
    assert.deepEqual(merged.map((e) => e.id), [1, 3, 2])
})

test('the pagination cursor stays the last fetched row after live insertions', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const cursorBefore = state.pageCursor
    server.commit(event(6, at(6)))
    server.commit(event(7, at(7)))
    feed.notifyLatest(7)
    await feed.idle()
    assert.equal(state.pageCursor, cursorBefore)
    feed.loadMore()
    await feed.idle()
    assert.equal(server.calls.at(-1).before, cursorBefore)
    assert.deepEqual(ids(state), [7, 6, 5, 4, 3, 2])
})

test('truncated: the list and the cursors are discarded and the first page is reloaded', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    feed.loadMore()
    await feed.idle()
    const original = server.fetchPage
    server.fetchPage = async (params) => {
        server.fetchPage = original
        server.calls.push({ ...params })
        return { events: [event(99, at(50))], has_more: false, truncated: true, latest_id: 700, sessions: {} }
    }
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'before', 'after:5', 'first'])
    assert.deepEqual(ids(state), [6, 5], 'the truncated rows are not used')
    assert.equal(state.liveCursor, 6, 'the live cursor never took the truncated latest_id')
    assert.equal(state.status, 'ready')
})

test('a signal during the first page is deferred and replayed after it', async () => {
    const { server, feed, state } = setup(five())
    server.hold()
    feed.setScope([])
    await started()
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    server.release()
    await feed.idle()
    // The first page was read before row 6 existed (latest_id 5): the replay fetches it.
    assert.deepEqual(kinds(server), ['first', 'after:5'])
    assert.deepEqual(ids(state), [6, 5, 4])
})

test('a deferred signal that the first page already covers is not replayed', async () => {
    const { server, feed } = setup(five())
    server.hold()
    feed.setScope([])
    await started()
    feed.notifyLatest(5)
    server.release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first'])
})

test('a null live cursor on an empty feed counts as 0: the first event ever recorded is fetched', async () => {
    const { server, feed, state } = setup([])
    feed.setScope([])
    await feed.idle()
    assert.deepEqual([state.liveCursor, state.status, state.events.length], [null, 'ready', 0])
    server.commit(event(1, at(1)))
    feed.notifyLatest(1)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:0'])
    assert.deepEqual(ids(state), [1])
    assert.equal(state.liveCursor, 1)
})

test('the queue race: a row committed after a before query read but inside the new range is not lost', async () => {
    // Loaded [10, 9], pages remain. The before page below reads rows 8, 7. Row 20 is then committed with a
    // time between them (back-dated), which the before snapshot cannot contain.
    const rows = [event(10, at(10)), event(9, at(9)), event(8, at(8)), event(7, at(7)), event(6, at(6))]
    const { server, feed, state } = setup(rows)
    feed.setScope([])
    await feed.idle()
    assert.deepEqual(ids(state), [10, 9])
    server.hold()
    feed.loadMore()
    await started()
    server.commit(event(20, at(7, 500)))
    feed.notifyLatest(20)
    server.release()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'before', 'after:10'])
    assert.deepEqual(ids(state), [10, 9, 8, 20, 7], 'row 20 is inside the loaded range once the page landed')
})

test('a project-id set change reloads the first page; the same set in another order does not', async () => {
    const { server, feed, state } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: 'b' }), event(3, at(3), { project_id: 'c' })])
    feed.setScope(['a', 'b'])
    await feed.idle()
    assert.deepEqual(ids(state), [2, 1])
    feed.setScope(['b', 'a'])
    await feed.idle()
    assert.equal(server.calls.length, 1)
    feed.setScope(['a', 'b', 'c'])
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'first'])
    assert.deepEqual(ids(state), [3, 2])
    assert.deepEqual(server.calls[1].projectIds, ['a', 'b', 'c'])
})

test('every after_id fetch uses the current set', async () => {
    const { server, feed } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: 'b' })])
    feed.setScope(['a'])
    await feed.idle()
    feed.setScope(['a', 'b'])
    await feed.idle()
    server.commit(event(3, at(3), { project_id: 'b' }))
    feed.notifyLatest(3)
    await feed.idle()
    assert.deepEqual(server.calls.at(-1), { projectIds: ['a', 'b'], before: null, afterId: 2 })
})

test('a response for a set that is no longer current is ignored, and its queue is dropped', async () => {
    const { server, feed, state } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: 'b' })])
    server.hold()
    feed.setScope(['a'])
    await started()
    feed.notifyLatest(5)
    feed.setScope(['b'])
    server.release()
    await feed.idle()
    assert.deepEqual(ids(state), [2], 'only the data of the current set')
    assert.deepEqual(server.calls.map((c) => c.projectIds), [['a'], ['b']], 'the queued live fetch of the old set never ran')
})

test('going back to no scope clears the list', async () => {
    const { feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    feed.setScope(null)
    assert.deepEqual([state.status, state.events.length, state.hasMore, state.liveCursor, state.pageCursor], ['idle', 0, false, null, null])
})

test('a failed first page shows the error and Retry replays it', async () => {
    const { server, feed, state } = setup(five())
    server.failNext()
    feed.setScope([])
    await feed.idle()
    assert.equal(state.status, 'error')
    assert.equal(state.loadError.phase, 'first')
    feed.retry()
    assert.equal(state.status, 'loading')
    assert.equal(state.loadError, null)
    await feed.idle()
    assert.deepEqual([state.status, ids(state)], ['ready', [5, 4]])
})

test('a failed before page stops the automatic loading until Retry', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.failNext()
    feed.loadMore()
    await feed.idle()
    assert.equal(state.loadError.phase, 'before')
    assert.equal(state.loadingMore, false)
    assert.equal(state.status, 'ready')
    feed.loadMore()
    await feed.idle()
    assert.equal(server.calls.filter((c) => c.before).length, 1, 'no automatic retry')
    feed.retry()
    await feed.idle()
    assert.deepEqual(ids(state), [5, 4, 3, 2])
    assert.equal(state.loadError, null)
})

test('refresh (first OPEN, reconnect, tab return) catches up from the live cursor and coalesces', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(6, at(6)))
    feed.refresh()
    feed.refresh()
    feed.refresh()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5'])
    assert.deepEqual(ids(state), [6, 5, 4])
})

test('refresh with nothing new still fetches once (the signal may have been missed)', async () => {
    const { server, feed } = setup(five())
    feed.setScope([])
    await feed.idle()
    feed.refresh()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:5'])
})

test('refresh with no list reloads the first page', async () => {
    const { server, feed, state } = setup(five())
    server.failNext()
    feed.setScope([])
    await feed.idle()
    feed.refresh()
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'first'])
    assert.equal(state.status, 'ready')
})

test('a live fetch that fails is silent and the next signal retries it', async () => {
    const { server, feed, state, liveErrors } = setup(five())
    feed.setScope([])
    await feed.idle()
    server.commit(event(6, at(6)))
    server.failNext(new Error('offline'))
    feed.notifyLatest(6)
    await feed.idle()
    assert.equal(liveErrors.length, 1)
    assert.deepEqual([state.status, state.loadError, state.liveCursor], ['ready', null, 5])
    feed.notifyLatest(6)
    await feed.idle()
    assert.deepEqual(ids(state), [6, 5, 4])
})

test('a later response overwrites the side-table entry of a session', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const first = state.sessions.s1.title
    server.commit(event(6, at(6)))
    feed.notifyLatest(6)
    await feed.idle()
    assert.notEqual(state.sessions.s1.title, first)
})

test('an empty page ends the list even if has_more says otherwise', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const original = server.fetchPage
    server.fetchPage = async () => {
        server.fetchPage = original
        return { events: [], has_more: true, truncated: false, latest_id: 5, sessions: {} }
    }
    feed.loadMore()
    await feed.idle()
    assert.equal(state.hasMore, false)
})

test('a disposed feed ignores every call and keeps its state as it was', async () => {
    const { server, feed, state } = setup(five())
    feed.setScope([])
    await feed.idle()
    const before = { ...state }
    feed.dispose()
    feed.loadMore()
    feed.notifyLatest(9)
    feed.refresh()
    feed.retry()
    feed.setScope(['x'])
    await feed.idle()
    assert.equal(server.calls.length, 1, 'only the first page of before the dispose')
    assert.deepEqual({ ...state }, before, 'no flag, list or cursor moved (loadingMore included)')
})

test('a feed disposed with a request in flight drops its answer', async () => {
    const { server, feed, state } = setup(five())
    server.hold()
    feed.setScope([])
    await started()
    feed.dispose()
    server.release()
    await feed.idle()
    assert.deepEqual([state.status, state.events.length], ['loading', 0])
})

test('a signal while the first page failed does nothing: the Retry action reloads', async () => {
    const { server, feed, state } = setup(five())
    server.failNext()
    feed.setScope([])
    await feed.idle()
    assert.equal(state.status, 'error')
    feed.notifyLatest(9)
    await feed.idle()
    assert.deepEqual([server.calls.length, state.status], [1, 'error'])
    feed.retry()
    await feed.idle()
    assert.deepEqual([server.calls.length, state.status], [2, 'ready'])
})

test('in a scoped feed the live cursor follows latest_id, which is global, even when no row is in scope', async () => {
    const { server, feed, state } = setup([event(1, at(1), { project_id: 'a' }), event(2, at(2), { project_id: 'a' })])
    feed.setScope(['a'])
    await feed.idle()
    assert.equal(state.liveCursor, 2)
    server.commit(event(3, at(3), { project_id: 'b' }))
    feed.notifyLatest(3)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:2'])
    assert.deepEqual(ids(state), [2, 1], 'nothing in scope')
    assert.equal(state.liveCursor, 3)
    feed.notifyLatest(3)
    await feed.idle()
    assert.deepEqual(kinds(server), ['first', 'after:2'], 'the same signal fetches nothing more')
})

test('buildTimelineUrl encodes the cursor and keeps after_id 0', () => {
    assert.equal(buildTimelineUrl({}), '/api/timeline/')
    assert.equal(buildTimelineUrl({ projectIds: ['a', 'b'] }), '/api/timeline/?project_ids=a%2Cb')
    assert.equal(buildTimelineUrl({ afterId: 0 }), '/api/timeline/?after_id=0')
    const url = buildTimelineUrl({ projectIds: ['-p'], before: '2026-10-14T10:00:00.123456Z,42', limit: 50 })
    assert.equal(url, '/api/timeline/?project_ids=-p&limit=50&before=2026-10-14T10%3A00%3A00.123456Z%2C42')
    assert.equal(new URL(`http://x${url}`).searchParams.get('before'), '2026-10-14T10:00:00.123456Z,42')
    assert.equal(buildTimelineUrl({ before: 'a+b,1' }), '/api/timeline/?before=a%2Bb%2C1')
})
```


- [ ] **Step 2: Run the tests and see them fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/utils/timeline/feed.test.js
```

Expected: `ERR_MODULE_NOT_FOUND` for `feed.js`.

- [ ] **Step 3: Write the controller**

`frontend/src/utils/timeline/feed.js` (new file):

```javascript
// frontend/src/utils/timeline/feed.js
//
// The data side of the Timeline page: the list of events, the cursors and the single fetch queue
// (display design section 9.2). Pure: the network call is injected (`fetchPage`) and the state object
// is owned by the caller (a `reactive()` object in the app, a plain one in the tests).
//
// The rules this module keeps, each pinned by feed.test.js:
//   * ONE queue serializes every list fetch (first page, `before` pages, `after_id` fetches). A live
//     fetch therefore applies its insertion rules against the tail and `has_more` that the pending load
//     left behind. Signals that arrive meanwhile coalesce into one follow-up fetch.
//   * The LIVE CURSOR (highest id surely received from the head) advances only on a first-page or an
//     `after_id` response, to its `latest_id`, never on a `before` response and never on a truncated one.
//     A null cursor counts as 0.
//   * The PAGINATION CURSOR is the last row of the last fetched page, never the tail after live inserts.
//   * Fetched rows are INSERTED by (occurred_at, id) descending. A live row older than the loaded tail is
//     not inserted while `has_more` is true: the page that reaches it brings it.
//   * A change of the project-id SET discards list, cursors and queue; a response of an older set is ignored.

import { compareNewestFirst, cursorOf } from './eventTime.js'

/**
 * @typedef {Object} FeedState
 * @property {'idle'|'loading'|'ready'|'error'} status - State of the first page. `idle`: no scope to load.
 * @property {Array<Object>} events - Newest first.
 * @property {Object<string, Object>} sessions - The side table; a later response overwrites an entry.
 * @property {boolean} hasMore - More events exist older than the pagination cursor.
 * @property {number|null} liveCursor - Highest id surely received from the head; null counts as 0.
 * @property {string|null} pageCursor - `<occurred_at>,<id>` of the last row of the last fetched page.
 * @property {boolean} loadingMore - A `before` page is queued or running.
 * @property {{phase: 'first'|'before', error: *}|null} loadError - The failure the Retry action replays.
 */

/** @returns {FeedState} */
export function createFeedState() {
    return {
        status: 'idle', events: [], sessions: {}, hasMore: false, liveCursor: null, pageCursor: null,
        loadingMore: false, loadError: null,
    }
}

/**
 * URL of one request. `URLSearchParams` percent-encodes the cursor, so a `+` offset or a `,` survives.
 *
 * @param {Object} params
 * @param {string[]} [params.projectIds] - Empty means the global scope.
 * @param {string|null} [params.before] - `<occurred_at>,<id>`.
 * @param {number|null} [params.afterId]
 * @param {number|null} [params.limit]
 * @param {string|null} [params.provider]
 * @returns {string}
 */
export function buildTimelineUrl({ projectIds = [], before = null, afterId = null, limit = null, provider = null } = {}) {
    const query = new URLSearchParams()
    if (projectIds.length) query.set('project_ids', projectIds.join(','))
    if (provider) query.set('provider', provider)
    if (limit !== null) query.set('limit', String(limit))
    if (before) query.set('before', before)
    if (afterId !== null) query.set('after_id', String(afterId))
    const text = query.toString()
    return `/api/timeline/${text ? `?${text}` : ''}`
}

/**
 * Insert fetched rows into the list by (occurred_at, id) descending.
 *
 * @param {Array<Object>} list - Newest first.
 * @param {Array<Object>} incoming - In any order.
 * @param {{hasMore: boolean, live: boolean}} options - `live`: rows of an `after_id` fetch.
 * @returns {Array<Object>} `list` itself when nothing was added, else a new array.
 */
export function mergeEvents(list, incoming, { hasMore, live }) {
    const known = new Set(list.map((event) => event.id))
    const tail = list.length ? list[list.length - 1] : null
    const accepted = []
    for (const event of incoming) {
        if (known.has(event.id)) continue
        known.add(event.id)
        // Older than what is loaded, with older pages still to come: that page will bring it.
        if (live && hasMore && tail && compareNewestFirst(event, tail) > 0) continue
        accepted.push(event)
    }
    if (!accepted.length) return list
    return [...list, ...accepted].sort(compareNewestFirst)
}

function scopeKeyOf(projectIds) {
    return projectIds === null ? null : [...projectIds].sort().join('\u0000')
}

/**
 * @param {Object} options
 * @param {(params: {projectIds: string[], before: (string|null), afterId: (number|null)}) => Promise<Object>} options.fetchPage
 *   Resolves with the response body of `GET /api/timeline/`; rejects on any failure.
 * @param {FeedState} [options.state]
 * @param {(error: *) => void} [options.onLiveError] - A failed `after_id` fetch is silent: the next signal retries.
 */
export function createTimelineFeed({ fetchPage, state = createFeedState(), onLiveError = () => {} }) {
    let generation = 0
    let chain = Promise.resolve()
    let disposed = false
    let scope = null // string[] (empty = global) or null (nothing to load)
    let scopeKey
    let loadPending = false // a first page or a `before` page is queued or running
    let liveQueued = false
    let liveInfo = { latest: null, force: false }

    const stale = (gen) => disposed || gen !== generation

    function enqueue(task) {
        const gen = generation
        chain = chain
            .then(() => (stale(gen) ? undefined : task(gen)))
            .catch((error) => {
                // A task handles its own failures; this is only a guard so one bug cannot freeze the queue.
                console.error('Timeline feed task failed', error)
            })
    }

    function reset() {
        Object.assign(state, createFeedState())
        loadPending = false
        liveQueued = false
        liveInfo = { latest: null, force: false }
    }

    function mergeSessions(sessions) {
        if (sessions && Object.keys(sessions).length) state.sessions = { ...state.sessions, ...sessions }
    }

    async function runFirst(gen) {
        state.status = 'loading'
        state.loadError = null
        let body
        try {
            body = await fetchPage({ projectIds: scope, before: null, afterId: null })
        } catch (error) {
            if (stale(gen)) return
            state.status = 'error'
            state.loadError = { phase: 'first', error }
            loadPending = false
            return
        }
        if (stale(gen)) return
        state.events = [...body.events].sort(compareNewestFirst)
        state.sessions = { ...state.sessions, ...body.sessions }
        state.hasMore = body.has_more === true
        state.pageCursor = state.events.length ? cursorOf(state.events[state.events.length - 1]) : null
        state.liveCursor = body.latest_id ?? null
        state.status = 'ready'
        loadPending = false
    }

    async function runBefore(gen) {
        // A live fetch that came back truncated reset the list while this page was queued behind it: there is no
        // cursor left, and the first page that the reset queued comes next. Sending `before: null` would only
        // fetch the first page twice.
        if (state.pageCursor === null) return
        state.loadingMore = true
        state.loadError = null
        let body
        try {
            body = await fetchPage({ projectIds: scope, before: state.pageCursor, afterId: null })
        } catch (error) {
            if (stale(gen)) return
            state.loadingMore = false
            state.loadError = { phase: 'before', error }
            loadPending = false
            return
        }
        if (stale(gen)) return
        state.events = mergeEvents(state.events, body.events, { hasMore: false, live: false })
        mergeSessions(body.sessions)
        // The cursor never moves backwards through live rows: it is the last row of THIS page.
        if (body.events.length) state.pageCursor = cursorOf(body.events[body.events.length - 1])
        // An empty page ends the list, whatever `has_more` says: no endless loop on a misbehaving server.
        state.hasMore = body.has_more === true && body.events.length > 0
        state.loadingMore = false
        loadPending = false
    }

    async function runLive(gen) {
        const info = liveInfo
        liveInfo = { latest: null, force: false }
        liveQueued = false
        const cursor = state.liveCursor ?? 0
        if (!info.force && !(info.latest !== null && info.latest > cursor)) return
        let body
        try {
            body = await fetchPage({ projectIds: scope, before: null, afterId: cursor })
        } catch (error) {
            if (!stale(gen)) onLiveError(error)
            return
        }
        if (stale(gen)) return
        if (body.truncated === true) {
            // Too much happened to patch the list: start again from the first page.
            reset()
            state.status = 'loading'
            loadPending = true
            enqueue(runFirst)
            return
        }
        state.events = mergeEvents(state.events, body.events, { hasMore: state.hasMore, live: true })
        mergeSessions(body.sessions)
        if (body.latest_id !== null && body.latest_id !== undefined && body.latest_id > cursor) state.liveCursor = body.latest_id
    }

    function requestLive({ latest = null, force = false }) {
        if (disposed || scope === null) return
        if (latest !== null && (liveInfo.latest === null || latest > liveInfo.latest)) liveInfo.latest = latest
        liveInfo.force = liveInfo.force || force
        if (liveQueued) return
        liveQueued = true
        enqueue(runLive)
    }

    function setScope(projectIds) {
        if (disposed) return
        const key = scopeKeyOf(projectIds)
        if (key === scopeKey) return
        scopeKey = key
        scope = projectIds === null ? null : [...projectIds]
        generation += 1
        chain = Promise.resolve()
        reset()
        if (scope === null) return
        state.status = 'loading'
        loadPending = true
        enqueue(runFirst)
    }

    function loadMore() {
        if (disposed || scope === null) return
        if (state.status !== 'ready' || !state.hasMore || state.loadError || loadPending) return
        loadPending = true
        state.loadingMore = true
        enqueue(runBefore)
    }

    function retry() {
        if (disposed || scope === null || !state.loadError) return
        const { phase } = state.loadError
        state.loadError = null
        if (phase === 'first') {
            state.status = 'loading'
            loadPending = true
            enqueue(runFirst)
        } else {
            loadMore()
        }
    }

    function notifyLatest(latestId) {
        if (disposed || scope === null || state.status === 'idle' || state.status === 'error') return
        requestLive({ latest: latestId })
    }

    function refresh() {
        if (disposed || scope === null) return
        if (state.status === 'error') retry()
        else requestLive({ force: true })
    }

    async function idle() {
        let last
        do {
            last = chain
            await last
        } while (last !== chain)
    }

    function dispose() {
        disposed = true
        generation += 1
    }

    return {
        state,
        /**
         * Point the feed at a project-id set. `[]` is the global scope, `null` means "nothing to load yet or at
         * all" (projects not loaded, empty workspace, unknown project): no request is sent. The SET is compared,
         * not the order: an unchanged set keeps everything.
         *
         * @param {string[]|null} projectIds
         */
        setScope,
        /** Ask for the next page. A no-op while a load is pending, after a failure (use `retry`) or at the end. */
        loadMore,
        /** Replay the failed load (the Retry action). */
        retry,
        /**
         * A `timeline_changed` signal. When the queued live task runs it does nothing if the id is not above the
         * live cursor (null counts as 0); a signal that arrives while the first page loads is replayed after it.
         *
         * @param {number} latestId
         */
        notifyLatest,
        /**
         * The socket reached OPEN after the page loaded, reconnected, or the tab became active again: catch up
         * from the live cursor, or reload the first page when there is no list.
         */
        refresh,
        /** Resolves when the queue is empty (tests). */
        idle,
        dispose,
    }
}
```


- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/utils/timeline/*.test.js
```

Expected: all pass (136 tests in the scratch run).

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add frontend/src/utils/timeline/feed.js frontend/src/utils/timeline/feed.test.js
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
feat(timeline): add the feed controller of the Timeline page

One queue serializes the first page, the before pages and the after_id fetches, so a
live fetch applies its insertion rules against the tail and has_more that a pending load
left behind and a row committed after a before query read is not lost. Signals that
arrive during a fetch coalesce into one follow-up; a signal during the first page is
replayed after it; a null live cursor counts as 0.

The live cursor advances only on a first page or an after_id response (to its latest_id,
never when truncated); the pagination cursor is the last row of the last fetched page.
Fetched rows are inserted by (occurred_at, id) descending, duplicates are ignored, and a
live row older than the loaded tail waits for its page while has_more is true. A change
of the project-id set resets list, cursors and queue and ignores responses of the old set.
The network call and the state object are injected, so the module is plain node-testable.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 7: The event row components

**Files:**
- Create: `frontend/src/components/timeline/TimelineSessionMention.vue`
- Create: `frontend/src/components/timeline/TimelineEventRow.vue`
- Create: `frontend/src/components/timeline/TimelineEventRow.test.js`

**Interfaces:**
- Consumes (T4, T5): `describeEvent`, the `EventRow` of `buildTimelineRows`, a `SessionMention`. Existing: `ProviderIcon.vue`, `ProjectBadge.vue`, `utils/sessionRoute.js` (`sessionRouteLocation`), `stores/data` (`getProject`).
- Produces (used by T8):
  - `TimelineSessionMention.vue`: props `mention` (object, required), `markers` (boolean, default false: archived and hidden markers, group heads only). Link or plain text as `mention.link` says.
  - `TimelineEventRow.vue`: props `row` (an `EventRow`), `context` (`{sessions, labelsFor, isSubagent, time?}`). Root class `timeline-row` with `timeline-row--<severity>`, `timeline-row--indented`, `timeline-row--head`, attribute `data-kind`. Other class hooks: `timeline-row-icon`, `timeline-identity`, `timeline-project`, `timeline-project--plain`, `timeline-wording`, `timeline-tag`, `timeline-badge`, `timeline-author`, `timeline-error`, `timeline-error-line`, `timeline-error-rest`, `timeline-time`, `timeline-time--estimated`, `timeline-session`, `timeline-session-title`, `timeline-session-title--plain`, `timeline-marker`.
  - Every `wa-*` element used is already imported in `main.js` (`wa-icon`, `wa-badge`, `wa-button`): nothing to add there.

- [ ] **Step 1: Write the failing tests**

The tests compile the two `.vue` files with the repo's SFC harness and render them into a plain node tree (no DOM). They check the markers on group heads ONLY (an author label's session and a parent carry none), the parent of `session.created` as a linked mention, the wording, severity class and tooltip, the head identity (provider icon, linked title, once), indentation, hidden and archived markers, a missing session, the project name per scope, the author label with a linked session, the tag, the badge, the estimated time, the expandable error, and that a hostile string is text (no `img` element is created and the sources contain no `v-html`).

`frontend/src/components/timeline/TimelineEventRow.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'

import { compileComponent, descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import * as describeEventModule from '../../utils/timeline/describeEvent.js'
import { buildTimelineRows } from '../../utils/timeline/rows.js'

const RouterLink = { props: ['to'], setup: (props, { slots }) => () => Vue.h('a', { 'data-to': JSON.stringify(props.to) }, slots.default?.()) }
const ProviderIcon = { props: ['provider'], setup: (props) => () => Vue.h('i', { 'data-provider': props.provider }) }
const ProjectBadge = { props: ['projectId'], setup: (props) => () => Vue.h('b', props.projectId) }

const knownProjects = new Set(['p1'])
const dataStore = { getProject: (id) => (knownProjects.has(id) ? { id } : null) }
const route = { name: 'project', params: { projectId: 'p1' }, query: {} }

const Mention = compileComponent(new URL('./TimelineSessionMention.vue', import.meta.url), {
    'vue-router': { RouterLink, useRoute: () => route },
    '../../utils/sessionRoute': { sessionRouteLocation: (target) => ({ name: 'session', params: { projectId: 'p1', sessionId: target.id } }) },
    '../ui/ProviderIcon.vue': ProviderIcon,
})
const Row = compileComponent(new URL('./TimelineEventRow.vue', import.meta.url), {
    'vue-router': { RouterLink },
    '../../stores/data': { useDataStore: () => dataStore },
    '../../utils/timeline/describeEvent': describeEventModule,
    '../project/ProjectBadge.vue': ProjectBadge,
    './TimelineSessionMention.vue': Mention,
})

const sessions = {
    s1: { title: 'Fix the build', project_id: 'p1', provider: 'codex', hidden: false, archived: false },
    secret: { title: 'Hidden one', project_id: 'p1', provider: 'claude_code', hidden: true, archived: true },
    parent: { title: 'The parent', project_id: 'p1', provider: 'codex', hidden: false, archived: false },
}
let nextId = 1
const make = (kind, fields = {}) => ({
    id: nextId++, kind, occurred_at: '2026-10-14T14:32:07.000000Z', precision: 'exact', actor: 'agent', via: null,
    actor_ref: null, actor_name: null, reason: '', data: {}, session_id: 's1', project_id: 'p1', provider: 'codex', ...fields,
})

function textOf(node) {
    if (node.type === '#comment') return ''
    return `${node.text ?? ''}${node.children.map(textOf).join('')}`
}

/** The links inside the session mentions (the project name is a link too). */
const sessionLinks = (root) => byClass(root, 'timeline-session').flatMap((mention) => descendants(mention, (n) => n.type === 'a'))

async function render(t, events, { scope = { kind: 'global' }, index = 0, sessionTable = sessions } = {}) {
    const rows = buildTimelineRows(events, { sessions: sessionTable, scope, dayKeyOf: () => 'd', dayLabelOf: () => 'Day' })
        .filter((row) => row.type === 'event')
    const { renderer, root } = makeRenderer()
    const context = { sessions: sessionTable, labelsFor: () => null, time: { locale: 'en-GB', timeZone: 'UTC' } }
    const app = renderer.createApp({ render: () => Vue.h(Row, { row: rows[index], context }) })
    app.mount(root)
    t.after(() => app.unmount())
    await flush()
    return root
}

const byClass = (root, name) => descendants(root, (node) => String(node.props.class ?? '').split(' ').includes(name))
const classesOf = (node) => String(node.props.class ?? '')

test('a plain row: icon with severity, wording and the time on the right with the tooltip', async (t) => {
    const root = await render(t, [make('process.started', { data: { cause: 'resume', hybrid: true } })])
    const [row] = byClass(root, 'timeline-row')
    assert.match(classesOf(row), /timeline-row--success/)
    assert.equal(row.props['data-kind'], 'process.started')
    assert.equal(descendants(root, (n) => n.type === 'wa-icon' && String(n.props.class).includes('timeline-row-icon'))[0].props.name, 'play')
    assert.equal(textOf(byClass(root, 'timeline-wording')[0]), 'Process resumed (hybrid CLI)')
    const [time] = byClass(root, 'timeline-time')
    assert.equal(textOf(time), '14:32')
    assert.match(time.props.title, /Precision: exact/)
})

test('the group head shows the provider icon and the session title as a link, once', async (t) => {
    const root = await render(t, [make('turn.started')])
    const link = descendants(root, (n) => n.type === 'a')[0]
    assert.equal(JSON.parse(link.props['data-to']).params.sessionId, 's1')
    assert.equal(textOf(link), 'Fix the build')
    assert.equal(descendants(root, (n) => n.type === 'i')[0].props['data-provider'], 'codex')
    assert.equal(byClass(root, 'timeline-identity').length, 1)
})

test('a following row is indented and has no identity', async (t) => {
    const root = await render(t, [make('turn.ended', { occurred_at: '2026-10-14T14:32:09.000000Z', data: { background_work: null } }), make('turn.started')], { index: 1 })
    assert.match(classesOf(byClass(root, 'timeline-row')[0]), /timeline-row--indented/)
    assert.equal(byClass(root, 'timeline-identity').length, 0)
})

test('a hidden and archived session is plain text with both markers; an archived one keeps its link', async (t) => {
    const hidden = await render(t, [make('turn.started', { session_id: 'secret', provider: 'claude_code' })])
    assert.equal(sessionLinks(hidden).length, 0)
    assert.equal(textOf(byClass(hidden, 'timeline-session-title--plain')[0]), 'Hidden one')
    assert.deepEqual(descendants(hidden, (n) => n.type === 'wa-icon' && String(n.props.class).includes('timeline-marker')).map((n) => n.props.name), ['box-archive', 'eye-slash'])
    const archivedOnly = { s1: { ...sessions.s1, archived: true } }
    const archived = await render(t, [make('turn.started')], { sessionTable: archivedOnly })
    assert.equal(sessionLinks(archived).length, 1)
    assert.deepEqual(descendants(archived, (n) => n.type === 'wa-icon' && String(n.props.class).includes('timeline-marker')).map((n) => n.props.name), ['box-archive'])
})

test('a session with no entry shows its id as plain text', async (t) => {
    const root = await render(t, [make('turn.started', { session_id: 'purged-session-id' })], { sessionTable: {} })
    assert.equal(sessionLinks(root).length, 0)
    assert.equal(textOf(byClass(root, 'timeline-session-title')[0]), 'purged-session-id')
})

test('the project name: a link on a global page, plain text for an unknown project, absent on a worktree page', async (t) => {
    const global = await render(t, [make('turn.started')])
    const links = descendants(global, (n) => n.type === 'a')
    assert.equal(JSON.parse(links[1].props['data-to']).params.projectId, 'p1')
    assert.equal(sessionLinks(global).length, 1)
    assert.equal(descendants(global, (n) => n.type === 'b')[0].text, 'p1')
    const unknown = await render(t, [make('turn.started', { project_id: 'gone' })])
    assert.equal(descendants(unknown, (n) => n.type === 'a').length, 1, 'the session link only')
    assert.equal(textOf(byClass(unknown, 'timeline-project--plain')[0]), 'gone')
    const worktree = await render(t, [make('turn.started')], { scope: { kind: 'worktree', projectId: 'p1' } })
    assert.equal(byClass(worktree, 'timeline-project').length, 0)
})

test('the author label follows the wording, and an agent label links its session', async (t) => {
    const root = await render(t, [make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 's1' })])
    assert.equal(textOf(byClass(root, 'timeline-author')[0]), 'by Fix the build (CLI)')
    assert.equal(descendants(byClass(root, 'timeline-author')[0], (n) => n.type === 'a').length, 1)
    assert.equal(byClass(root, 'timeline-identity').length, 1)
})

test('the markers are on the group head only: the session of an author label and a parent carry none', async (t) => {
    // The actor is a hidden AND archived session; the parent of session.created is archived too.
    const table = { ...sessions, parent: { ...sessions.parent, archived: true } }
    const labelled = await render(t, [make('archived.started', { actor: 'agent', via: 'cli', actor_ref: 'secret' })], { sessionTable: table })
    assert.equal(textOf(byClass(labelled, 'timeline-author')[0]), 'by Hidden one (CLI)')
    assert.equal(descendants(byClass(labelled, 'timeline-author')[0], (n) => String(n.props.class).includes('timeline-marker')).length, 0)
    const created = await render(t, [make('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })], { sessionTable: table })
    assert.equal(descendants(byClass(created, 'timeline-wording')[0], (n) => String(n.props.class).includes('timeline-marker')).length, 0)
    assert.equal(descendants(created, (n) => String(n.props.class).includes('timeline-marker')).length, 0, 'no marker anywhere: the head session is not archived')
})

test('the parent of session.created is rendered as a session mention with its link', async (t) => {
    const root = await render(t, [make('session.created', { actor: 'user', via: 'ui', data: { origin: 'twicc', spawned_by: 'parent' } })])
    const wording = byClass(root, 'timeline-wording')[0]
    assert.equal(textOf(wording), 'Session created by The parent')
    const link = descendants(wording, (n) => n.type === 'a')[0]
    assert.equal(JSON.parse(link.props['data-to']).params.sessionId, 'parent')
})

test('the source tag, the background-work badge and the estimated time', async (t) => {
    const tag = await render(t, [make('title.changed', { data: { source: 'auto', new: 'T', old: null } })])
    assert.equal(textOf(byClass(tag, 'timeline-tag')[0]), 'automatic')
    const badge = await render(t, [make('turn.ended', { data: { background_work: { subagents: 2, shells: 0, monitors: 0, scheduled_wakeup_at: null, goal: false } } })])
    const [element] = descendants(badge, (n) => n.type === 'wa-badge')
    assert.deepEqual([textOf(element), element.props.variant, element.props.appearance], ['2 subagents', 'warning', 'outlined'])
    const estimated = await render(t, [make('process.ended', { reason: 'lost', precision: 'estimated' })])
    const [time] = byClass(estimated, 'timeline-time')
    assert.equal(textOf(time), '~14:32')
    assert.match(classesOf(time), /timeline-time--estimated/)
})

test('an error shows its first line, with the rest behind an expand control', async (t) => {
    const root = await render(t, [make('process.ended', { reason: 'error', data: { error: 'Boom\nat file.js:1' } })])
    assert.equal(textOf(byClass(root, 'timeline-error-line')[0]), 'Boom')
    assert.equal(byClass(root, 'timeline-error-rest').length, 0)
    const [toggle] = descendants(root, (n) => n.type === 'wa-button')
    assert.equal(textOf(toggle), 'Show more')
    toggle.props.onClick()
    await flush()
    assert.equal(textOf(byClass(root, 'timeline-error-rest')[0]), 'at file.js:1')
    assert.equal(textOf(descendants(root, (n) => n.type === 'wa-button')[0]), 'Show less')
})

test('an error with a single line has no expand control', async (t) => {
    const root = await render(t, [make('process.ended', { reason: 'error', data: { error: 'Boom' } })])
    assert.equal(descendants(root, (n) => n.type === 'wa-button').length, 0)
})

test('strings from data are text, never markup', async (t) => {
    const hostile = '<img src=x onerror=alert(1)>'
    const root = await render(t, [
        make('title.changed', { data: { source: 'user', new: hostile, old: null }, actor: 'external', via: 'mcp_external', actor_name: hostile }),
    ])
    assert.equal(descendants(root, (n) => n.type === 'img').length, 0)
    assert.ok(textOf(byClass(root, 'timeline-wording')[0]).includes(hostile))
    assert.ok(textOf(byClass(root, 'timeline-author')[0]).includes(hostile))
    for (const file of ['TimelineEventRow.vue', 'TimelineSessionMention.vue']) {
        assert.ok(!readFileSync(new URL(`./${file}`, import.meta.url), 'utf8').includes('v-html'), file)
    }
})

test('an unknown kind does not break the row', async (t) => {
    const root = await render(t, [make('plan.approved', { data: 'garbage' })])
    assert.equal(textOf(byClass(root, 'timeline-wording')[0]), 'plan.approved')
})
```


- [ ] **Step 2: Run the tests and see them fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/components/timeline/TimelineEventRow.test.js
```

Expected: the file fails to load, `ENOENT ... TimelineSessionMention.vue`.

- [ ] **Step 3: Write the components**

`frontend/src/components/timeline/TimelineSessionMention.vue` (new file):

```vue
<script setup>
// TimelineSessionMention.vue - One mention of a session in the Timeline: provider icon, then the title,
// as a link or as plain text (display design sections 5.3 and 5.4). The same rules serve the group head,
// the "by <session>" author label and the parent of `session.created`.
//
// The `mention` comes from utils/timeline/sessions.js, which already decided link or not. Markers (archived,
// hidden) are shown on group heads only, through `markers`.

import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import ProviderIcon from '../ui/ProviderIcon.vue'

const props = defineProps({
    /** See `sessionMention` in utils/timeline/sessions.js. */
    mention: { type: Object, required: true },
    /** Show the archived / hidden markers (the group head only). */
    markers: { type: Boolean, default: false },
})

const route = useRoute()

const location = computed(() => (props.mention.link
    ? sessionRouteLocation({ id: props.mention.id, project_id: props.mention.projectId }, route)
    : null))
</script>

<template>
    <span class="timeline-session">
        <ProviderIcon v-if="mention.provider" :provider="mention.provider" class="timeline-session-icon" />
        <RouterLink v-if="location" :to="location" class="timeline-session-title">{{ mention.title }}</RouterLink>
        <span v-else class="timeline-session-title timeline-session-title--plain">{{ mention.title }}</span>
        <template v-if="markers">
            <wa-icon v-if="mention.archived" name="box-archive" label="Archived" title="Archived" class="timeline-marker"></wa-icon>
            <wa-icon v-if="mention.hidden" name="eye-slash" label="Hidden" title="Hidden" class="timeline-marker"></wa-icon>
        </template>
    </span>
</template>

<style scoped>
.timeline-session {
    display: inline-flex;
    align-items: baseline;
    gap: var(--wa-space-2xs);
    min-width: 0;
    max-width: 100%;
}

.timeline-session-icon {
    flex-shrink: 0;
    align-self: center;
}

.timeline-session-title {
    font-weight: var(--wa-font-weight-semibold);
    overflow-wrap: anywhere;
    color: inherit;
}

a.timeline-session-title {
    text-decoration: none;
}

a.timeline-session-title:hover {
    text-decoration: underline;
}

.timeline-marker {
    flex-shrink: 0;
    align-self: center;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
```


`frontend/src/components/timeline/TimelineEventRow.vue` (new file):

```vue
<script setup>
// TimelineEventRow.vue - One event line of the Timeline (display design section 5.1):
//   [indent] icon | [group head: provider icon, session title, project name, markers] wording, tag, badge,
//   author label | time on the right.
//
// All the decisions (wording, icon, severity, links, time) are made by the pure modules of
// utils/timeline/; this component only shows them. Every string from data is rendered as TEXT with
// interpolation, never as HTML.

import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { describeEvent } from '../../utils/timeline/describeEvent'
import ProjectBadge from '../project/ProjectBadge.vue'
import TimelineSessionMention from './TimelineSessionMention.vue'

const props = defineProps({
    /** An event row of `buildTimelineRows`. */
    row: { type: Object, required: true },
    /** Facts the wording needs: `{sessions, labelsFor, isSubagent}`. */
    context: { type: Object, required: true },
})

const dataStore = useDataStore()

const vm = computed(() => describeEvent(props.row.event, props.context))
const identity = computed(() => props.row.identity)
// A project the store does not know (deleted) is shown as plain text, never as a broken link.
const knownProject = computed(() => (identity.value?.projectId ? !!dataStore.getProject(identity.value.projectId) : false))

const expanded = ref(false)
</script>

<template>
    <div
        class="timeline-row"
        :class="[`timeline-row--${vm.severity}`, { 'timeline-row--indented': row.indented, 'timeline-row--head': row.head }]"
        :data-kind="vm.kind"
    >
        <wa-icon :name="vm.icon" class="timeline-row-icon" auto-width :title="vm.tooltip"></wa-icon>
        <div class="timeline-row-main">
            <span v-if="identity" class="timeline-identity">
                <TimelineSessionMention :mention="identity.mention" markers />
                <template v-if="identity.showProject && identity.projectId">
                    <RouterLink
                        v-if="knownProject"
                        :to="{ name: 'project', params: { projectId: identity.projectId } }"
                        class="timeline-project"
                    ><ProjectBadge :project-id="identity.projectId" /></RouterLink>
                    <span v-else class="timeline-project timeline-project--plain">{{ identity.projectId }}</span>
                </template>
            </span>
            <span class="timeline-wording">
                <template v-for="(part, index) in vm.parts" :key="index">
                    <span v-if="part.type === 'text'">{{ part.text }}</span>
                    <TimelineSessionMention v-else :mention="part.mention" />
                </template>
            </span>
            <span v-if="vm.tag" class="timeline-tag">{{ vm.tag }}</span>
            <wa-badge v-if="vm.badge" variant="warning" appearance="outlined" class="timeline-badge" :title="vm.badge.title">{{ vm.badge.text }}</wa-badge>
            <span v-if="vm.author.parts.length" class="timeline-author">
                <template v-for="(part, index) in vm.author.parts" :key="index">
                    <span v-if="part.type === 'text'">{{ part.text }}</span>
                    <TimelineSessionMention v-else :mention="part.mention" />
                </template>
            </span>
            <span v-if="vm.detail" class="timeline-error">
                <code class="timeline-error-line">{{ vm.detail.first }}</code>
                <wa-button
                    v-if="vm.detail.rest"
                    size="small"
                    appearance="plain"
                    class="timeline-error-toggle"
                    @click="expanded = !expanded"
                >{{ expanded ? 'Show less' : 'Show more' }}</wa-button>
                <pre v-if="expanded && vm.detail.rest" class="timeline-error-rest">{{ vm.detail.rest }}</pre>
            </span>
        </div>
        <span class="timeline-time" :class="{ 'timeline-time--estimated': vm.time.estimated }" :title="vm.tooltip">{{ vm.time.text }}</span>
    </div>
</template>

<style scoped>
.timeline-row {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    padding: var(--wa-space-2xs) var(--wa-space-l);
    min-height: 2rem;
    font-size: var(--wa-font-size-s);
}

.timeline-row--indented {
    padding-inline-start: calc(var(--wa-space-l) + var(--wa-space-xl));
}

.timeline-row-icon {
    flex-shrink: 0;
    align-self: center;
    color: var(--wa-color-text-quiet);
}

.timeline-row--success .timeline-row-icon { color: var(--wa-color-success-60); }
.timeline-row--warning .timeline-row-icon { color: var(--wa-color-warning-60); }
.timeline-row--danger .timeline-row-icon { color: var(--wa-color-danger-60); }

.timeline-row-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    column-gap: var(--wa-space-s);
    row-gap: 0;
    overflow-wrap: anywhere;
}

.timeline-identity {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.timeline-project {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

a.timeline-project {
    text-decoration: none;
}

.timeline-tag,
.timeline-author {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

.timeline-badge {
    align-self: center;
}

.timeline-error {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--wa-space-xs);
    flex-basis: 100%;
}

.timeline-error-line,
.timeline-error-rest {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-60);
}

.timeline-error-rest {
    flex-basis: 100%;
    margin: 0;
    white-space: pre-wrap;
}

.timeline-time {
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

.timeline-time--estimated {
    opacity: 0.6;
}
</style>
```


- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/components/timeline/*.test.js src/utils/timeline/*.test.js
```

Expected: all pass (150 tests in the scratch run).

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add frontend/src/components/timeline/TimelineSessionMention.vue frontend/src/components/timeline/TimelineEventRow.vue frontend/src/components/timeline/TimelineEventRow.test.js
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
feat(timeline): render one event row

TimelineEventRow shows what describeEvent decided: an icon with the severity colour, the
wording with its source tag, background-work badge and author label, an expandable
monospace error, and the time on the right (a "~" and a lighter style when estimated, a
multi-line native tooltip with the technical details). A group head also shows the
session identity once: provider icon, title (a link, or plain text for a hidden, missing
or subagent session), archived and hidden markers, and the project name with its
worktree marker where the scope calls for it.

TimelineSessionMention serves the head, the "by <session>" label and the parent of
session.created with the same link rules. Every string from data is rendered as text.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 8: The Timeline panel

**Files:**
- Create: `frontend/src/components/timeline/TimelinePanel.vue`, `TimelinePanel.test.js`
- Create: `frontend/src/composables/useTimelineFeed.js`
- Create: `frontend/src/utils/timeline/providerLabels.js`, `providerLabels.test.js`
- Modify: `frontend/src/styles/glass.test.js` (the new component may use a glass class), `frontend/src/styles/panel-veil.test.js` (the band's no-blur fallbacks)

**Interfaces:**
- Consumes: T4 to T7; `VirtualScroller.vue`, `DaySeparator.vue`, `ProjectDetailNavList.vue`, `stores/data`, `stores/workspaces`, `providers` (`getProviderHelpers`), `utils/compactHeight` (`compactHeight`), `utils/api` (`apiFetch`).
- Produces:
  - `TimelinePanel.vue`: prop `projectId` (project id, `ws:<id>` or `ALL_PROJECTS_ID`). Not mounted anywhere yet (T9 mounts it). T10 adds the prop `active` and the live wiring.
  - `useTimelineFeed() -> feed` (a `createTimelineFeed` with a reactive state and the real fetch), `fetchTimelinePage(params)`.
  - `createLabelsFor(getProviderHelpers) -> (provider) -> SettingsLabels | null`.
  - The page layout: a band (`timeline-band glass-sticky`) outside the scroller; below it either a state block (`timeline-state`: loading, empty, not found, error with Retry) or the list (`timeline-list` holding the scroller, then the footer with the spinner or the next-page error).

- [ ] **Step 1: Write the failing tests**

The panel suite compiles the real `TimelinePanel.vue` and the real `VirtualScroller.vue` with the harness; the feed is the real controller on a fake server, the stores are reactive doubles. A scroller double (renders every item, records what the panel asks of it, answers `getItemHeight` for rows it "measured", emits `update` on demand) lets the tests put the end of a 60-event list in or out of view. The suite covers the band, the global page, the loading state before the projects load (no request), an empty workspace and an unknown workspace (no request), a set change reloading, an unknown project (link to All Projects), the empty feed, a failed first page with Retry, a short list asking for its next page by itself, the next page requested ONLY when the end of a long list is in view (none far from the end, exactly one at the end, none once the server said there is no more), a failed next page whose Retry sends exactly one new request and which does not retry by itself (both error blocks are also checked for slots: every child of a `wa-callout` must be default content or `slot="icon"`, because the callout has no other slot and the browser would not render the button, which this slot-less test renderer cannot show by itself), the page asking again by itself when a landed page leaves the end in view and when its scroller becomes visible again, the feed being disposed on unmount, the height estimates (every row of the first page and the compact-height navigation row are seeded; a row the scroller already measured is not; a new scroller after a remount is seeded again; a row that left the list is seeded again on return), the compact-height first row, the navigation copy and its divider in EVERY state of the page (loading, empty, not found, error) in compact mode and none in normal mode, and source pins (band outside the scroller, no static router import in any timeline module, archived-blind workspace ids, no bottom pin, the no-blur fallbacks).

`frontend/src/components/timeline/TimelinePanel.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import test from 'node:test'
import * as Vue from 'vue'

import { compileComponent, descendants, flush, makeRenderer, virtualScroller } from '../../../tests/helpers/scrollerComponentHarness.js'
import { createFeedState, createTimelineFeed } from '../../utils/timeline/feed.js'
import * as providerLabels from '../../utils/timeline/providerLabels.js'
import * as rowsModule from '../../utils/timeline/rows.js'
import * as scopeModule from '../../utils/timeline/scope.js'

const RouterLink = { props: ['to'], setup: (props, { slots }) => () => Vue.h('a', { 'data-to': JSON.stringify(props.to) }, slots.default?.()) }
const stub = (tag, textProp) => ({ props: ['projectId', 'label', 'row', 'dayKey'], setup: (props) => () => Vue.h(tag, { 'data-id': props.row?.key ?? props.projectId }, textProp ? props[textProp] : undefined) })

const isoAt = (n) => new Date(Date.UTC(2026, 9, 14, 10, 0, n)).toISOString().replace('.000Z', '.000000Z')
const at = (n) => `2026-10-14T10:00:${String(n).padStart(2, '0')}.000000Z`
const event = (id, fields = {}) => ({ id, kind: 'turn.started', occurred_at: at(id), session_id: 's1', project_id: 'p1', provider: 'codex', actor: 'agent', precision: 'exact', data: {}, ...fields })

/** A scroller double: renders every item and records what the panel asks of it. */
function createFakeScroller() {
    // `measured`: keys the scroller already measured (it answers their height). `mounts`: how many scrollers
    // were created. `emit(range)`: the `update` event of the real scroller.
    const log = { scrollTop: 0, anchorIndex: 1, calls: [], measured: new Set(), mounts: 0, range: { start: 0, end: 0 }, emit: null, emitVisible: null }
    const component = {
        props: ['items', 'itemKey'],
        emits: ['update', 'became-visible'],
        setup(props, { slots, expose, emit }) {
            log.mounts += 1
            let rendered = []
            log.emit = (range) => {
                log.range = { start: range.visibleStartIndex, end: range.visibleEndIndex }
                emit('update', range)
            }
            log.emitVisible = () => emit('became-visible')
            expose({
                seedItemHeight: (key, height) => log.calls.push(['seed', key, height]),
                getItemHeight: (key) => (log.measured.has(key) ? 30 : undefined),
                getVisibleRange: () => log.range,
                getScrollState: () => ({ scrollTop: log.scrollTop }),
                // The first visible row is the one at `anchorIndex` of what is RENDERED NOW: asked before the DOM
                // moved it names the old row, asked after it names whatever sits there then.
                getScrollAnchor: () => (rendered[log.anchorIndex] === undefined
                    ? null : { key: rendered[log.anchorIndex], index: log.anchorIndex, offset: 7 }),
                // A restore also records the rows rendered at that moment: before the render they are the old ones.
                scrollToAnchor: (anchor) => log.calls.push(['anchor', anchor, [...rendered]]),
                scrollToTop: () => log.calls.push(['top']),
            })
            return () => {
                rendered = props.items.map((item) => item.key)
                return Vue.h('div', { class: 'fake-scroller' }, props.items.map((item) => slots.default({ item })))
            }
        },
    }
    return { log, component }
}

function fixture({ events = [event(3), event(2), event(1)], hasMore = false, projectsLoaded = true, compact = false, projects = { p1: { id: 'p1' } }, workspaces = {}, fakeScroller = false } = {}) {
    const calls = []
    const state = Vue.reactive(createFeedState())
    const server = {
        fail: false,
        respond: (params) => ({
            events: params.before ? [] : events, has_more: params.before ? false : hasMore, truncated: false,
            latest_id: events.length ? events[0].id : null, sessions: { s1: { title: 'My session', project_id: 'p1', provider: 'codex', hidden: false, archived: false } },
        }),
    }
    const feed = createTimelineFeed({
        state,
        fetchPage: async (params) => {
            calls.push({ ...params })
            if (server.fail) throw new Error('boom')
            return server.respond(params)
        },
    })
    const dataStore = Vue.reactive({
        projectsLoaded, projects,
        getProject: (id) => dataStore.projects[id] ?? null,
        getProjectScopeIds: (id) => [id],
        getSession: () => undefined,
    })
    const workspacesStore = Vue.reactive({
        workspaces,
        getWorkspaceById: (id) => workspacesStore.workspaces[id] ?? null,
        getAllProjectIds: (id) => workspacesStore.workspaces[id].projectIds,
    })
    const compactHeight = Vue.ref(compact)
    const scroller = fakeScroller ? createFakeScroller() : null
    const Panel = compileComponent(new URL('./TimelinePanel.vue', import.meta.url), {
        'vue-router': { RouterLink },
        '../../composables/useTimelineFeed': { useTimelineFeed: () => feed },
        '../../stores/data': { ALL_PROJECTS_ID: '__all__', useDataStore: () => dataStore },
        '../../stores/workspaces': { useWorkspacesStore: () => workspacesStore },
        '../../providers': { getProviderHelpers: () => null },
        '../../utils/compactHeight': { compactHeight },
        '../../utils/timeline/rows': rowsModule,
        '../../utils/timeline/providerLabels': providerLabels,
        '../../utils/timeline/scope': scopeModule,
        '../project/ProjectDetailNavList.vue': stub('nav'),
        '../session/detail/items/DaySeparator.vue': { props: ['label', 'dayKey'], setup: (props) => () => Vue.h('div', { class: 'day', 'data-day': props.dayKey }, props.label) },
        '../virtual-scroller/VirtualScroller.vue': scroller ? scroller.component : virtualScroller(),
        './TimelineEventRow.vue': stub('article'),
    })
    return { Panel, feed, state, calls, server, dataStore, workspacesStore, compactHeight, scroller }
}

const settle = async () => {
    await flush()
    await new Promise((resolve) => setTimeout(resolve, 5))
    await flush()
}

async function mount(t, f, projectId = '__all__') {
    const { renderer, root } = makeRenderer(600)
    const props = Vue.reactive({ projectId })
    const app = renderer.createApp({ render: () => Vue.h(f.Panel, props) })
    app.mount(root)
    let mounted = true
    t.after(() => { if (mounted) app.unmount() })
    await settle()
    root.unmount = () => { app.unmount(); mounted = false }
    return root
}

/**
 * `wa-callout` has a default slot and an `icon` slot only: a light-DOM child with any other `slot` is not
 * rendered by the browser, whatever this slot-less test renderer shows. Every callout child must be assignable.
 */
function assertRenderableCallouts(root) {
    const callouts = descendants(root, (n) => n.type === 'wa-callout')
    assert.ok(callouts.length >= 1)
    for (const callout of callouts) {
        for (const child of callout.children.filter((n) => n.type !== '#comment')) {
            assert.ok([undefined, 'icon'].includes(child.props.slot), `<${child.type} slot="${child.props.slot}"> has no slot to go to`)
        }
        const button = descendants(callout, (n) => n.type === 'wa-button')[0]
        assert.equal(button.props.slot, undefined, 'the Retry button is default content of the callout')
    }
}

const byClass = (root, name) => descendants(root, (node) => String(node.props.class ?? '').split(' ').includes(name))
const textOf = (node) => (node.type === '#comment' ? '' : `${node.text ?? ''}${node.children.map(textOf).join('')}`)
const rowIds = (root) => descendants(root, (n) => n.type === 'article').map((n) => n.props['data-id'])

test('the band carries the label "Timeline" in the glass-sticky style and nothing else', async (t) => {
    const root = await mount(t, fixture())
    const [band] = byClass(root, 'timeline-band')
    assert.match(String(band.props.class), /glass-sticky/)
    assert.equal(textOf(band), 'Timeline')
    assert.equal(descendants(band, (n) => ['input', 'wa-select', 'wa-button'].includes(n.type)).length, 0)
})

test('the global page loads the feed and renders a day separator then the events newest first', async (t) => {
    const f = fixture()
    const root = await mount(t, f)
    assert.deepEqual(f.calls, [{ projectIds: [], before: null, afterId: null }])
    assert.equal(byClass(root, 'day').length, 1)
    assert.deepEqual(rowIds(root), ['event-3', 'event-2', 'event-1'])
})

test('while the projects are not loaded the page shows the loading state and sends nothing, then loads', async (t) => {
    const f = fixture({ projectsLoaded: false })
    const root = await mount(t, f, 'p1')
    assert.equal(f.calls.length, 0)
    assert.match(textOf(byClass(root, 'timeline-state')[0]), /Loading/)
    f.dataStore.projectsLoaded = true
    await flush()
    await new Promise((resolve) => setTimeout(resolve, 5))
    await flush()
    assert.deepEqual(f.calls, [{ projectIds: ['p1'], before: null, afterId: null }])
    assert.equal(rowIds(root).length, 3)
})

test('a workspace with no project shows the empty state and sends nothing; an unknown workspace is loading', async (t) => {
    const empty = fixture({ workspaces: { w: { projectIds: [] } } })
    const root = await mount(t, empty, 'ws:w')
    assert.equal(empty.calls.length, 0)
    assert.equal(textOf(byClass(root, 'timeline-state')[0]), 'No events yet')
    const unknown = fixture()
    const root2 = await mount(t, unknown, 'ws:ghost')
    assert.equal(unknown.calls.length, 0)
    assert.match(textOf(byClass(root2, 'timeline-state')[0]), /Loading/)
})

test('a workspace sends every project id, and a change of the set reloads the first page', async (t) => {
    const f = fixture({ workspaces: { w: { projectIds: ['p1', 'p2'] } } })
    const root = await mount(t, f, 'ws:w')
    assert.deepEqual(f.calls.at(-1).projectIds, ['p1', 'p2'])
    f.workspacesStore.workspaces.w = { projectIds: ['p1', 'p2', 'p3'] }
    await flush()
    await new Promise((resolve) => setTimeout(resolve, 5))
    assert.deepEqual(f.calls.map((c) => c.projectIds), [['p1', 'p2'], ['p1', 'p2', 'p3']])
    assert.ok(root)
})

test('an unknown project shows "Project not found" with a link to All Projects and sends nothing', async (t) => {
    const f = fixture()
    const root = await mount(t, f, 'ghost')
    assert.equal(f.calls.length, 0)
    const [stateBlock] = byClass(root, 'timeline-state')
    assert.match(textOf(stateBlock), /Project not found/)
    const [link] = descendants(stateBlock, (n) => n.type === 'a')
    assert.deepEqual(JSON.parse(link.props['data-to']), { name: 'projects-all' })
})

test('an empty feed shows the empty state and no end-of-list marker', async (t) => {
    const root = await mount(t, fixture({ events: [] }))
    assert.equal(textOf(byClass(root, 'timeline-state')[0]), 'No events yet')
    assert.equal(byClass(root, 'timeline-list').length, 0)
})

test('a failed first page shows an inline error whose Retry reloads', async (t) => {
    const f = fixture()
    f.server.fail = true
    const root = await mount(t, f)
    const callout = descendants(root, (n) => n.type === 'wa-callout')[0]
    assert.match(textOf(callout), /Failed to load the timeline/)
    assertRenderableCallouts(root)
    f.server.fail = false
    const retry = descendants(callout, (n) => n.type === 'wa-button')[0]
    retry.props.onClick()
    await flush()
    await new Promise((resolve) => setTimeout(resolve, 5))
    await flush()
    assert.equal(rowIds(root).length, 3)
})

test('a list shorter than the window asks for the next page by itself, and not when there is no more', async (t) => {
    const more = fixture({ hasMore: true })
    await mount(t, more)
    assert.equal(more.calls.filter((c) => c.before).length >= 1, true)
    const none = fixture({ hasMore: false })
    await mount(t, none)
    assert.equal(none.calls.filter((c) => c.before).length, 0)
})

// Sixty events on one day: 61 rows with the separator.
const sixty = () => Array.from({ length: 60 }, (_, i) => event(60 - i, { occurred_at: isoAt(60 - i) }))
const beforeRequests = (f) => f.calls.filter((c) => c.before).length

test('the next page is requested only when the end of the list is in view', async (t) => {
    const f = fixture({ events: sixty(), hasMore: true, fakeScroller: true })
    await mount(t, f)
    assert.equal(beforeRequests(f), 0, 'the first rows are in view, far from the end')
    f.scroller.log.emit({ startIndex: 0, endIndex: 20, visibleStartIndex: 0, visibleEndIndex: 5 })
    await settle()
    assert.equal(beforeRequests(f), 0)
    f.scroller.log.emit({ startIndex: 30, endIndex: 60, visibleStartIndex: 40, visibleEndIndex: 58 })
    await settle()
    assert.equal(beforeRequests(f), 1, 'the end is in view: exactly one request')
    f.scroller.log.emit({ startIndex: 30, endIndex: 60, visibleStartIndex: 40, visibleEndIndex: 58 })
    await settle()
    assert.equal(beforeRequests(f), 1, 'the server said there is no more')
})

test('a failed next page shows an inline error whose Retry sends one new request', async (t) => {
    const f = fixture({ events: sixty(), hasMore: true, fakeScroller: true })
    const root = await mount(t, f)
    f.server.fail = true
    f.scroller.log.emit({ startIndex: 30, endIndex: 60, visibleStartIndex: 40, visibleEndIndex: 58 })
    await settle()
    const footer = byClass(root, 'timeline-footer')[0]
    assert.match(textOf(footer), /Failed to load more events/)
    assertRenderableCallouts(footer)
    assert.equal(beforeRequests(f), 1)
    f.scroller.log.emit({ startIndex: 30, endIndex: 60, visibleStartIndex: 40, visibleEndIndex: 58 })
    await settle()
    assert.equal(beforeRequests(f), 1, 'no automatic retry while the error shows')
    f.server.fail = false
    descendants(footer, (n) => n.type === 'wa-button')[0].props.onClick()
    await settle()
    assert.equal(beforeRequests(f), 2, 'Retry sends one new request')
    assert.equal(f.state.loadError, null)
    assert.equal(byClass(root, 'timeline-footer').filter((n) => textOf(n).includes('Failed')).length, 0)
})

test('a page that lands with the end of the list still in view asks for the next one by itself', async (t) => {
    // No `update` event: the panel itself asks the scroller what is in view when a page has landed.
    const f = fixture({ events: sixty(), hasMore: true, fakeScroller: true })
    f.scroller.log.range = { start: 40, end: 58 }
    await mount(t, f)
    assert.equal(beforeRequests(f), 1)
})

test('a scroller that becomes visible again asks the next page if the end is in view', async (t) => {
    const f = fixture({ events: sixty(), hasMore: true, fakeScroller: true })
    await mount(t, f)
    assert.equal(beforeRequests(f), 0)
    f.scroller.log.range = { start: 40, end: 58 }
    f.scroller.log.emitVisible()
    await settle()
    assert.equal(beforeRequests(f), 1)
})

test('unmounting the page disposes its feed', async (t) => {
    const f = fixture({ fakeScroller: true })
    let disposed = 0
    const original = f.feed.dispose
    f.feed.dispose = () => { disposed += 1; original() }
    const root = await mount(t, f)
    assert.equal(disposed, 0)
    root.unmount()
    assert.equal(disposed, 1)
})

const seedsOf = (f) => f.scroller.log.calls.filter(([name]) => name === 'seed')

test('every row gets a height estimate once the scroller exists, the navigation row included', async (t) => {
    const f = fixture({ fakeScroller: true, compact: true })
    await mount(t, f)
    const seeds = Object.fromEntries(seedsOf(f).map(([, key, height]) => [key, height]))
    assert.equal(seeds['timeline-nav'], 96)
    assert.equal(seeds['event-3'], 34)
    assert.equal(seeds[Object.keys(seeds).find((key) => key.startsWith('daysep-'))], 40)
    assert.equal(seedsOf(f).length, 5, 'nav row, day separator and three events, once each')
})

test('a row the scroller already measured keeps its measure', async (t) => {
    const f = fixture({ fakeScroller: true })
    f.scroller.log.measured.add('event-2')
    await mount(t, f)
    assert.deepEqual(seedsOf(f).map(([, key]) => key).sort(), ['daysep-2026-10-14', 'event-1', 'event-3'])
})

test('a new scroller (the list came back) is seeded again, and a row that left the list is seeded again if it returns', async (t) => {
    const f = fixture({ fakeScroller: true })
    await mount(t, f, 'p1')
    assert.equal(f.scroller.log.mounts, 1)
    assert.equal(seedsOf(f).filter(([, key]) => key === 'event-3').length, 1)
    f.dataStore.projectsLoaded = false
    await settle()
    f.dataStore.projectsLoaded = true
    await settle()
    assert.equal(f.scroller.log.mounts, 2, 'the scroller was destroyed and created again')
    assert.equal(seedsOf(f).filter(([, key]) => key === 'event-3').length, 2)
    const all = f.state.events
    f.state.events = [all[0]]
    await settle()
    f.state.events = all
    await settle()
    assert.equal(seedsOf(f).filter(([, key]) => key === 'event-2').length, 3, 'trimmed with the list, seeded when it came back')
})

test('compact-height mode: the navigation copy is the first row of the list; normal mode has none', async (t) => {
    const compact = fixture({ compact: true })
    const root = await mount(t, compact)
    assert.equal(byClass(root, 'timeline-nav-list').length >= 1, true)
    const firstRendered = descendants(root, (n) => n.type === 'nav' || n.type === 'div' && n.props.class === 'day' || n.type === 'article')[0]
    assert.equal(firstRendered.type, 'nav')
    const normal = fixture({ compact: false })
    const root2 = await mount(t, normal)
    assert.equal(descendants(root2, (n) => n.type === 'nav').length, 0)
    assert.equal(byClass(root2, 'timeline-band').length, 1, 'the band is outside the scroller in both modes')
})

test('compact-height mode: every state (loading, empty, not found, error) carries the navigation copy and its divider; normal mode none', async (t) => {
    const navigation = (root) => [byClass(root, 'timeline-nav-list').length, byClass(root, 'timeline-nav-divider').length]
    const loading = await mount(t, fixture({ compact: true, projectsLoaded: false }), 'p1')
    assert.deepEqual(navigation(loading), [1, 1])
    const empty = await mount(t, fixture({ compact: true, workspaces: { w: { projectIds: [] } } }), 'ws:w')
    assert.deepEqual(navigation(empty), [1, 1])
    const missing = await mount(t, fixture({ compact: true, projects: {} }), 'ghost')
    assert.deepEqual(navigation(missing), [1, 1])
    const broken = fixture({ compact: true })
    broken.server.fail = true
    assert.deepEqual(navigation(await mount(t, broken)), [1, 1])
    const normal = await mount(t, fixture({ compact: false, projectsLoaded: false }), 'p1')
    assert.deepEqual(navigation(normal), [0, 0])
})

test('no timeline module imports the router statically (circular imports break the hot reload)', () => {
    const dirs = ['../../components/timeline', '../../utils/timeline', '../../composables']
    const files = dirs.flatMap((dir) => readdirSync(new URL(`${dir}/`, import.meta.url))
        .filter((name) => /^(?!.*\.test\.js$).*\.(js|vue)$/.test(name) && (dir !== '../../composables' || name === 'useTimelineFeed.js'))
        .map((name) => new URL(`${dir}/${name}`, import.meta.url)))
    assert.ok(files.length >= 12)
    for (const file of files) {
        assert.ok(!/from ['"](?:\.\.?\/)+router(?:\.js)?['"]/.test(readFileSync(file, 'utf8')), String(file))
    }
})

test('source pins: the band is outside the scroller, the router is not imported, the bottom pin is off', () => {
    const source = readFileSync(new URL('./TimelinePanel.vue', import.meta.url), 'utf8')
    const template = source.slice(source.indexOf('<template>'), source.indexOf('</template>\n\n<style'))
    assert.ok(template.indexOf('timeline-band') < template.indexOf('<VirtualScroller'))
    assert.ok(!/from '\.\.\/\.\.\/router/.test(source) && !/from 'vue-router'.*useRouter/.test(source))
    assert.match(source, /\.timeline-list \.timeline-scroller\.at-bottom \{\s*overflow-anchor: none;/)
    assert.match(source, /\.timeline-list \.timeline-scroller \{\s*flex: 1;\s*min-height: 0;/)
    assert.match(source, /<VirtualScroller\s+ref="scrollerRef"\s+class="timeline-scroller"/)
    // Stats' archived-blind list: the "show archived projects" setting does not apply to the Timeline.
    assert.match(source, /workspacesStore\.getAllProjectIds\(id\)/)
    assert.ok(!source.includes('getVisibleProjectIds'))
    assert.match(source, /prevent-auto-scroll-to-bottom/)
    assert.match(source, /@media \(prefers-reduced-transparency: reduce\) \{\s*\.timeline-band \{\s*--glass-sticky-bg: transparent;\s*background: var\(--panel-solid\);/)
    assert.match(source, /:root\.reduce-effects \.timeline-band \{\s*--glass-sticky-bg: transparent;\s*background: var\(--panel-solid\);/)
})
```


`frontend/src/utils/timeline/providerLabels.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import { describeSettingsChange } from './settingsDiff.js'
import { createLabelsFor } from './providerLabels.js'

const helpers = {
    getFieldLabel: (field) => ({ effort: 'Effort', selected_model: 'Model' }[field] ?? field),
    getChoiceLabel: (field, value) => (field === 'effort' && value === 'high' ? 'High' : null),
    getModelLabel: (model) => (model === 'opus' ? 'Opus' : ''),
}
const labelsFor = createLabelsFor((provider) => (provider === 'claude_code' ? helpers : null))

test('a known provider gives labels, an unknown or missing one gives null', () => {
    assert.equal(labelsFor('codex'), null)
    assert.equal(labelsFor(null), null)
    assert.equal(labelsFor(undefined), null)
    const labels = labelsFor('claude_code')
    assert.deepEqual([labels.field('effort'), labels.choice('effort', 'high'), labels.choice('effort', 'x'), labels.model('opus')], ['Effort', 'High', null, 'Opus'])
})

test('the diff uses them and falls back to the raw value when the helper has no label', () => {
    const labels = labelsFor('claude_code')
    assert.equal(
        describeSettingsChange({ effort: ['low', 'high'], selected_model: ['mystery', 'opus'] }, '', labels).text,
        'Effort: low -> High, Model: mystery -> Opus',
    )
})
```


The two existing style suites change: the glass whitelist gains the new component, and the band gets the same fallback pin as Stats' provider filter (test 5c).

`frontend/src/styles/glass.test.js` (modify):

```diff
--- a/frontend/src/styles/glass.test.js
+++ b/frontend/src/styles/glass.test.js
@@ -228,9 +228,9 @@ function templateOf(sfc) {
 
 const GLASS_CLASSES = ['glass-surface', 'glass-sticky', 'glass-veil']
 const GLASS_FILES = ['CommandPickerPopup', 'MessageHistoryPickerPopup', 'FilePickerPopup', 'DirectoryPickerPopup',
     'SessionSwitcher', 'TextSelectionComment', 'HoverInfoPanel', 'UsageGraphDialog', 'ContributionSparklines',
-    'SharedSubagentView', 'ProjectView', 'CommandPalette', 'SettingsPopover', 'SessionHeader', 'ProjectDetailHeader', 'ShareSessionApp', 'ContributionGraphs']
+    'SharedSubagentView', 'ProjectView', 'CommandPalette', 'SettingsPopover', 'SessionHeader', 'ProjectDetailHeader', 'ShareSessionApp', 'ContributionGraphs', 'TimelinePanel']
 
 test('4. glass classes stay away from pane containers', () => {
     for (const name of ['main-content', 'session-layout', 'center-slot', 'dock-region', 'layout-overlay',
         'frame-host', 'file-pane-preview--fullscreen', 'browser-pane--fullscreen']) {
```


`frontend/src/styles/panel-veil.test.js` (modify):

```diff
--- a/frontend/src/styles/panel-veil.test.js
+++ b/frontend/src/styles/panel-veil.test.js
@@ -128,8 +128,23 @@ test('5b. the provider filter is neutral glass, opaque --panel-solid without the
         assert.ok(/background: var\(--panel-solid\);/.test(body), `${selector}: paints --panel-solid`)
     }
 })
 
+// The Timeline band is the same neutral glass bar, outside the scroller: same no-blur fallbacks.
+test('5c. the timeline band is neutral glass, opaque --panel-solid without the blur', () => {
+    const src = strip(read('../components/timeline/TimelinePanel.vue'))
+    const style = src.slice(src.indexOf('<style'))
+    assert.ok(/<template>[\s\S]*class="timeline-band glass-sticky"/.test(src), 'glass-sticky layer')
+    assert.ok(/--glass-sticky-bg: color-mix\(in oklab, var\(--surface-solid\) 60%, transparent\);/.test(style), 'neutral 60%')
+    for (const selector of ['@media (prefers-reduced-transparency: reduce) {\n    .timeline-band', ':root.reduce-effects .timeline-band']) {
+        const at = style.indexOf(selector)
+        assert.ok(at >= 0, selector)
+        const body = style.slice(at, style.indexOf('}', at))
+        assert.ok(body.includes('--glass-sticky-bg: transparent;'), `${selector}: no glass layer`)
+        assert.ok(/background: var\(--panel-solid\);/.test(body), `${selector}: paints --panel-solid`)
+    }
+})
+
 // The session search bar floats at the top of the chat, on the card's own colour: a neutral hairline and
 // no shadow made it vanish. It reuses what the floating surfaces have: their shadow and the glass border.
 test('6. the session search bar is set off: the floating shadow and the glass border', () => {
     const src = strip(read('../components/session/list/SessionSearchBar.vue'))
```


- [ ] **Step 2: Run the tests and see them fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/components/timeline/TimelinePanel.test.js src/utils/timeline/providerLabels.test.js src/styles/glass.test.js src/styles/panel-veil.test.js
```

Expected: `ERR_MODULE_NOT_FOUND` for `providerLabels.js` (the panel suite) and a failing panel-veil 5c (`ENOENT` reading `TimelinePanel.vue`).

- [ ] **Step 3: Write the adapter, the composable and the panel**

`frontend/src/utils/timeline/providerLabels.js` (new file):

```javascript
// frontend/src/utils/timeline/providerLabels.js
//
// Adapter between the provider helpers (providers/baseHelpers.js) and the settings diff of a timeline row.
// The helpers pull in stores and the DOM, so they are handed in: the module itself stays pure.

/**
 * @param {(provider: string) => (Object|null)} getProviderHelpers
 * @returns {(provider: (string|null)) => (import('./settingsDiff.js').SettingsLabels|null)} `null` for an
 *   unknown provider: the diff then shows raw names and values.
 */
export function createLabelsFor(getProviderHelpers) {
    return (provider) => {
        const helpers = provider ? getProviderHelpers(provider) : null
        if (!helpers) return null
        return {
            field: (field) => helpers.getFieldLabel(field),
            choice: (field, value) => helpers.getChoiceLabel(field, value),
            model: (model) => helpers.getModelLabel(model),
        }
    }
}
```


`frontend/src/composables/useTimelineFeed.js` (new file):

```javascript
// frontend/src/composables/useTimelineFeed.js
//
// The Timeline page's feed: the pure controller of utils/timeline/feed.js with a reactive state and the
// real network call. One instance per page (the page is kept alive per scope, so the list survives a
// scope switch).

import { reactive } from 'vue'
import { apiFetch } from '../utils/api'
import { buildTimelineUrl, createFeedState, createTimelineFeed } from '../utils/timeline/feed'

/**
 * @param {{projectIds: string[], before: (string|null), afterId: (number|null)}} params
 * @returns {Promise<Object>} The response body of `GET /api/timeline/`.
 */
export async function fetchTimelinePage({ projectIds, before, afterId }) {
    const response = await apiFetch(buildTimelineUrl({ projectIds, before, afterId }))
    if (!response.ok) throw new Error(`Timeline request failed (${response.status})`)
    return response.json()
}

export function useTimelineFeed() {
    return createTimelineFeed({
        fetchPage: fetchTimelinePage,
        state: reactive(createFeedState()),
        onLiveError: (error) => console.warn('Timeline live fetch failed', error),
    })
}
```


`frontend/src/components/timeline/TimelinePanel.vue` (new file):

```vue
<script setup>
// TimelinePanel.vue - The Timeline tab of a scope page (global, workspace, project, worktree).
// Design: docs/plans/2026-10-15-timeline-display-design.md.
//
// A sticky "Timeline" band, then the newest-first list in the simple virtual scroller. The page header and
// the band never scroll; only the list does. In compact-height mode the page header drops its navigation
// list and a copy of it becomes the FIRST ROW of the list, so it scrolls away with the content while the
// band stays at the top (the same behaviour as the Stats tab).
//
// The data side lives in utils/timeline/ (pure, tested): which events the scope asks for, the fetch queue,
// the grouping into rows and the wording of each row. This component wires them to the stores and the DOM.

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useTimelineFeed } from '../../composables/useTimelineFeed'
import { ALL_PROJECTS_ID, useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { getProviderHelpers } from '../../providers'
import { compactHeight } from '../../utils/compactHeight'
import { buildTimelineRows, estimateRowHeight, isNearEnd } from '../../utils/timeline/rows'
import { createLabelsFor } from '../../utils/timeline/providerLabels'
import { resolveTimelineScope } from '../../utils/timeline/scope'
import ProjectDetailNavList from '../project/ProjectDetailNavList.vue'
import DaySeparator from '../session/detail/items/DaySeparator.vue'
import VirtualScroller from '../virtual-scroller/VirtualScroller.vue'
import TimelineEventRow from './TimelineEventRow.vue'

const props = defineProps({
    /** Project ID, `ws:<id>` of a workspace, or ALL_PROJECTS_ID. */
    projectId: {
        type: String,
        required: true,
    },
})

const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()
const feed = useTimelineFeed()
const state = feed.state

// ── What the page asks for ────────────────────────────────────────────────────────────────────────────────

const scope = computed(() => resolveTimelineScope({
    pageId: props.projectId,
    allProjectsId: ALL_PROJECTS_ID,
    projectsLoaded: dataStore.projectsLoaded,
    // The archived-blind list, as Stats uses: the "show archived projects" setting does not apply here.
    workspaceOf: (id) => (workspacesStore.getWorkspaceById(id) ? { allProjectIds: workspacesStore.getAllProjectIds(id) } : null),
    projectOf: (id) => dataStore.getProject(id),
    projectScopeIds: (id) => dataStore.getProjectScopeIds(id),
}))

// The feed compares the SET of ids: a project added, removed or archived reloads the first page.
watch(() => scope.value.projectIds, (ids) => feed.setScope(ids), { immediate: true })
onBeforeUnmount(() => feed.dispose())

// ── What the page shows ───────────────────────────────────────────────────────────────────────────────────

const context = computed(() => ({
    sessions: state.sessions,
    labelsFor: createLabelsFor(getProviderHelpers),
    // A subagent has no openable route as a top-level session; the store knows the ones it loaded.
    isSubagent: (id) => dataStore.getSession(id)?.type === 'subagent',
}))

const eventRows = computed(() => buildTimelineRows(state.events, {
    sessions: state.sessions,
    scope: { kind: scope.value.kind, projectId: scope.value.projectId },
    isSubagent: context.value.isSubagent,
}))

const NAV_ROW = Object.freeze({ key: 'timeline-nav', type: 'nav' })
const items = computed(() => (compactHeight.value ? [NAV_ROW, ...eventRows.value] : eventRows.value))

/** Which block of the page is visible. */
const view = computed(() => {
    if (scope.value.state === 'loading') return 'loading'
    if (scope.value.state === 'empty') return 'empty'
    if (scope.value.state === 'not-found') return 'not-found'
    if (state.status === 'error') return 'error'
    if (state.status !== 'ready') return 'loading'
    return state.events.length ? 'list' : 'empty'
})

const itemKey = (item) => item.key

// ── Scroller: estimates, next page ────────────────────────────────────────────────────────────────────────

const scrollerRef = ref(null)

// A first height estimate per row type (day separator, event line, navigation copy) for the rows the scroller
// has not measured yet. It runs AFTER the render: the scroller only exists once the first page flipped the view
// to the list, and it is created again each time the list comes back. A row the scroller already measured keeps
// its measure (seeding would overwrite it, and no resize would correct it). `seeded` follows the list: it is
// emptied when the scroller goes away and trimmed to the keys still in the list.
const seeded = new Set()
function seedHeights() {
    const scroller = scrollerRef.value
    if (!scroller) {
        seeded.clear()
        return
    }
    const keys = new Set()
    for (const row of items.value) {
        keys.add(row.key)
        if (seeded.has(row.key)) continue
        seeded.add(row.key)
        if (scroller.getItemHeight(row.key) === undefined) scroller.seedItemHeight(row.key, estimateRowHeight(row))
    }
    for (const key of seeded) if (!keys.has(key)) seeded.delete(key)
}
watch([items, scrollerRef], seedHeights, { flush: 'post' })

let visibleEnd = 0

function maybeLoadMore() {
    if (view.value === 'list' && isNearEnd(items.value.length, visibleEnd)) feed.loadMore()
}

function onScrollerUpdate(range) {
    visibleEnd = range.visibleEndIndex
    maybeLoadMore()
}

// What is in view now, asked to the scroller (its last `update` may be older: a page landed, the tab came back).
function refreshVisibleEnd() {
    const range = scrollerRef.value?.getVisibleRange()
    if (range) visibleEnd = range.end
}

function onBecameVisible() {
    refreshVisibleEnd()
    maybeLoadMore()
}

// A page that just landed may leave the end of the list still in view (a tall window): ask again.
watch(() => state.events.length, async () => {
    await nextTick()
    refreshVisibleEnd()
    maybeLoadMore()
})
</script>

<template>
    <div class="timeline-panel">
        <div class="timeline-band glass-sticky">
            <span class="timeline-band-label">Timeline</span>
        </div>

        <div v-if="view !== 'list'" class="timeline-state-area">
            <template v-if="compactHeight">
                <ProjectDetailNavList :project-id="projectId" class="timeline-nav-list" />
                <wa-divider class="timeline-nav-divider"></wa-divider>
            </template>
            <div v-if="view === 'loading'" class="timeline-state" role="status">
                <wa-spinner></wa-spinner>
                <span>Loading…</span>
            </div>
            <div v-else-if="view === 'empty'" class="timeline-state">No events yet</div>
            <div v-else-if="view === 'not-found'" class="timeline-state">
                <span>Project not found</span>
                <RouterLink :to="{ name: 'projects-all' }">Back to All Projects</RouterLink>
            </div>
            <div v-else class="timeline-state">
                <!-- wa-callout has a default slot and an icon slot only: the button goes in the default one. -->
                <wa-callout variant="danger">
                    <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                    <div class="timeline-error-content">
                        <span>Failed to load the timeline</span>
                        <wa-button variant="danger" appearance="outlined" size="small" @click="feed.retry()">
                            <wa-icon name="arrow-rotate-right" slot="start"></wa-icon>
                            Retry
                        </wa-button>
                    </div>
                </wa-callout>
            </div>
        </div>

        <div v-else class="timeline-list">
            <VirtualScroller
                ref="scrollerRef"
                class="timeline-scroller"
                :items="items"
                :item-key="itemKey"
                :min-item-height="28"
                :buffer="400"
                :unload-buffer="800"
                prevent-auto-scroll-to-bottom
                @update="onScrollerUpdate"
                @became-visible="onBecameVisible"
            >
                <template #default="{ item }">
                    <template v-if="item.type === 'nav'">
                        <ProjectDetailNavList :project-id="projectId" class="timeline-nav-list" />
                        <wa-divider class="timeline-nav-divider"></wa-divider>
                    </template>
                    <DaySeparator v-else-if="item.type === 'day'" :label="item.label" :day-key="item.dayKey" />
                    <TimelineEventRow v-else :row="item" :context="context" />
                </template>
            </VirtualScroller>

            <div v-if="state.loadingMore" class="timeline-footer" role="status">
                <wa-spinner></wa-spinner>
            </div>
            <div v-else-if="state.loadError" class="timeline-footer timeline-footer--error">
                <!-- wa-callout has a default slot and an icon slot only: the button goes in the default one. -->
                <wa-callout variant="danger">
                    <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                    <div class="timeline-error-content">
                        <span>Failed to load more events</span>
                        <wa-button variant="danger" appearance="outlined" size="small" @click="feed.retry()">
                            <wa-icon name="arrow-rotate-right" slot="start"></wa-icon>
                            Retry
                        </wa-button>
                    </div>
                </wa-callout>
            </div>
        </div>
    </div>
</template>

<style scoped>
.timeline-panel {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

/* The band of Stats' provider filter, outside the scroller: it never moves (design 3.3, 3.4). */
.timeline-band {
    position: relative;
    z-index: 2;
    flex: 0 0 auto;
    display: flex;
    justify-content: center;
    padding: var(--wa-space-xs) 0 var(--wa-space-m);
    --glass-sticky-bg: color-mix(in oklab, var(--surface-solid) 60%, transparent);
}

.timeline-band-label {
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
}

/* Without the blur the translucent layer would let the list show through unreadable: the card's colour. */
@media (prefers-reduced-transparency: reduce) {
    .timeline-band {
        --glass-sticky-bg: transparent;
        background: var(--panel-solid);
    }
}

.timeline-list {
    flex: 1;
    min-height: 0;
    position: relative;
    display: flex;
    flex-direction: column;
}

/* The scroller shares the column with the footer (spinner, error): it takes what is left. */
.timeline-list .timeline-scroller {
    flex: 1;
    min-height: 0;
}

/* The scroller pins itself to the bottom when the user is near it (native scroll anchoring on its sentinel).
   A newest-first list must never do that: loading older events would drag the view down. */
.timeline-list .timeline-scroller.at-bottom {
    overflow-anchor: none;
}

.timeline-state-area {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.timeline-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-2xl) var(--wa-space-l);
    color: var(--wa-color-text-quiet);
}

/* The navigation copy exists only in compact-height mode (the page header omits it then). */
.timeline-nav-list {
    display: none;
}

:where(html.compact-height) .timeline-nav-list {
    display: flex;
    padding: var(--wa-space-s) var(--wa-space-m);
}

.timeline-nav-divider {
    --spacing: 0;
    --width: var(--divider-size);
}

.timeline-footer {
    flex: 0 0 auto;
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s) var(--wa-space-l);
}

.timeline-footer--error {
    display: block;
}

.timeline-error-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-s);
    text-align: center;
}
</style>

<style>
/* "Reduce effects" has no blur either: the opaque card colour, as in the other no-blur modes. */
:root.reduce-effects .timeline-band {
    --glass-sticky-bg: transparent;
    background: var(--panel-solid);
}
</style>
```


- [ ] **Step 4: Run the tests and see them pass**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/components/timeline/*.test.js src/utils/timeline/*.test.js src/styles/glass.test.js src/styles/panel-veil.test.js
```

Expected: all pass (202 tests in the scratch run). Visual checks (compact mode, bottom pin, glass look) need a browser: they are in the final user checklist of Task 11.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add frontend/src/components/timeline/TimelinePanel.vue frontend/src/components/timeline/TimelinePanel.test.js frontend/src/composables/useTimelineFeed.js frontend/src/utils/timeline/providerLabels.js frontend/src/utils/timeline/providerLabels.test.js frontend/src/styles/glass.test.js frontend/src/styles/panel-veil.test.js
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
feat(timeline): add the Timeline panel

A sticky "Timeline" band outside the scroller, then the newest-first list in the simple
virtual scroller (bottom pinning disabled, a height estimate per row type (seeded after the render, only for rows the scroller has not measured), the next page
requested within ten rows of the end, an inline error with Retry, no load-more button).
The scope is resolved from the stores: a workspace sends every member and worktree
(archived ones included, as Stats does), an empty or not-yet-known scope shows its state
and sends nothing, an unknown project shows "Project not found". A change of the id set
reloads the first page.

In compact-height mode the navigation copy is the first row of the list, so it scrolls
away while the band stays. The panel is not mounted yet. The glass whitelist and the
no-blur fallback pins learn the new band.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 9: Routes, tabs, shortcuts, palette, scope memory

**Files:**
- Create: `frontend/src/utils/projectTabs.js`, `projectTabs.test.js`, `projectTabsRouting.test.js`
- Modify: `frontend/src/router.js`, `utils/tabRoutes.js`, `utils/scopeMemory.js`, `App.vue`, `commands/staticCommands.js`, `components/app/SettingsPopover.vue`, `components/sidebar/SidebarRail.vue`, `components/project/ProjectDetailPanel.vue`

**Interfaces:**
- Consumes (T8): `TimelinePanel.vue` (prop `projectId`). Existing: `utils/granularRoutes.js` (`buildTabRouteName`). `granularRoutes.js` itself needs no edit although the spec says it "learns the stats route": `buildTabRouteName({tab: 'stats'})` already yields `project-stats` / `projects-stats`, and `scopeMemory.rebuild` and `routeNameForTab` rely on that.
- Produces:
  - Routes `project-stats` (`/project/:projectId/stats`) and `projects-stats` (`/projects/stats`); the bare routes `project` and `projects-all` are the Timeline.
  - `projectTabs.js`: `PROJECT_TABS` (frozen list `{id, label, icon}`: `timeline` `clock-rotate-left`, `stats` `chart-simple`, `files`, `git`, `terminal`); `DIRECT_TAB_MAP` (`1 timeline, 2 stats, 3 files, 4 git, 5 terminal`); `visibleProjectTabs({hasGitRepo})`; `directTab(index, {hasGitRepo}) -> id | null`; `tabForRouteName(name) -> id` (bare and unknown names are `timeline`); `routeNameForTab(tab, {isAllProjectsMode}) -> string`.
  - `tabRoutes.js`: `PROJECT_DETAIL_ROUTES` (the 10 scope page route names), now shared by `App.vue` and the command registry.
  - Palette commands `nav.project-tab.timeline` and `nav.project-tab.stats`.

- [ ] **Step 1: Write the failing tests**

`projectTabs.test.js` pins the tab strip, the fixed shortcut map (key 4 is a no-op without a git root, key 5 stays Terminal, keys outside 1 to 5 open nothing), the route-name round trip. `projectTabsRouting.test.js` runs the real `scopeSelected` of the rail (Stats keeps the scope highlighted, Files does not), pins that a scope page without a git root leaves the Git route for the bare route (the Timeline), and pins the route table in `router.js`, cross-checks that every route the tabs can open exists in the router and in the shared set, checks `App.vue` and the registry use the shared set, the two palette commands and their gating, the settings text, the panel markup, and drives the real scope-memory guard with a memory router: a remembered Stats location is restored as Stats (project and workspace), a remembered Timeline is not restored as anything else, a switch Timeline to Stats is not a re-entry, and the rail still lands on the bare route.

`frontend/src/utils/projectTabs.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import { DIRECT_TAB_MAP, PROJECT_TABS, directTab, routeNameForTab, tabForRouteName, visibleProjectTabs } from './projectTabs.js'

test('the tab strip is Timeline, Stats, Files, Git (only with a git root), Terminal', () => {
    assert.deepEqual(visibleProjectTabs({ hasGitRepo: true }).map((tab) => tab.id), ['timeline', 'stats', 'files', 'git', 'terminal'])
    assert.deepEqual(visibleProjectTabs({ hasGitRepo: false }).map((tab) => tab.id), ['timeline', 'stats', 'files', 'terminal'])
    assert.equal(PROJECT_TABS[0].label, 'Timeline')
    assert.ok(PROJECT_TABS.every((tab) => tab.label && tab.icon))
})

test('the fixed shortcut map: 1 Timeline, 2 Stats, 3 Files, 4 Git, 5 Terminal', () => {
    assert.deepEqual({ ...DIRECT_TAB_MAP }, { 1: 'timeline', 2: 'stats', 3: 'files', 4: 'git', 5: 'terminal' })
    for (const [key, id] of [[1, 'timeline'], [2, 'stats'], [3, 'files'], [4, 'git'], [5, 'terminal']]) {
        assert.equal(directTab(key, { hasGitRepo: true }), id)
    }
})

test('without a git root key 4 does nothing and key 5 is still the Terminal', () => {
    assert.equal(directTab(4, { hasGitRepo: false }), null)
    assert.equal(directTab(5, { hasGitRepo: false }), 'terminal')
    assert.equal(directTab(3, { hasGitRepo: false }), 'files')
})

test('keys outside 1-5 open nothing', () => {
    for (const key of [0, 6, 9, undefined, 'x']) assert.equal(directTab(key, { hasGitRepo: true }), null)
})

test('the active tab follows the route name; the bare routes are the Timeline', () => {
    assert.equal(tabForRouteName('project'), 'timeline')
    assert.equal(tabForRouteName('projects-all'), 'timeline')
    assert.equal(tabForRouteName('project-stats'), 'stats')
    assert.equal(tabForRouteName('projects-stats'), 'stats')
    assert.equal(tabForRouteName('project-files'), 'files')
    assert.equal(tabForRouteName('projects-git'), 'git')
    assert.equal(tabForRouteName('projects-terminal'), 'terminal')
    assert.equal(tabForRouteName('something-else'), 'timeline')
    assert.equal(tabForRouteName('toString'), 'timeline')
    assert.equal(tabForRouteName(undefined), 'timeline')
})

test('a tab opens its route: bare for the Timeline, /stats and the tool routes otherwise', () => {
    assert.equal(routeNameForTab('timeline', { isAllProjectsMode: false }), 'project')
    assert.equal(routeNameForTab('timeline', { isAllProjectsMode: true }), 'projects-all')
    assert.equal(routeNameForTab('stats', { isAllProjectsMode: false }), 'project-stats')
    assert.equal(routeNameForTab('stats', { isAllProjectsMode: true }), 'projects-stats')
    assert.equal(routeNameForTab('files', { isAllProjectsMode: false }), 'project-files')
    assert.equal(routeNameForTab('terminal', { isAllProjectsMode: true }), 'projects-terminal')
})

test('every tab round-trips through its route name', () => {
    for (const isAllProjectsMode of [false, true]) {
        for (const tab of PROJECT_TABS) {
            assert.equal(tabForRouteName(routeNameForTab(tab.id, { isAllProjectsMode })), tab.id)
        }
    }
})
```


`frontend/src/utils/projectTabsRouting.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import { createMemoryHistory, createRouter } from 'vue-router'

import { createRailScopeNavigation } from './railScopeNavigation.js'
import { PROJECT_TABS, routeNameForTab, tabForRouteName } from './projectTabs.js'
import { registerScopeMemory } from './scopeMemory.js'
import { PROJECT_DETAIL_ROUTES } from './tabRoutes.js'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const routerSource = read('../router.js')
const routeNamesInRouter = new Set([...routerSource.matchAll(/name: '([^']+)'/g)].map((match) => match[1]))

// ── The route table ────────────────────────────────────────────────────────────────────────────────────

test('router.js has a /stats route per scope, next to the bare routes', () => {
    assert.match(routerSource, /\{ path: 'stats', name: 'project-stats', component: \{ render: \(\) => null \} \}/)
    assert.match(routerSource, /\{ path: 'stats', name: 'projects-stats', component: \{ render: \(\) => null \} \}/)
    assert.match(routerSource, /\{ path: '', name: 'project', component/)
    assert.match(routerSource, /\{ path: '', name: 'projects-all', component/)
})

test('every route name the tabs can open exists in the router, and the scope page set matches them', () => {
    for (const isAllProjectsMode of [false, true]) {
        for (const tab of PROJECT_TABS) {
            const name = routeNameForTab(tab.id, { isAllProjectsMode })
            assert.ok(routeNamesInRouter.has(name), name)
            assert.ok(PROJECT_DETAIL_ROUTES.has(name), `${name} is a scope page route`)
        }
    }
    assert.equal(PROJECT_DETAIL_ROUTES.size, 10)
    for (const name of PROJECT_DETAIL_ROUTES) assert.ok(routeNamesInRouter.has(name), name)
})

// ── Shared sets, shortcuts, palette, settings text ─────────────────────────────────────────────────────────

test('App.vue and the command registry use the shared set of scope page routes, not their own copy', () => {
    for (const file of ['../App.vue', '../commands/staticCommands.js']) {
        const source = read(file)
        assert.match(source, /import \{[^}]*PROJECT_DETAIL_ROUTES[^}]*\} from '(\.\.?)\/utils\/tabRoutes'/, file)
        assert.ok(!/const PROJECT_DETAIL_ROUTES = new Set/.test(source), `${file} has a private copy`)
    }
})

test('the palette: a Timeline command on the bare route and a Stats command on /stats, with the same gating', () => {
    const source = read('../commands/staticCommands.js')
    const block = (id) => source.slice(source.indexOf(`id: '${id}'`), source.indexOf('},\n        {', source.indexOf(`id: '${id}'`)))
    const timeline = block('nav.project-tab.timeline')
    const stats = block('nav.project-tab.stats')
    assert.match(timeline, /label: 'Switch to Timeline Tab'/)
    assert.match(timeline, /when: \(\) => isOnProjectDetail\(\)/)
    assert.match(timeline, /isAllProjectsMode\(\) \? 'projects-all' : 'project'/)
    assert.match(stats, /label: 'Switch to Stats Tab'/)
    assert.match(stats, /when: \(\) => isOnProjectDetail\(\)/)
    assert.match(stats, /isAllProjectsMode\(\) \? 'projects-stats' : 'project-stats'/)
    assert.ok(source.indexOf("id: 'nav.project-tab.timeline'") < source.indexOf("id: 'nav.project-tab.stats'"))
})

test('the settings panel text follows the fixed map', () => {
    const source = read('../components/app/SettingsPopover.vue')
    assert.match(source, /label: 'Project home tabs'/)
    assert.match(source, /keys: \['Alt', 'Shift', '1–5'\], description: 'Jump to tab \(Timeline, Stats, Files, Git, Terminal\)'/)
    assert.ok(!source.includes("'1–4'"))
})

test('the scope page mounts the Timeline first, takes its tabs, numbering and routes from projectTabs.js', () => {
    const source = read('../components/project/ProjectDetailPanel.vue')
    assert.ok(source.indexOf('<wa-tab-panel name="timeline">') < source.indexOf('<wa-tab-panel name="stats">'))
    assert.match(source, /<TimelinePanel :project-id="projectId" \/>/)
    assert.match(source, /visibleProjectTabs\(\{ hasGitRepo: hasGitRepo\.value \}\)/)
    assert.match(source, /directTab\(index, \{ hasGitRepo: hasGitRepo\.value \}\)/)
    assert.match(source, /tabForRouteName\(route\.name\)/)
    assert.match(source, /routeNameForTab\(tabId, \{ isAllProjectsMode: isAllProjectsMode\.value \}\)/)
    assert.ok(!source.includes('DIRECT_TAB_MAP'), 'the numbering lives in projectTabs.js only')
    assert.match(source, /wa-tab-panel\[name="timeline"\]\)::part\(base\),/)
})

test('the rail keeps a scope selected on its Stats tab as well as on its Timeline', () => {
    const source = read('../components/sidebar/SidebarRail.vue')
    const body = source.slice(source.indexOf('function scopeSelected'), source.indexOf('function scopeClick'))
    const selected = (route) => runInNewContext(`${body}; scopeSelected`, { route })
    const project = (name, query = {}) => selected({ name, params: { projectId: 'p' }, query })('project', 'p')
    assert.deepEqual(['project', 'project-stats', 'project-files'].map((name) => project(name)), [true, true, false])
    assert.equal(selected({ name: 'project-stats', params: { projectId: 'p' }, query: { workspace: 'w' } })('project', 'p'), false)
    const workspace = (name) => selected({ name, params: {}, query: { workspace: 'w' } })('workspace', 'w')
    assert.deepEqual(['projects-all', 'projects-stats', 'projects-files'].map(workspace), [true, true, false])
})

test('a scope page without a git root leaves the Git tab for the scope home, which is the Timeline', () => {
    const source = read('../components/project/ProjectDetailPanel.vue')
    const watcher = source.slice(source.indexOf("if (tabId === 'git' && !hasGit)"))
    assert.match(watcher.slice(0, 700), /router\.replace\(\{\s*name: buildProjectBaseRouteName\(isAllProjectsMode\.value\)/)
    assert.equal(routeNameForTab('timeline', { isAllProjectsMode: false }), 'project')
    assert.equal(tabForRouteName('project'), 'timeline')
    assert.ok(source.includes('which is now the Timeline'))
})

// ── Scope memory and the rail ──────────────────────────────────────────────────────────────────────────────
// The memory is module state shared by the whole file: every test uses its own project id.

async function scopeFixture() {
    const router = createRouter({ history: createMemoryHistory(), routes: [
        { path: '/', name: 'home', component: {} },
        { path: '/project/:projectId', name: 'project', component: {} },
        { path: '/project/:projectId/stats', name: 'project-stats', component: {} },
        { path: '/project/:projectId/files', name: 'project-files', component: {} },
        { path: '/projects', name: 'projects-all', component: {} },
        { path: '/projects/stats', name: 'projects-stats', component: {} },
    ] })
    await router.push('/')
    return router
}

async function withScopeMemory(run) {
    const router = await scopeFixture()
    const originalWindow = globalThis.window
    globalThis.window = { history: { state: { position: 0 } } }
    try {
        registerScopeMemory(router)
        await run(router)
    } finally {
        globalThis.window = originalWindow
    }
}

test('scope memory restores a remembered Stats location as Stats, for a project and for a workspace', async () => {
    await withScopeMemory(async (router) => {
        await router.push({ name: 'project-stats', params: { projectId: 'p' } })
        await router.push('/')
        await router.push({ name: 'project', params: { projectId: 'p' } })
        assert.equal(router.currentRoute.value.name, 'project-stats')
        await router.push('/')
        await router.push({ name: 'projects-stats', query: { workspace: 'w' } })
        await router.push('/')
        await router.push({ name: 'projects-all', query: { workspace: 'w' } })
        assert.deepEqual([router.currentRoute.value.name, router.currentRoute.value.query.workspace], ['projects-stats', 'w'])
    })
})

test('a remembered Timeline (the bare route) is not restored as anything else, and tool tabs still are', async () => {
    await withScopeMemory(async (router) => {
        await router.push({ name: 'project', params: { projectId: 'q' } })
        await router.push('/')
        await router.push({ name: 'project', params: { projectId: 'q' } })
        assert.equal(router.currentRoute.value.name, 'project')
        await router.push({ name: 'project-files', params: { projectId: 'q' } })
        await router.push('/')
        await router.push({ name: 'project', params: { projectId: 'q' } })
        assert.equal(router.currentRoute.value.name, 'project-files')
    })
})

test('switching between Timeline and Stats inside a scope is not a re-entry: the memory leaves it alone', async () => {
    await withScopeMemory(async (router) => {
        await router.push({ name: 'project-stats', params: { projectId: 'r' } })
        await router.push({ name: 'project', params: { projectId: 'r' } })
        assert.equal(router.currentRoute.value.name, 'project')
    })
})

test('the rail keeps pointing to the bare route, which is the Timeline, even after Stats was remembered', async () => {
    await withScopeMemory(async (router) => {
        await router.push({ name: 'project-stats', params: { projectId: 's' } })
        await router.push('/')
        await createRailScopeNavigation(router, () => {})('project', 's')
        assert.equal(router.currentRoute.value.name, 'project')
    })
})
```


- [ ] **Step 2: Run the tests and see them fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/utils/projectTabs.test.js src/utils/projectTabsRouting.test.js
```

Expected: `ERR_MODULE_NOT_FOUND` for `projectTabs.js`.

- [ ] **Step 3: Write the tab module and the routes**

`frontend/src/utils/projectTabs.js` (new file):

```javascript
// frontend/src/utils/projectTabs.js
//
// The tabs of a scope page (global, workspace, project, worktree) and how they map to routes and
// keys. Pure, so the node:test suite pins the numbering (display design sections 3.1 and 10).
//
//   Alt+Shift+1 Timeline, 2 Stats, 3 Files, 4 Git (only with a git root), 5 Terminal.
//
// Timeline is the BARE route of a scope (`project`, `projects-all`): an old link to a bare project URL
// opens it. Stats has its own `/stats` route.

import { buildTabRouteName } from './granularRoutes.js'

export const PROJECT_TABS = Object.freeze([
    { id: 'timeline', label: 'Timeline', icon: 'clock-rotate-left' },
    { id: 'stats', label: 'Stats', icon: 'chart-simple' },
    { id: 'files', label: 'Files', icon: 'folder' },
    { id: 'git', label: 'Git', icon: 'code-branch' },
    { id: 'terminal', label: 'Terminal', icon: 'terminal' },
])

/** Alt+Shift+digit → tab id. Fixed: a missing tab (Git without a git root) leaves its key unused. */
export const DIRECT_TAB_MAP = Object.freeze({ 1: 'timeline', 2: 'stats', 3: 'files', 4: 'git', 5: 'terminal' })

/** The tab strip: Git only with a git root. */
export function visibleProjectTabs({ hasGitRepo }) {
    return PROJECT_TABS.filter((tab) => tab.id !== 'git' || hasGitRepo)
}

/** The tab a direct shortcut opens, or null when the key is unused or its tab is missing. */
export function directTab(index, { hasGitRepo }) {
    const id = DIRECT_TAB_MAP[index] ?? null
    if (id === 'git' && !hasGitRepo) return null
    return id
}

const TAB_BY_ROUTE_NAME = {
    'project-stats': 'stats', 'projects-stats': 'stats',
    'project-files': 'files', 'projects-files': 'files',
    'project-git': 'git', 'projects-git': 'git',
    'project-terminal': 'terminal', 'projects-terminal': 'terminal',
}

/** The active tab, derived from the route name; the bare routes are the Timeline. */
export function tabForRouteName(name) {
    return Object.hasOwn(TAB_BY_ROUTE_NAME, name) ? TAB_BY_ROUTE_NAME[name] : 'timeline'
}

/** Route name of a tab: the bare route for the Timeline, `<prefix>-<tab>` for the others. */
export function routeNameForTab(tab, { isAllProjectsMode }) {
    if (tab === 'timeline') return isAllProjectsMode ? 'projects-all' : 'project'
    return buildTabRouteName({ isAllProjectsMode, isSessionRoute: false, tab })
}
```


`frontend/src/router.js` (modify):

```diff
--- a/frontend/src/router.js
+++ b/frontend/src/router.js
@@ -32,9 +32,11 @@ const routes = [
     {
         path: '/project/:projectId',
         component: ProjectView,
         children: [
+            // The bare route is the Timeline tab; Stats has its own route.
             { path: '', name: 'project', component: { render: () => null } },
+            { path: 'stats', name: 'project-stats', component: { render: () => null } },
             { path: 'files/:rootKey?/:filePath?', name: 'project-files', component: { render: () => null } },
             { path: 'artifacts/:bookmarkId?', name: 'project-artifacts', component: { render: () => null } },
             { path: 'git/:rootKey?/:commitRef?/:filePath?', name: 'project-git', component: { render: () => null } },
             { path: 'terminal/:termIndex?', name: 'project-terminal', component: { render: () => null } },
@@ -68,8 +70,9 @@ const routes = [
         path: '/projects',
         component: ProjectView,
         children: [
             { path: '', name: 'projects-all', component: { render: () => null } },
+            { path: 'stats', name: 'projects-stats', component: { render: () => null } },
             { path: 'files/:rootKey?/:filePath?', name: 'projects-files', component: { render: () => null } },
             { path: 'artifacts/:bookmarkId?', name: 'projects-artifacts', component: { render: () => null } },
             { path: 'git/:rootKey?/:commitRef?/:filePath?', name: 'projects-git', component: { render: () => null } },
             { path: 'terminal/:termIndex?', name: 'projects-terminal', component: { render: () => null } },
```


The scope page route names live in one shared set:

`frontend/src/utils/tabRoutes.js` (modify):

```diff
--- a/frontend/src/utils/tabRoutes.js
+++ b/frontend/src/utils/tabRoutes.js
@@ -1,8 +1,16 @@
 // Route names where per-tab navigation applies, shared by App.vue (the
 // Alt+Ctrl+Shift keydown dispatch) and the command registry (route-gated
 // "Go to … tab" commands), so the gate stays defined in one place.
 
+// Every route of a scope page (global, workspace, project, worktree): the bare route (Timeline) and the
+// tab routes (Stats, Files, Git, Terminal). Shared by App.vue (the Alt+Shift+digit dispatch) and the
+// command registry (the "Switch to ... Tab" commands).
+export const PROJECT_DETAIL_ROUTES = new Set([
+    'project', 'project-stats', 'project-files', 'project-git', 'project-terminal',
+    'projects-all', 'projects-stats', 'projects-files', 'projects-git', 'projects-terminal',
+])
+
 // Terminal panel routes — session + project/worktree scopes.
 export const TERMINAL_ROUTES = new Set([
     'session-terminal', 'projects-session-terminal',
     'project-terminal', 'projects-terminal',
```


Scope memory must know the new names, so a remembered Stats location is restored as Stats (the bare route stays the "nothing better" home):

`frontend/src/utils/scopeMemory.js` (modify):

```diff
--- a/frontend/src/utils/scopeMemory.js
+++ b/frontend/src/utils/scopeMemory.js
@@ -27,12 +27,12 @@ const SESSION_NAMES = new Set([
     'projects-session-terminal', 'projects-session-artifacts',
     'projects-session-orchestration', 'projects-session-plan', 'projects-session-tasks', 'projects-session-workflows', 'projects-session-browser', 'projects-session-subagent',
 ])
 const PROJECT_NAMES = new Set([
-    'project', 'project-files', 'project-git', 'project-terminal', 'project-artifacts',
+    'project', 'project-stats', 'project-files', 'project-git', 'project-terminal', 'project-artifacts',
 ])
 const ALL_PROJECTS_NAMES = new Set([
-    'projects-all', 'projects-files', 'projects-git', 'projects-terminal', 'projects-artifacts',
+    'projects-all', 'projects-stats', 'projects-files', 'projects-git', 'projects-terminal', 'projects-artifacts',
 ])
 // Bare entry routes (homes) per scope family.
 const HOME_NAMES = new Set(['session', 'projects-session', 'project', 'projects-all'])
 
@@ -60,15 +60,15 @@ export function scopeKey(route) {
     }
     return null
 }
 
-// True only for a scope's bare entry route (Chat / project stats / All Projects list).
+// True only for a scope's bare entry route (Chat / project Timeline / All Projects Timeline).
 export function isScopeHome(route) {
     return HOME_NAMES.has(route?.name)
 }
 
 // The sub-screen id within a scope: '' for the base, else the tool tab / 'subagent'.
-// Assumes tab ids are single-segment (no hyphen): files/git/terminal/artifacts/
+// Assumes tab ids are single-segment (no hyphen): stats/files/git/terminal/artifacts/
 // orchestration/subagent. A future hyphenated tab name would need a different split.
 export function tabOf(name) {
     if (HOME_NAMES.has(name)) return ''
     return name.split('-').pop()
```


`App.vue` and the command registry import the shared set instead of keeping a private copy, and the registry gets the Timeline command and re-targets the Stats one:

`frontend/src/App.vue` (modify):

```diff
--- a/frontend/src/App.vue
+++ b/frontend/src/App.vue
@@ -46,9 +46,9 @@ import {
     hardKillSessionProcess,
 } from './composables/useStopSessionProcess'
 import { canStealFocus, hasBlockingOverlay } from './utils/focusGuard'
 import { focusChatPrimary, gotoChatFooterPanel } from './utils/focusChat'
-import { TERMINAL_ROUTES, WORKFLOW_ROUTES } from './utils/tabRoutes'
+import { PROJECT_DETAIL_ROUTES, TERMINAL_ROUTES, WORKFLOW_ROUTES } from './utils/tabRoutes'
 import { toggleSearchInActiveCodeMirror } from './composables/useCodeMirror'
 import { useSessionSwitcher } from './composables/useSessionSwitcher'
 import BrandLogo from './components/ui/BrandLogo.vue'
 
@@ -203,13 +203,10 @@ const SESSION_ROUTES = new Set([
     'session', 'session-subagent', 'session-files', 'session-artifacts', 'session-git', 'session-terminal', 'session-orchestration', 'session-plan', 'session-tasks', 'session-workflows', 'session-browser',
     'projects-session', 'projects-session-subagent', 'projects-session-files', 'projects-session-artifacts', 'projects-session-git', 'projects-session-terminal', 'projects-session-orchestration', 'projects-session-plan', 'projects-session-tasks', 'projects-session-workflows', 'projects-session-browser',
 ])
 
-// Project detail route names (for tab keyboard shortcuts: Alt+Shift+{1-4, ←, →, ↑})
-const PROJECT_DETAIL_ROUTES = new Set([
-    'project', 'project-files', 'project-git', 'project-terminal',
-    'projects-all', 'projects-files', 'projects-git', 'projects-terminal',
-])
+// Project detail route names (for tab keyboard shortcuts: Alt+Shift+{1-5, ←, →, ↑}) come from
+// ./utils/tabRoutes, shared with the command registry.
 
 // TERMINAL_ROUTES / WORKFLOW_ROUTES come from ./utils/tabRoutes — shared with the
 // command registry's route-gated "Go to … tab" commands (imported above).
 
```


`frontend/src/commands/staticCommands.js` (modify):

```diff
--- a/frontend/src/commands/staticCommands.js
+++ b/frontend/src/commands/staticCommands.js
@@ -34,9 +34,9 @@ import { useWorkflowRunsStore } from '../stores/workflowRuns'
 import { usePeersStore } from '../stores/peers'
 import { useHelpStore } from '../stores/help'
 import { usePeerSystemConfigured } from '../composables/usePeerSystemConfigured'
 import { showHelp } from '../components/help/showHelp'
-import { TERMINAL_ROUTES, WORKFLOW_ROUTES } from '../utils/tabRoutes'
+import { PROJECT_DETAIL_ROUTES, TERMINAL_ROUTES, WORKFLOW_ROUTES } from '../utils/tabRoutes'
 import {
     DISPLAY_MODE,
     COLOR_SCHEME,
 } from '../constants'
@@ -423,13 +423,8 @@ export function initStaticCommands(router) {
     function workflowRunList() {
         return workflowRunsStore.getRuns(routeSessionId())
     }
 
-    const PROJECT_DETAIL_ROUTES = new Set([
-        'project', 'project-files', 'project-git', 'project-terminal',
-        'projects-all', 'projects-files', 'projects-git', 'projects-terminal',
-    ])
-
     /** Whether the current route is on a project detail panel (not a session) */
     function isOnProjectDetail() {
         return PROJECT_DETAIL_ROUTES.has(route.name)
     }
@@ -947,16 +942,32 @@ export function initStaticCommands(router) {
             },
         },
 
         // Project detail panel tabs
+        {
+            id: 'nav.project-tab.timeline',
+            label: 'Switch to Timeline Tab',
+            icon: 'clock-rotate-left',
+            category: 'navigation',
+            when: () => isOnProjectDetail(),
+            action: () => {
+                // The bare route of the scope is the Timeline.
+                const name = isAllProjectsMode() ? 'projects-all' : 'project'
+                router.push({
+                    name,
+                    params: clearTabRouteParams('files', isAllProjectsMode() ? {} : { projectId: route.params.projectId }),
+                    query: route.query,
+                })
+            },
+        },
         {
             id: 'nav.project-tab.stats',
             label: 'Switch to Stats Tab',
             icon: 'chart-line',
             category: 'navigation',
             when: () => isOnProjectDetail(),
             action: () => {
-                const name = isAllProjectsMode() ? 'projects-all' : 'project'
+                const name = isAllProjectsMode() ? 'projects-stats' : 'project-stats'
                 router.push({
                     name,
                     params: clearTabRouteParams('files', isAllProjectsMode() ? {} : { projectId: route.params.projectId }),
                     query: route.query,
```


The settings panel text follows the fixed map:

`frontend/src/components/app/SettingsPopover.vue` (modify):

```diff
--- a/frontend/src/components/app/SettingsPopover.vue
+++ b/frontend/src/components/app/SettingsPopover.vue
@@ -377,9 +377,9 @@ const shortcutGroups = computed(() => {
         },
         {
             label: 'Project home tabs',
             shortcuts: [
-                { keys: ['Alt', 'Shift', '1–4'], description: 'Jump to tab (Stats, Files, Git, Terminal)' },
+                { keys: ['Alt', 'Shift', '1–5'], description: 'Jump to tab (Timeline, Stats, Files, Git, Terminal)' },
                 { keys: ['Alt', 'Shift', '←/→'], description: 'Previous / next tab' },
                 { keys: ['Alt', 'Shift', '↑/↓'], description: 'Last visited tab' },
             ]
         },
```


The rail keeps a scope highlighted on its Stats tab too (the bare route used to be Stats; decision P16):

`frontend/src/components/sidebar/SidebarRail.vue` (modify):

```diff
--- a/frontend/src/components/sidebar/SidebarRail.vue
+++ b/frontend/src/components/sidebar/SidebarRail.vue
@@ -39,11 +39,12 @@ const { rows } = useRailActiveSessions(store, () => currentSessionId.value)
 
 const hasCentralContent = computed(() => rows.value.length || recentProjects.value.length || recentWorkspaces.value.length)
 const navigateScope = createRailScopeNavigation(router)
 function scopeSelected(kind, id) {
+    // The bare route is the Timeline; Stats is the same scope page on another tab, so the scope stays selected.
     return kind === 'project'
-        ? route.name === 'project' && route.params.projectId === id && !route.query.workspace
-        : route.name === 'projects-all' && route.query.workspace === id
+        ? (route.name === 'project' || route.name === 'project-stats') && route.params.projectId === id && !route.query.workspace
+        : (route.name === 'projects-all' || route.name === 'projects-stats') && route.query.workspace === id
 }
 function scopeClick(kind, id, event) {
     if (longPress.consumeClick(`${kind}:${id}`, event)) return
     longPress.cancel()
```


The scope page takes its tabs, numbering and routes from `projectTabs.js`, mounts the Timeline as the first tab panel and gives it the `overflow-y: hidden` rule that the Files, Git and Terminal panels have (the Timeline owns its scroll):

`frontend/src/components/project/ProjectDetailPanel.vue` (modify):

```diff
--- a/frontend/src/components/project/ProjectDetailPanel.vue
+++ b/frontend/src/components/project/ProjectDetailPanel.vue
@@ -13,8 +13,9 @@ import ProjectDetailNavList from './ProjectDetailNavList.vue'
 import ContributionGraphs from '../activity/ContributionGraphs.vue'
 import FilesPanel from '../files/FilesPanel.vue'
 import GitPanel from '../git/GitPanel.vue'
 import TerminalPanel from '../terminal/TerminalPanel.vue'
+import TimelinePanel from '../timeline/TimelinePanel.vue'
 import TabBar from '../ui/TabBar.vue'
 import UploadTabStatus from '../files/UploadTabStatus.vue'
 import { useUploadsStore } from '../../stores/uploads'
 import { originKey } from '../../utils/uploads/rules'
@@ -30,8 +31,9 @@ import {
     decodePath,
     parseRouteString,
     parseRouteTermIndex,
 } from '../../utils/granularRoutes'
+import { directTab, routeNameForTab, tabForRouteName, visibleProjectTabs } from '../../utils/projectTabs'
 
 const props = defineProps({
     /** Project ID or ALL_PROJECTS_ID for aggregate view */
     projectId: {
@@ -231,28 +233,13 @@ const hasGitRepo = computed(() => {
     if (!isSingleProjectMode.value) return false
     return !!dataStore.getProject(props.projectId)?.git_root
 })
 
-const TABS = computed(() => {
-    const tabs = [
-        { id: 'stats', label: 'Stats', icon: 'chart-simple' },
-        { id: 'files', label: 'Files', icon: 'folder' },
-    ]
-    if (hasGitRepo.value) {
-        tabs.push({ id: 'git', label: 'Git', icon: 'code-branch' })
-    }
-    tabs.push({ id: 'terminal', label: 'Terminal', icon: 'terminal' })
-    return tabs
-})
+// Timeline, Stats, Files, Git (only with a git root), Terminal — see utils/projectTabs.js.
+const TABS = computed(() => visibleProjectTabs({ hasGitRepo: hasGitRepo.value }))
 
-// Active tab derived from the route name
-const activeTab = computed(() => {
-    const name = route.name
-    if (name === 'project-files' || name === 'projects-files') return 'files'
-    if (name === 'project-git' || name === 'projects-git') return 'git'
-    if (name === 'project-terminal' || name === 'projects-terminal') return 'terminal'
-    return 'stats'
-})
+// Active tab derived from the route name; the bare routes are the Timeline.
+const activeTab = computed(() => tabForRouteName(route.name))
 const filesRouteRootKey = computed(() => parseRouteString(route.params.rootKey))
 const filesRouteFilePath = computed(() => {
     const decoded = decodePath(parseRouteString(route.params.filePath))
     return decoded === null ? null : decoded
@@ -268,8 +255,9 @@ const terminalRouteTermIndex = computed(() => parseRouteTermIndex(route.params.t
 watch([activeTab, hasGitRepo], ([tabId, hasGit]) => {
     if (tabId === 'git' && !hasGit) {
         if (!isActive.value) return
         if (!dataStore.getProject(props.projectId)) return
+        // No git root, no Git tab: leave for the scope's home, which is now the Timeline (it used to be Stats).
         router.replace({
             name: buildProjectBaseRouteName(isAllProjectsMode.value),
             params: isAllProjectsMode.value ? {} : { projectId: props.projectId },
             query: route.query,
@@ -372,11 +360,11 @@ function switchToTab(tabId) {
     if (tabId === activeTab.value) return
     if (TOOL_TAB_IDS.includes(tabId)) {
         navigateInTab(tabId, rememberedToolTabRoutes[tabId] ?? {})
     } else {
-        // Stats = default route (no suffix)
+        // Timeline = the bare route of the scope, Stats = its /stats route.
         router.push({
-            name: buildProjectBaseRouteName(isAllProjectsMode.value),
+            name: routeNameForTab(tabId, { isAllProjectsMode: isAllProjectsMode.value }),
             params: isAllProjectsMode.value ? {} : { projectId: props.projectId },
             query: route.query,
         })
     }
@@ -388,9 +376,9 @@ function onTabShow(event) {
     if (panel && TABS.value.some(t => t.id === panel)) switchToTab(panel)
 }
 
 // ═══════════════════════════════════════════════════════════════════════════
-// Keyboard shortcuts: tab navigation (Alt+Shift+1-4, ←/→, ↑)
+// Keyboard shortcuts: tab navigation (Alt+Shift+1-5, ←/→, ↑)
 // Events dispatched by App.vue, handled here by the active instance only.
 // ═══════════════════════════════════════════════════════════════════════════
 
 // Ordered list of all visible tabs (for sequential ←/→ navigation).
@@ -410,21 +398,18 @@ watch(activeTab, (newTabId, oldTabId) => {
     if (!isActive.value) return
     if (oldTabId) pushTabHistory(oldTabId)
 })
 
-// Direct tab mapping: Alt+Shift+{1,2,3,4} → fixed tabs (matches session pattern)
-const DIRECT_TAB_MAP = { 1: 'stats', 2: 'files', 3: 'git', 4: 'terminal' }
-
 function handleTabShortcut(event) {
     if (!isActive.value) return
 
     const { type, index } = event.detail
     let targetTab = null
 
     if (type === 'direct') {
-        targetTab = DIRECT_TAB_MAP[index]
+        // Alt+Shift+{1..5} → fixed tabs; 4 (Git) does nothing without a git root, 5 stays the Terminal.
+        targetTab = directTab(index, { hasGitRepo: hasGitRepo.value })
         if (!targetTab) return
-        if (targetTab === 'git' && !hasGitRepo.value) return
     } else if (type === 'prev' || type === 'next') {
         const tabs = orderedTabs.value
         const currentIndex = tabs.indexOf(activeTab.value)
         if (currentIndex === -1) return
@@ -471,8 +456,12 @@ onBeforeUnmount(() => {
                 {{ tab.label }}
                 <UploadTabStatus v-if="tab.id === 'files'" :status="filesUploadStatus" />
             </wa-tab>
 
+            <wa-tab-panel name="timeline">
+                <TimelinePanel :project-id="projectId" />
+            </wa-tab-panel>
+
             <wa-tab-panel name="stats">
                 <ProjectDetailNavList :project-id="projectId" class="stats-nav-list" />
                 <wa-divider class="stats-nav-list-divider"></wa-divider>
                 <ContributionGraphs :project-id="projectId" :project-ids="statsProjectIds" />
@@ -587,8 +576,9 @@ wa-divider {
     flex-direction: column;
     overflow-y: auto;
 }
 
+.detail-tabs :deep(wa-tab-panel[name="timeline"])::part(base),
 .detail-tabs :deep(wa-tab-panel[name="files"])::part(base),
 .detail-tabs :deep(wa-tab-panel[name="git"])::part(base),
 .detail-tabs :deep(wa-tab-panel[name="terminal"])::part(base) {
     overflow-y: hidden;
```


- [ ] **Step 4: Run the tests and see them pass, then review the suites that pin the Stats layout**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/utils/projectTabs.test.js src/utils/projectTabsRouting.test.js
```

Expected: all pass. The spec names nine existing suites that pin the markup the move could touch. None of them needs an edit: the Stats tab keeps its markup (`ContributionGraphs`, `.provider-filter glass-sticky`, `.stats-nav-list`); the tab strip class and the compact-height rules of `ProjectDetailPanel.vue` are untouched. Run them and the whole frontend suite:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/styles/main-content-height.test.js src/styles/panel-veil.test.js src/styles/glass.test.js src/styles/compact-header-panel.test.js src/utils/compactHeight.test.js src/styles/home-motion.test.js src/styles/stats-motion.test.js src/components/project/projectActivityUi.test.js src/utils/railScopeNavigation.test.js
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && npm test
```

Expected: all pass. (Known environment failures: the tests under `tests/browser/*` that read git history (`prepareMarkdownRenderingBaseline.test.js`, `scrollerGeometryFixture.test.js`, ...) fail in a copy made without the git history, identically on the base commit; a reviewer's copy may show only some of them. They pass in the worktree. A new failure anywhere else is a real finding.)

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add frontend/src/utils/projectTabs.js frontend/src/utils/projectTabs.test.js frontend/src/utils/projectTabsRouting.test.js frontend/src/router.js frontend/src/utils/tabRoutes.js frontend/src/utils/scopeMemory.js frontend/src/App.vue frontend/src/commands/staticCommands.js frontend/src/components/app/SettingsPopover.vue frontend/src/components/sidebar/SidebarRail.vue frontend/src/components/project/ProjectDetailPanel.vue
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
feat(timeline): make Timeline the first tab and move Stats to /stats

Every scope page (global, workspace, project, worktree) now opens on the Timeline tab:
the bare routes are the Timeline and Stats has its own project-stats / projects-stats
route. The tab strip is Timeline, Stats, Files, Git (only with a git root), Terminal,
derived from the route name, with the fixed Alt+Shift+digit map 1 Timeline, 2 Stats,
3 Files, 4 Git, 5 Terminal (key 4 does nothing without a git root). The numbering, the
tab list and the route names live in one pure module.

Scope memory knows the stats routes, so a remembered Stats location is restored as
Stats. The command palette gets "Switch to Timeline Tab" and its Stats command targets
/stats. The settings panel text follows the new map. Old bare project links now open the
Timeline, as the design accepts.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 10: Live updates (signal, first OPEN, tab return, scroll)

**Files:**
- Create: `frontend/src/utils/timeline/liveEvents.js`, `liveEvents.test.js`, `scrollKeeper.js`, `scrollKeeper.test.js`
- Modify: `frontend/src/components/timeline/TimelinePanel.vue` (+ `TimelinePanel.test.js`), `frontend/src/composables/useWebSocket.js`, `frontend/src/components/project/ProjectDetailPanel.vue` (+ the pin in `utils/projectTabsRouting.test.js`)

**Interfaces:**
- Consumes: T3 (the frame `{type: 'timeline_changed', latest_id}`), T6 (`feed.notifyLatest`, `feed.refresh`), T8 (the panel).
- Produces:
  - Window events: `twicc:timeline-changed` with `detail.latestId` (new, from the frame), `twicc:ws-first-open` (new, dispatched when the socket first reaches OPEN), `twicc:ws-reconnected` (existing).
  - `liveEvents.js`: `TIMELINE_CHANGED_EVENT`, `WS_RECONNECTED_EVENT`, `WS_FIRST_OPEN_EVENT`, `bindTimelineEvents(target, feed) -> unbind`.
  - `scrollKeeper.js`: `TOP_EPSILON = 2`, `planScrollRestore(snapshot, indexByKey) -> 'none' | 'anchor' | 'top'`, `createScrollKeeper(getScroller) -> {capture(), restore(snapshot, indexByKey)}`.
  - `TimelinePanel.vue`: new prop `active` (boolean, default true); the page binds the window events on mount and removes them on unmount; becoming `active` triggers `feed.refresh()`.

- [ ] **Step 1: Write the failing tests**

The two new suites are pure. The panel suite (its scroller double exists since Task 8; here it gains a model of what is RENDERED: its first visible row is read from the rows rendered at that moment, and a restore records the rows rendered when it is called, which makes the capture-before / restore-after order observable) grows a window-listener counter (it shims `window` as an `EventTarget` that counts `addEventListener`/`removeEventListener` per type), the two helper modules' imports, an `active` prop in its mount helper, and a "Staying current" section: a new id fetches once and shows at the top, a known id fetches nothing, reconnect plus first OPEN make one fetch, resync does nothing, returning to the tab catches up once and leaving fetches nothing, an unmounted page releases its listeners, rows inserted above a scrolled-down view put the anchor back (captured before the render, restored after it) while the top is left alone, and source pins for the WebSocket wiring and for the shape of the scroll-keeper watcher (`flush: 'pre'`, `await nextTick()` before the restore).

`frontend/src/utils/timeline/liveEvents.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import {
    TIMELINE_CHANGED_EVENT, WS_FIRST_OPEN_EVENT, WS_RECONNECTED_EVENT, bindTimelineEvents,
} from './liveEvents.js'

function setup() {
    const target = new EventTarget()
    const calls = []
    const feed = { notifyLatest: (id) => calls.push(['latest', id]), refresh: () => calls.push(['refresh']) }
    return { target, calls, unbind: bindTimelineEvents(target, feed) }
}

test('the event names are the ones useWebSocket.js dispatches', () => {
    assert.deepEqual([TIMELINE_CHANGED_EVENT, WS_RECONNECTED_EVENT, WS_FIRST_OPEN_EVENT], [
        'twicc:timeline-changed', 'twicc:ws-reconnected', 'twicc:ws-first-open',
    ])
})

test('a signal hands its latest id to the feed', () => {
    const { target, calls } = setup()
    target.dispatchEvent(new CustomEvent(TIMELINE_CHANGED_EVENT, { detail: { latestId: 42 } }))
    assert.deepEqual(calls, [['latest', 42]])
})

test('a malformed signal is ignored', () => {
    const { target, calls } = setup()
    for (const detail of [undefined, {}, { latestId: null }, { latestId: '7' }, { latestId: 1.5 }]) {
        target.dispatchEvent(new CustomEvent(TIMELINE_CHANGED_EVENT, { detail }))
    }
    assert.deepEqual(calls, [])
})

test('a reconnection and the first OPEN of the socket both refresh the feed', () => {
    const { target, calls } = setup()
    target.dispatchEvent(new Event(WS_RECONNECTED_EVENT))
    target.dispatchEvent(new Event(WS_FIRST_OPEN_EVENT))
    assert.deepEqual(calls, [['refresh'], ['refresh']])
})

test('resync_required is not a timeline event: nothing else is listened to', () => {
    const { target, calls } = setup()
    for (const name of ['twicc:resync-required', 'resync_required', 'twicc:ws-open', 'twicc:ws-closed']) {
        target.dispatchEvent(new Event(name))
    }
    assert.deepEqual(calls, [])
})

test('unbinding removes every listener', () => {
    const { target, calls, unbind } = setup()
    unbind()
    target.dispatchEvent(new CustomEvent(TIMELINE_CHANGED_EVENT, { detail: { latestId: 1 } }))
    target.dispatchEvent(new Event(WS_RECONNECTED_EVENT))
    target.dispatchEvent(new Event(WS_FIRST_OPEN_EVENT))
    assert.deepEqual(calls, [])
})
```


`frontend/src/utils/timeline/scrollKeeper.test.js` (new file):

```javascript
import assert from 'node:assert/strict'
import test from 'node:test'

import { TOP_EPSILON, createScrollKeeper, planScrollRestore } from './scrollKeeper.js'

function fakeScroller({ scrollTop = 0, anchor = null } = {}) {
    const calls = []
    return {
        calls,
        getScrollState: () => ({ scrollTop }),
        getScrollAnchor: () => anchor,
        scrollToAnchor: (value) => calls.push(['anchor', value]),
        scrollToTop: () => calls.push(['top']),
    }
}

const anchor = { key: 'event-7', index: 3, offset: 12 }

const indexes = (...keys) => new Map(keys.map((key, index) => [key, index]))

test('at the top nothing is restored: new rows appear at the top', () => {
    const scroller = fakeScroller({ scrollTop: 0, anchor })
    const keeper = createScrollKeeper(() => scroller)
    const snapshot = keeper.capture()
    assert.equal(snapshot.atTop, true)
    keeper.restore(snapshot, indexes('a', 'b', 'c', 'event-7'))
    assert.deepEqual(scroller.calls, [])
})

test('a few pixels from the top still counts as the top', () => {
    const scroller = fakeScroller({ scrollTop: TOP_EPSILON, anchor })
    assert.equal(createScrollKeeper(() => scroller).capture().atTop, true)
    assert.equal(createScrollKeeper(() => fakeScroller({ scrollTop: TOP_EPSILON + 1, anchor })).capture().atTop, false)
})

test('scrolled down, rows inserted above: the first visible row is put back where it was', () => {
    const scroller = fakeScroller({ scrollTop: 500, anchor })
    const keeper = createScrollKeeper(() => scroller)
    const snapshot = keeper.capture()
    keeper.restore(snapshot, indexes('n1', 'n2', 'event-9', 'event-8', 'event-7')) // event-7 was at index 3, now 4
    assert.deepEqual(scroller.calls, [['anchor', anchor]])
})

test('scrolled down, rows added below (an older page): the scroll is left alone', () => {
    const scroller = fakeScroller({ scrollTop: 500, anchor })
    const keeper = createScrollKeeper(() => scroller)
    const snapshot = keeper.capture()
    keeper.restore(snapshot, indexes('a', 'b', 'c', 'event-7', 'older-1', 'older-2'))
    assert.deepEqual(scroller.calls, [])
})

test('the row being read is gone (another scope): back to the top', () => {
    const scroller = fakeScroller({ scrollTop: 500, anchor })
    const keeper = createScrollKeeper(() => scroller)
    keeper.restore(keeper.capture(), indexes('event-100'))
    assert.deepEqual(scroller.calls, [['top']])
})

test('no anchor (an empty list) or no scroller: nothing happens', () => {
    const empty = fakeScroller({ scrollTop: 500, anchor: null })
    const keeper = createScrollKeeper(() => empty)
    keeper.restore(keeper.capture(), indexes('event-1'))
    assert.deepEqual(empty.calls, [])
    const none = createScrollKeeper(() => null)
    assert.equal(none.capture(), null)
    assert.doesNotThrow(() => none.restore(null, new Map()))
})

test('planScrollRestore', () => {
    assert.equal(planScrollRestore(null, new Map()), 'none')
    assert.equal(planScrollRestore({ atTop: true, anchor }, indexes('a', 'b', 'c', 'event-7')), 'none')
    assert.equal(planScrollRestore({ atTop: false, anchor: null }, indexes('event-7')), 'none')
    assert.equal(planScrollRestore({ atTop: false, anchor }, indexes('a', 'b', 'c', 'd', 'event-7')), 'anchor')
    assert.equal(planScrollRestore({ atTop: false, anchor }, indexes('a', 'b', 'c', 'event-7')), 'none')
    assert.equal(planScrollRestore({ atTop: false, anchor }, new Map()), 'top')
})
```


`frontend/src/components/timeline/TimelinePanel.test.js` (modify):

```diff
--- a/frontend/src/components/timeline/TimelinePanel.test.js
+++ b/frontend/src/components/timeline/TimelinePanel.test.js
@@ -4,11 +4,26 @@ import test from 'node:test'
 import * as Vue from 'vue'
 
 import { compileComponent, descendants, flush, makeRenderer, virtualScroller } from '../../../tests/helpers/scrollerComponentHarness.js'
 import { createFeedState, createTimelineFeed } from '../../utils/timeline/feed.js'
+import * as liveEventsModule from '../../utils/timeline/liveEvents.js'
 import * as providerLabels from '../../utils/timeline/providerLabels.js'
 import * as rowsModule from '../../utils/timeline/rows.js'
 import * as scopeModule from '../../utils/timeline/scope.js'
+import * as scrollKeeperModule from '../../utils/timeline/scrollKeeper.js'
+
+// The panel binds window events when it mounts: count the listeners to see them removed.
+const windowListeners = new Map()
+globalThis.window = new EventTarget()
+for (const [method, step] of [['addEventListener', 1], ['removeEventListener', -1]]) {
+    const original = window[method].bind(window)
+    window[method] = (type, listener, options) => {
+        windowListeners.set(type, (windowListeners.get(type) ?? 0) + step)
+        original(type, listener, options)
+    }
+}
+const TIMELINE_EVENT_TYPES = ['twicc:timeline-changed', 'twicc:ws-reconnected', 'twicc:ws-first-open']
+const listenerCounts = () => Object.fromEntries(TIMELINE_EVENT_TYPES.map((type) => [type, windowListeners.get(type) ?? 0]))
 
 const RouterLink = { props: ['to'], setup: (props, { slots }) => () => Vue.h('a', { 'data-to': JSON.stringify(props.to) }, slots.default?.()) }
 const stub = (tag, textProp) => ({ props: ['projectId', 'label', 'row', 'dayKey'], setup: (props) => () => Vue.h(tag, { 'data-id': props.row?.key ?? props.projectId }, textProp ? props[textProp] : undefined) })
 
@@ -92,8 +107,10 @@ function fixture({ events = [event(3), event(2), event(1)], hasMore = false, pro
         '../../stores/workspaces': { useWorkspacesStore: () => workspacesStore },
         '../../providers': { getProviderHelpers: () => null },
         '../../utils/compactHeight': { compactHeight },
         '../../utils/timeline/rows': rowsModule,
+        '../../utils/timeline/liveEvents': liveEventsModule,
+        '../../utils/timeline/scrollKeeper': scrollKeeperModule,
         '../../utils/timeline/providerLabels': providerLabels,
         '../../utils/timeline/scope': scopeModule,
         '../project/ProjectDetailNavList.vue': stub('nav'),
         '../session/detail/items/DaySeparator.vue': { props: ['label', 'dayKey'], setup: (props) => () => Vue.h('div', { class: 'day', 'data-day': props.dayKey }, props.label) },
@@ -108,16 +125,17 @@ const settle = async () => {
     await new Promise((resolve) => setTimeout(resolve, 5))
     await flush()
 }
 
-async function mount(t, f, projectId = '__all__') {
+async function mount(t, f, projectId = '__all__', { active = true } = {}) {
     const { renderer, root } = makeRenderer(600)
-    const props = Vue.reactive({ projectId })
+    const props = Vue.reactive({ projectId, active })
     const app = renderer.createApp({ render: () => Vue.h(f.Panel, props) })
     app.mount(root)
     let mounted = true
     t.after(() => { if (mounted) app.unmount() })
     await settle()
+    root.setActive = async (value) => { props.active = value; await settle() }
     root.unmount = () => { app.unmount(); mounted = false }
     return root
 }
 
@@ -392,4 +410,110 @@ test('source pins: the band is outside the scroller, the router is not imported,
     assert.match(source, /prevent-auto-scroll-to-bottom/)
     assert.match(source, /@media \(prefers-reduced-transparency: reduce\) \{\s*\.timeline-band \{\s*--glass-sticky-bg: transparent;\s*background: var\(--panel-solid\);/)
     assert.match(source, /:root\.reduce-effects \.timeline-band \{\s*--glass-sticky-bg: transparent;\s*background: var\(--panel-solid\);/)
 })
+
+// ── Staying current ─────────────────────────────────────────────────────────────────────────────────────────
+
+const signal = (latestId) => window.dispatchEvent(new CustomEvent('twicc:timeline-changed', { detail: { latestId } }))
+
+test('a signal with a new id fetches what is new once and shows it at the top', async (t) => {
+    const f = fixture({ fakeScroller: true })
+    const root = await mount(t, f)
+    f.server.respond = ((respond) => (params) => (params.afterId !== null
+        ? { events: [event(4)], has_more: false, truncated: false, latest_id: 4, sessions: {} }
+        : respond(params)))(f.server.respond)
+    signal(4)
+    signal(4)
+    await settle()
+    assert.deepEqual(f.calls.map((c) => c.afterId), [null, 3])
+    assert.deepEqual(rowIds(root), ['event-4', 'event-3', 'event-2', 'event-1'])
+})
+
+test('a signal with a known id does nothing', async (t) => {
+    const f = fixture({ fakeScroller: true })
+    await mount(t, f)
+    signal(3)
+    await settle()
+    assert.equal(f.calls.length, 1)
+})
+
+test('a reconnection and the first OPEN of the socket catch up, as one fetch', async (t) => {
+    const f = fixture({ fakeScroller: true })
+    await mount(t, f)
+    window.dispatchEvent(new Event('twicc:ws-reconnected'))
+    window.dispatchEvent(new Event('twicc:ws-first-open'))
+    await settle()
+    assert.deepEqual(f.calls.map((c) => c.afterId), [null, 3])
+})
+
+test('a resync request triggers nothing timeline-specific', async (t) => {
+    const f = fixture({ fakeScroller: true })
+    await mount(t, f)
+    window.dispatchEvent(new Event('twicc:resync-required'))
+    await settle()
+    assert.equal(f.calls.length, 1)
+})
+
+test('coming back to the Timeline tab catches up once', async (t) => {
+    const f = fixture({ fakeScroller: true })
+    const root = await mount(t, f, '__all__', { active: false })
+    assert.equal(f.calls.length, 1, 'a hidden page loads like a visible one')
+    await root.setActive(true)
+    assert.deepEqual(f.calls.map((c) => c.afterId), [null, 3])
+    await root.setActive(false)
+    assert.equal(f.calls.length, 2, 'leaving the tab fetches nothing')
+})
+
+test('an unmounted page stops listening: its window listeners are removed and nothing is fetched', async (t) => {
+    const before = listenerCounts()
+    const f = fixture({ fakeScroller: true })
+    const root = await mount(t, f)
+    assert.deepEqual(listenerCounts(), Object.fromEntries(TIMELINE_EVENT_TYPES.map((type) => [type, before[type] + 1])))
+    root.unmount()
+    assert.deepEqual(listenerCounts(), before)
+    signal(9)
+    window.dispatchEvent(new Event('twicc:ws-reconnected'))
+    await settle()
+    assert.equal(f.calls.length, 1)
+})
+
+test('scrolled down, rows inserted above put the first visible row back; at the top nothing moves', async (t) => {
+    const f = fixture({ fakeScroller: true })
+    await mount(t, f)
+    f.server.respond = ((respond) => (params) => (params.afterId !== null
+        ? { events: [event(4)], has_more: false, truncated: false, latest_id: 4, sessions: {} }
+        : respond(params)))(f.server.respond)
+    // The day separator is row 0, event-3 row 1: that is the first visible row (`anchorIndex`).
+    f.scroller.log.scrollTop = 400
+    signal(4)
+    await settle()
+    const restores = f.scroller.log.calls.filter(([name]) => name !== 'seed')
+    assert.equal(restores.length, 1)
+    // The row captured BEFORE the render (event-3, not the event-4 that took its place at index 1) ...
+    assert.deepEqual(restores[0][1], { key: 'event-3', index: 1, offset: 7 })
+    // ... and put back AFTER the render (the new row is among the rendered ones).
+    assert.ok(restores[0][2].includes('event-4'), 'the restore waits for the render')
+    f.scroller.log.calls.length = 0
+    f.scroller.log.scrollTop = 0
+    f.server.respond = (params) => (params.afterId !== null
+        ? { events: [event(5)], has_more: false, truncated: false, latest_id: 5, sessions: {} }
+        : { events: [], has_more: false, truncated: false, latest_id: 5, sessions: {} })
+    signal(5)
+    await settle()
+    assert.deepEqual(f.scroller.log.calls.filter(([name]) => name !== 'seed'), [])
+})
+
+test('source pins: the live events and the active prop are wired, useWebSocket dispatches them', () => {
+    const panel = readFileSync(new URL('./TimelinePanel.vue', import.meta.url), 'utf8')
+    assert.match(panel, /bindTimelineEvents\(window, feed\)/)
+    // The order that keeps the view still: capture BEFORE the DOM moves (pre-flush watcher), restore after it.
+    assert.match(panel, /watch\(items, async \(next\) => \{\s*const snapshot = scrollKeeper\.capture\(\)\s*if \(!snapshot\) return\s*await nextTick\(\)\s*scrollKeeper\.restore\(snapshot, [^\n]*\)\s*\}, \{ flush: 'pre' \}\)/)
+    assert.match(panel, /watch\(\(\) => props\.active, \(active\) => \{\s*if \(active\) feed\.refresh\(\)/)
+    const socket = readFileSync(new URL('../../composables/useWebSocket.js', import.meta.url), 'utf8')
+    const changed = socket.slice(socket.indexOf("case 'timeline_changed':"))
+    assert.ok(changed.slice(0, 600).includes("window.dispatchEvent(new CustomEvent('twicc:timeline-changed', { detail: { latestId: msg.latest_id } }))"))
+    const reconnect = socket.slice(socket.indexOf("window.dispatchEvent(new CustomEvent('twicc:ws-reconnected'))"))
+    assert.match(reconnect.slice(0, 500), /\} else \{[^}]*window\.dispatchEvent\(new CustomEvent\('twicc:ws-first-open'\)\)[^}]*\}\s*wasConnected = true/)
+    const detail = readFileSync(new URL('../project/ProjectDetailPanel.vue', import.meta.url), 'utf8')
+    assert.match(detail, /<TimelinePanel :project-id="projectId" :active="isActive && activeTab === 'timeline'" \/>/)
+})
```


The mount pin of `ProjectDetailPanel.vue` now expects the `active` prop:

`frontend/src/utils/projectTabsRouting.test.js` (modify):

```diff
--- a/frontend/src/utils/projectTabsRouting.test.js
+++ b/frontend/src/utils/projectTabsRouting.test.js
@@ -67,9 +67,9 @@ test('the settings panel text follows the fixed map', () => {
 
 test('the scope page mounts the Timeline first, takes its tabs, numbering and routes from projectTabs.js', () => {
     const source = read('../components/project/ProjectDetailPanel.vue')
     assert.ok(source.indexOf('<wa-tab-panel name="timeline">') < source.indexOf('<wa-tab-panel name="stats">'))
-    assert.match(source, /<TimelinePanel :project-id="projectId" \/>/)
+    assert.match(source, /<TimelinePanel :project-id="projectId" :active="isActive && activeTab === 'timeline'" \/>/)
     assert.match(source, /visibleProjectTabs\(\{ hasGitRepo: hasGitRepo\.value \}\)/)
     assert.match(source, /directTab\(index, \{ hasGitRepo: hasGitRepo\.value \}\)/)
     assert.match(source, /tabForRouteName\(route\.name\)/)
     assert.match(source, /routeNameForTab\(tabId, \{ isAllProjectsMode: isAllProjectsMode\.value \}\)/)
```


- [ ] **Step 2: Run the tests and see them fail**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/utils/timeline/liveEvents.test.js src/utils/timeline/scrollKeeper.test.js src/components/timeline/TimelinePanel.test.js src/utils/projectTabsRouting.test.js
```

Expected: `ERR_MODULE_NOT_FOUND` for `liveEvents.js` and `scrollKeeper.js`, and a failing mount pin in `projectTabsRouting.test.js` (it now expects the `active` prop).

- [ ] **Step 3: Write the two modules**

`frontend/src/utils/timeline/liveEvents.js` (new file):

```javascript
// frontend/src/utils/timeline/liveEvents.js
//
// The window events that keep a Timeline page current (display design section 9.2). useWebSocket.js
// dispatches them; the page binds them to its feed. Pure: the event target is handed in.
//
//   twicc:timeline-changed  {latestId}  the server signal: a new event exists.
//   twicc:ws-reconnected                 the socket reopened after an outage (the existing event).
//   twicc:ws-first-open                  the socket reached OPEN for the first time (fires only then).
//
// `resync_required` is not handled here: the existing logic reloads the whole page (utils/resync.js).

export const TIMELINE_CHANGED_EVENT = 'twicc:timeline-changed'
export const WS_RECONNECTED_EVENT = 'twicc:ws-reconnected'
export const WS_FIRST_OPEN_EVENT = 'twicc:ws-first-open'

/**
 * @param {EventTarget} target - `window`.
 * @param {{notifyLatest: (id: number) => void, refresh: () => void}} feed
 * @returns {() => void} Removes the listeners.
 */
export function bindTimelineEvents(target, feed) {
    const onChanged = (event) => {
        const latestId = event.detail?.latestId
        if (Number.isInteger(latestId)) feed.notifyLatest(latestId)
    }
    const onOpen = () => feed.refresh()
    target.addEventListener(TIMELINE_CHANGED_EVENT, onChanged)
    target.addEventListener(WS_RECONNECTED_EVENT, onOpen)
    target.addEventListener(WS_FIRST_OPEN_EVENT, onOpen)
    return () => {
        target.removeEventListener(TIMELINE_CHANGED_EVENT, onChanged)
        target.removeEventListener(WS_RECONNECTED_EVENT, onOpen)
        target.removeEventListener(WS_FIRST_OPEN_EVENT, onOpen)
    }
}
```


`frontend/src/utils/timeline/scrollKeeper.js` (new file):

```javascript
// frontend/src/utils/timeline/scrollKeeper.js
//
// Keeps the view still when rows are inserted ABOVE it (display design section 9.2, "Scroll"):
//   * at the top, new rows simply appear at the top;
//   * scrolled down, the content on screen must not move.
// The simple virtual scroller has no insertion anchoring of its own, so the page captures the first visible
// row before the list changes and puts it back afterwards. Pure: the scroller is handed in.

/** Within this many pixels of the top the user counts as "at the top". */
export const TOP_EPSILON = 2

/**
 * @typedef {Object} ScrollerApi
 * @property {() => {scrollTop: number}} getScrollState
 * @property {() => ({key: *, index: number, offset: number}|null)} getScrollAnchor
 * @property {(anchor: Object) => void} scrollToAnchor
 * @property {() => void} scrollToTop
 */

/**
 * What to do once the new list is rendered.
 *
 * @param {{atTop: boolean, anchor: (Object|null)}|null} snapshot - Taken before the change.
 * @param {Map<*, number>} indexByKey - Position of every row of the new list.
 * @returns {'none'|'anchor'|'top'}
 */
export function planScrollRestore(snapshot, indexByKey) {
    if (!snapshot || snapshot.atTop || !snapshot.anchor) return 'none'
    const index = indexByKey.get(snapshot.anchor.key)
    // The row the user was reading is gone (another scope, a reload): start from the top.
    if (index === undefined) return 'top'
    // Same position: rows were added below (an older page), nothing moved. Do not touch the scroll then, the
    // user may be scrolling at this very moment.
    return index === snapshot.anchor.index ? 'none' : 'anchor'
}

/**
 * @param {() => (ScrollerApi|null|undefined)} getScroller
 */
export function createScrollKeeper(getScroller) {
    return {
        /** Call BEFORE the list changes (a pre-flush watcher). */
        capture() {
            const scroller = getScroller()
            if (!scroller) return null
            return {
                atTop: scroller.getScrollState().scrollTop <= TOP_EPSILON,
                anchor: scroller.getScrollAnchor(),
            }
        },

        /** Call AFTER the new list is rendered. */
        restore(snapshot, indexByKey) {
            const scroller = getScroller()
            if (!scroller) return
            const action = planScrollRestore(snapshot, indexByKey)
            if (action === 'anchor') scroller.scrollToAnchor(snapshot.anchor)
            else if (action === 'top') scroller.scrollToTop()
        },
    }
}
```


- [ ] **Step 4: Wire the panel, the socket and the tab**

The panel binds the events on mount, releases them with the feed on unmount, refreshes when it becomes active, and keeps the view still when rows land above it (`scrollKeeper` captures the first visible row in a pre-flush watcher and puts it back once the DOM is patched; it leaves the scroll alone when only older rows were appended):

`frontend/src/components/timeline/TimelinePanel.vue` (modify):

```diff
--- a/frontend/src/components/timeline/TimelinePanel.vue
+++ b/frontend/src/components/timeline/TimelinePanel.vue
@@ -9,18 +9,20 @@
 //
 // The data side lives in utils/timeline/ (pure, tested): which events the scope asks for, the fetch queue,
 // the grouping into rows and the wording of each row. This component wires them to the stores and the DOM.
 
-import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
+import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
 import { RouterLink } from 'vue-router'
 import { useTimelineFeed } from '../../composables/useTimelineFeed'
 import { ALL_PROJECTS_ID, useDataStore } from '../../stores/data'
 import { useWorkspacesStore } from '../../stores/workspaces'
 import { getProviderHelpers } from '../../providers'
 import { compactHeight } from '../../utils/compactHeight'
+import { bindTimelineEvents } from '../../utils/timeline/liveEvents'
 import { buildTimelineRows, estimateRowHeight, isNearEnd } from '../../utils/timeline/rows'
 import { createLabelsFor } from '../../utils/timeline/providerLabels'
 import { resolveTimelineScope } from '../../utils/timeline/scope'
+import { createScrollKeeper } from '../../utils/timeline/scrollKeeper'
 import ProjectDetailNavList from '../project/ProjectDetailNavList.vue'
 import DaySeparator from '../session/detail/items/DaySeparator.vue'
 import VirtualScroller from '../virtual-scroller/VirtualScroller.vue'
 import TimelineEventRow from './TimelineEventRow.vue'
@@ -30,8 +32,13 @@ const props = defineProps({
     projectId: {
         type: String,
         required: true,
     },
+    /** The Timeline tab is the one on screen (the page is not behind a session, nor on another tab). */
+    active: {
+        type: Boolean,
+        default: true,
+    },
 })
 
 const dataStore = useDataStore()
 const workspacesStore = useWorkspacesStore()
@@ -51,9 +58,23 @@ const scope = computed(() => resolveTimelineScope({
 }))
 
 // The feed compares the SET of ids: a project added, removed or archived reloads the first page.
 watch(() => scope.value.projectIds, (ids) => feed.setScope(ids), { immediate: true })
-onBeforeUnmount(() => feed.dispose())
+
+// ── Staying current (design section 9.2) ───────────────────────────────────────────────────────────────────
+// The page is kept alive per scope: it keeps fetching while hidden and renders nothing. The signal and the
+// socket events reach it through window events dispatched by useWebSocket.js.
+let unbindEvents = null
+onMounted(() => { unbindEvents = bindTimelineEvents(window, feed) })
+onBeforeUnmount(() => {
+    unbindEvents?.()
+    feed.dispose()
+})
+
+// Back on the tab (or the kept-alive page shown again): catch up once; the fetches coalesce in the queue.
+watch(() => props.active, (active) => {
+    if (active) feed.refresh()
+})
 
 // ── What the page shows ───────────────────────────────────────────────────────────────────────────────────
 
 const context = computed(() => ({
@@ -83,11 +104,20 @@ const view = computed(() => {
 })
 
 const itemKey = (item) => item.key
 
-// ── Scroller: estimates, next page ────────────────────────────────────────────────────────────────────────
-
+// ── Scroll: rows inserted above the view must not move it ─────────────────────────────────────────────────
 const scrollerRef = ref(null)
+const scrollKeeper = createScrollKeeper(() => scrollerRef.value)
+
+watch(items, async (next) => {
+    const snapshot = scrollKeeper.capture()
+    if (!snapshot) return
+    await nextTick()
+    scrollKeeper.restore(snapshot, new Map(next.map((row, index) => [row.key, index])))
+}, { flush: 'pre' })
+
+// ── Scroller: estimates, next page ────────────────────────────────────────────────────────────────────────
 
 // A first height estimate per row type (day separator, event line, navigation copy) for the rows the scroller
 // has not measured yet. It runs AFTER the render: the scroller only exists once the first page flipped the view
 // to the list, and it is created again each time the list comes back. A row the scroller already measured keeps
```


`useWebSocket.js` turns the frame into a window event and announces the first OPEN, which `twicc:ws-reconnected` never does:

`frontend/src/composables/useWebSocket.js` (modify):

```diff
--- a/frontend/src/composables/useWebSocket.js
+++ b/frontend/src/composables/useWebSocket.js
@@ -1329,8 +1329,13 @@ export function useWebSocket() {
                 break
             case 'mcp_updated':
                 window.dispatchEvent(new CustomEvent('twicc:mcp-updated'))
                 break
+            case 'timeline_changed':
+                // A new timeline event exists (no session, no project, no content): the Timeline pages fetch
+                // what is new themselves (utils/timeline/liveEvents.js).
+                window.dispatchEvent(new CustomEvent('twicc:timeline-changed', { detail: { latestId: msg.latest_id } }))
+                break
             case 'upload_state': {
                 // One upload's server record (spec §6.3). Lazy import: the
                 // uploads store never imports useWebSocket.
                 import('../stores/uploads').then(({ useUploadsStore }) => {
@@ -2012,8 +2017,12 @@ export function useWebSocket() {
             // are gone. Signal open panes to refresh their content. Not on the
             // first connect — panes load on mount, and there was no outage.
             if (isReconnection) {
                 window.dispatchEvent(new CustomEvent('twicc:ws-reconnected'))
+            } else {
+                // `twicc:ws-reconnected` never fires on the first connect: a Timeline page that loaded before
+                // the socket opened asks for what it missed.
+                window.dispatchEvent(new CustomEvent('twicc:ws-first-open'))
             }
             wasConnected = true
         } else if (newStatus === 'CLOSED') {
             // Clear send function when disconnected
```


The tab tells the panel when it is the one on screen:

`frontend/src/components/project/ProjectDetailPanel.vue` (modify):

```diff
--- a/frontend/src/components/project/ProjectDetailPanel.vue
+++ b/frontend/src/components/project/ProjectDetailPanel.vue
@@ -457,9 +457,9 @@ onBeforeUnmount(() => {
                 <UploadTabStatus v-if="tab.id === 'files'" :status="filesUploadStatus" />
             </wa-tab>
 
             <wa-tab-panel name="timeline">
-                <TimelinePanel :project-id="projectId" />
+                <TimelinePanel :project-id="projectId" :active="isActive && activeTab === 'timeline'" />
             </wa-tab-panel>
 
             <wa-tab-panel name="stats">
                 <ProjectDetailNavList :project-id="projectId" class="stats-nav-list" />
```


- [ ] **Step 5: Run the tests and see them pass**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && node --test src/utils/timeline/*.test.js src/components/timeline/*.test.js src/utils/projectTabsRouting.test.js
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && npm test
```

Expected: all pass (same environment note as Task 9).

- [ ] **Step 6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add frontend/src/utils/timeline/liveEvents.js frontend/src/utils/timeline/liveEvents.test.js frontend/src/utils/timeline/scrollKeeper.js frontend/src/utils/timeline/scrollKeeper.test.js frontend/src/components/timeline/TimelinePanel.vue frontend/src/components/timeline/TimelinePanel.test.js frontend/src/composables/useWebSocket.js frontend/src/components/project/ProjectDetailPanel.vue frontend/src/utils/projectTabsRouting.test.js
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
feat(timeline): update the Timeline in real time

useWebSocket turns the timeline_changed frame into a twicc:timeline-changed window event
and announces the first OPEN of the socket (twicc:ws-first-open), which
twicc:ws-reconnected never does. Each Timeline page binds those events, plus the existing
reconnect event, to its feed on mount and releases them on unmount; the tab passes an
active prop and a page coming back to the screen catches up once, through the same
coalescing queue. A page hidden behind another tab keeps fetching and renders nothing.
resync_required is left to the existing page reload.

Rows inserted above a scrolled-down view no longer move it: the first visible row is
captured before the list changes and put back afterwards, while older rows appended below
and a view at the top are left alone.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

---

## Task 11: Documentation and full verification

**Files:**
- Modify: `CLAUDE.md`, `AGENTS.md` (the `TimelineEvent` bullet of each, kept in sync), `frontend/public/tips/project-page.md` (the project page tip names the new tab)

**Interfaces:**
- Consumes: everything above. Produces: nothing used by code.

- [ ] **Step 1: Update the two guidance files**

Both files describe `TimelineEvent`. Append the new facts to each bullet, concisely (CLAUDE.md is the longer one, AGENTS.md keeps its shorter form): the `provider` column and its resolver, the feed endpoint with its keyset and `after_id`, the signal, the Timeline tab as the bare route and Stats at `/stats`, the pure modules, and the display design in the `Designs:` list. Do not edit any historical design or plan document.

`CLAUDE.md` (modify):

```diff
--- a/CLAUDE.md
+++ b/CLAUDE.md
@@ -127,3 +127,3 @@ Key models in `src/twicc/core/models.py` (read the source for full field lists;
 - **`PeerMessage`** — one cross-instance message. The local session link is `origin_session` for an outbound row and `delivered_to_session` for an inbound row. `reply_to_message` resolves the answered row once within the peer relationship. `(peer_id, thread_id)` is the local thread key; `thread_id` never crosses the wire. Design: `docs/plans/2026-08-11-peer-threading-design.md`.
-- **`TimelineEvent`** — append-only history of what happened to a session (and to the backend instance: `session_id`/`project` NULL). `session_id` is a plain CharField (like `ProcessRun`), `project` a raw FK. Kinds are a closed `subject.verb` enum (`<subject>.started`/`.ended` intervals, past-participle points); payloads are validated per kind (`core/timeline.py`). Written **only** through `core/services/session_timeline.record_event[_sync]` — never inserted directly, never raising, always under the DB write lock, with an explicit hook at every writer of the fact (never Django signals: the code base uses `queryset.update()`). `turn.ended` carries the background-work snapshot (non-null = waiting for the user while work still runs) and is re-emitted when it changes while idle; an `ended` row repeats the data of its open `started` row (`STARTED_OF`, applied by the recording service). Boot reconciliation (`timeline_boot.py`) writes `process.ended(reason="lost")` for processes a crash left behind; the backfill (`timeline_backfill.py`) lists the sessions without a `session.created` row once (newest `mtime` first) and handles them one per lock acquisition, writing only `session.created` and flag states, never process rows. Every request-caused row also records its **origin**: `actor` (`user`/`agent`/`external`), `via` (`ui`/`cli`/`rpc`/`mcp_internal`/`mcp_external`), `actor_ref` (session id, API token id or MCP connection id) and `actor_name` (connection-name snapshot). The origin lives in the `request_origin` ContextVar (`twicc/request_origin.py`), set by every entry point (WS frame, REST middleware, RPC view, MCP call and batch child, drop-file watcher; the CLI writes it under `_origin` in the drop payload from `transport.submit`) and read by the services; a writer passes an explicit `actor=` only when it is NOT request-caused (then the origin fields are NULL). Deferred writers never read the carrier: pending title and pending creation attributes carry the origin captured with them, the process DEAD hook reads the origin paired with `kill_reason` (`StopOriginMixin`), and automatic steps (state-change funnel, cron restart, boot adoption, the restart of an `apply-settings` kill) run under the `system` marker. NULL `via` means "unknown" (old rows) or "automatic". Designs: `docs/plans/2026-10-13-session-timeline-design.md`, `docs/plans/2026-10-14-timeline-actor-origin-design.md`.
+- **`TimelineEvent`** — append-only history of what happened to a session (and to the backend instance: `session_id`/`project` NULL). `session_id` is a plain CharField (like `ProcessRun`), `project` a raw FK. Kinds are a closed `subject.verb` enum (`<subject>.started`/`.ended` intervals, past-participle points); payloads are validated per kind (`core/timeline.py`). Written **only** through `core/services/session_timeline.record_event[_sync]` — never inserted directly, never raising, always under the DB write lock, with an explicit hook at every writer of the fact (never Django signals: the code base uses `queryset.update()`). `turn.ended` carries the background-work snapshot (non-null = waiting for the user while work still runs) and is re-emitted when it changes while idle; an `ended` row repeats the data of its open `started` row (`STARTED_OF`, applied by the recording service). Boot reconciliation (`timeline_boot.py`) writes `process.ended(reason="lost")` for processes a crash left behind; the backfill (`timeline_backfill.py`) lists the sessions without a `session.created` row once (newest `mtime` first) and handles them one per lock acquisition, writing only `session.created` and flag states, never process rows. Every request-caused row also records its **origin**: `actor` (`user`/`agent`/`external`), `via` (`ui`/`cli`/`rpc`/`mcp_internal`/`mcp_external`), `actor_ref` (session id, API token id or MCP connection id) and `actor_name` (connection-name snapshot). The origin lives in the `request_origin` ContextVar (`twicc/request_origin.py`), set by every entry point (WS frame, REST middleware, RPC view, MCP call and batch child, drop-file watcher; the CLI writes it under `_origin` in the drop payload from `transport.submit`) and read by the services; a writer passes an explicit `actor=` only when it is NOT request-caused (then the origin fields are NULL). Deferred writers never read the carrier: pending title and pending creation attributes carry the origin captured with them, the process DEAD hook reads the origin paired with `kill_reason` (`StopOriginMixin`), and automatic steps (state-change funnel, cron restart, boot adoption, the restart of an `apply-settings` kill) run under the `system` marker. NULL `via` means "unknown" (old rows) or "automatic". The `provider` column (nullable; indexes `(occurred_at, id)` and `(provider, occurred_at, id)`) is filled by every writer through `resolve_provider` (explicit value, then the payload, then a bounded cache, then one primary-key query). Display: `GET /api/timeline/` (`core/services/timeline_feed.py`, owner-only, read-only) returns events newest first by a row-value keyset `before=<occurred_at>,<id>` (never the expanded `OR` form), `after_id` for the live fetch (capped at 500, `truncated`), a `latest_id` read first and a side table of the sessions the events cite. A trailing-throttled `timeline_changed` WebSocket message carrying `latest_id` (`core/services/timeline_signal.py`, requested after commit by `record_event_sync` and the bulk path, one per backfill run) tells the **Timeline** tab to fetch `after_id`. Timeline is the first tab and the bare route of every scope page (global, workspace, project, worktree); Stats moved to `/stats`. Its logic is pure modules under `frontend/src/utils/timeline/` (feed queue, rows, wording), the components under `frontend/src/components/timeline/`. Designs: `docs/plans/2026-10-13-session-timeline-design.md`, `docs/plans/2026-10-14-timeline-actor-origin-design.md`, `docs/plans/2026-10-15-timeline-display-design.md`.
 - **`ArtifactBookmark`** — user bookmark of one rendered artifact (`session` FK, denormalised raw `project` FK, `relative_path`, `name`, `scope` reusing `PinMode`; unique `(session, relative_path)`). Scope mirrors session pinning (project/workspace/all, worktree-aware at resolution); "not bookmarked" = no row. Browsed via the sidebar's Artifacts mode (`/…/artifacts/:bookmarkId?`). `allowed_hosts` (JSON dict) is the network-broker allowlist — see **Artifact Network Broker** below. `denied_hosts` (same shape) = explicit owner deny decisions, auto-refused without prompt in the owner preview (broker reads both dicts live). **`ArtifactNetworkDenial`** — refused-fetch provenance rows (share/ip/user_agent + counter, NULL share = owner preview), coalesced+flushed by `artifacts/denial_tracking.py`, purged on allow; powers the bookmark dialog's Network access section (design `2026-07-10-artifact-network-access-design.md`).
```


`AGENTS.md` (modify):

```diff
--- a/AGENTS.md
+++ b/AGENTS.md
@@ -128,3 +128,3 @@ Key models in `src/twicc/core/models.py` (read the source for full field lists;
 - **`PeerMessage`** — one cross-instance message. Outbound rows link `origin_session`; inbound rows link `delivered_to_session`. `reply_to_message` resolves the parent within its peer. `(peer_id, thread_id)` is the local thread key. Design: `docs/plans/2026-08-11-peer-threading-design.md`.
-- **`TimelineEvent`** — append-only history of a session (and of the backend: `session_id`/`project` NULL). `session_id` is a plain CharField (like `ProcessRun`), `project` a raw FK. Kinds are a closed `subject.verb` enum; payloads validated per kind (`core/timeline.py`). Written **only** via `core/services/session_timeline.record_event[_sync]` — never inserted directly, never raising, under the DB write lock, with an explicit hook at every writer of the fact (never Django signals: `queryset.update()` is used). `turn.ended` carries the background-work snapshot and is re-emitted when it changes while idle; an `ended` row repeats its open `started` row's data (`STARTED_OF`). Boot reconciliation (`timeline_boot.py`) writes `process.ended(reason="lost")` after a crash; the backfill (`timeline_backfill.py`) lists the sessions without a `session.created` row once (newest `mtime` first) and handles them one per lock acquisition, writing only `session.created` and flag states. Every request-caused row also records `via`, `actor_ref` and `actor_name` next to `actor`: the origin lives in the `request_origin` ContextVar (`twicc/request_origin.py`), set by every entry point (WS frame, REST middleware, RPC, MCP, drop-file watcher; the CLI writes it under `_origin` in the drop payload) and read by the services; pass an explicit `actor=` only for a non-request writer (origin fields NULL). Deferred writers (pending title, pending creation attributes, process DEAD hook) use an origin captured earlier, never the carrier; automatic steps run under the `system` marker. NULL `via` = unknown (old rows) or automatic. Designs: `docs/plans/2026-10-13-session-timeline-design.md`, `docs/plans/2026-10-14-timeline-actor-origin-design.md`.
+- **`TimelineEvent`** — append-only history of a session (and of the backend: `session_id`/`project` NULL). `session_id` is a plain CharField (like `ProcessRun`), `project` a raw FK. Kinds are a closed `subject.verb` enum; payloads validated per kind (`core/timeline.py`). Written **only** via `core/services/session_timeline.record_event[_sync]` — never inserted directly, never raising, under the DB write lock, with an explicit hook at every writer of the fact (never Django signals: `queryset.update()` is used). `turn.ended` carries the background-work snapshot and is re-emitted when it changes while idle; an `ended` row repeats its open `started` row's data (`STARTED_OF`). Boot reconciliation (`timeline_boot.py`) writes `process.ended(reason="lost")` after a crash; the backfill (`timeline_backfill.py`) lists the sessions without a `session.created` row once (newest `mtime` first) and handles them one per lock acquisition, writing only `session.created` and flag states. Every request-caused row also records `via`, `actor_ref` and `actor_name` next to `actor`: the origin lives in the `request_origin` ContextVar (`twicc/request_origin.py`), set by every entry point (WS frame, REST middleware, RPC, MCP, drop-file watcher; the CLI writes it under `_origin` in the drop payload) and read by the services; pass an explicit `actor=` only for a non-request writer (origin fields NULL). Deferred writers (pending title, pending creation attributes, process DEAD hook) use an origin captured earlier, never the carrier; automatic steps run under the `system` marker. NULL `via` = unknown (old rows) or automatic. The nullable `provider` column is filled by every writer through `resolve_provider` (explicit value, payload, bounded cache, one primary-key query). `GET /api/timeline/` (`core/services/timeline_feed.py`, read-only) feeds the **Timeline** tab (first tab and bare route of every scope page; Stats moved to `/stats`): row-value keyset `before`, `after_id` live fetch, side table of cited sessions. A throttled `timeline_changed` WebSocket message (`core/services/timeline_signal.py`, after commit) tells clients to fetch `after_id`. Frontend logic: pure modules in `frontend/src/utils/timeline/`. Designs: `docs/plans/2026-10-13-session-timeline-design.md`, `docs/plans/2026-10-14-timeline-actor-origin-design.md`, `docs/plans/2026-10-15-timeline-display-design.md`.
 - **`ArtifactBookmark`** — user bookmark of one rendered artifact (`session` FK, denormalised raw `project` FK, `relative_path`, `name`, `scope` reusing `PinMode`; unique `(session, relative_path)`). Scope mirrors session pinning (project/workspace/all, worktree-aware at resolution); "not bookmarked" = no row. Browsed via the sidebar's Artifacts mode (`/…/artifacts/:bookmarkId?`). `allowed_hosts` (JSON dict) is the network-broker allowlist — see **Artifact Network Broker** below. `denied_hosts` (same shape) = explicit owner deny decisions, auto-refused without prompt in the owner preview (broker reads both dicts live). **`ArtifactNetworkDenial`** — refused-fetch provenance rows (share/ip/user_agent + counter, NULL share = owner preview), coalesced+flushed by `artifacts/denial_tracking.py`, purged on allow; powers the bookmark dialog's Network access section (design `2026-07-10-artifact-network-access-design.md`).
```


The tip that describes a project's own page still names a Stats tab: it gains the Timeline.

`frontend/public/tips/project-page.md` (modify):

```diff
--- a/frontend/public/tips/project-page.md
+++ b/frontend/public/tips/project-page.md
@@ -4,3 +4,4 @@ title: "Open a project's own page"
 
-Every project has its own page — a **Stats** tab full of activity insights
+Every project has its own page — a **Timeline** tab that lists what happened (sessions
+created, turns, approvals, renames...) as it happens, a **Stats** tab full of activity insights
 (and cost, when the **Show costs** setting is on), plus project-level **Files**, **Git**
```


- [ ] **Step 2: Check what does not change**

The new endpoint is a plain REST view. Only a CLI command gets an RPC route, a skill and an MCP tool automatically, so nothing to add in `SKILLS-AND-CLI.md`, `RPC-API.md` or the plugin, and no `plugin.json` bump:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && grep -rn -i "timeline" SKILLS-AND-CLI.md RPC-API.md src/twicc/agent/plugin; echo "exit $?"
```

Expected: no output, `exit 1`. The old bare project URLs emitted by the skills (`twicc-projects/SKILL.md`), the external notifications (`external_notifications.py`) and `claude_code/helpers.py` need no change: the SPA catch-all serves any path, and they now open the Timeline (spec section 10). Check the three backend suites that read those URLs:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest tests/test_external_notification_urls.py tests/test_spa_index_cache.py tests/test_browser_urls.py -q
```

Expected: all pass.

- [ ] **Step 3: Run everything**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && TWICC_DATA_DIR=$PWD uv run pytest -q
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline/frontend && npm test
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && uvx ruff check $(git diff --name-only HEAD~10 HEAD -- '*.py' | grep -v '/migrations/')
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git status --short
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git log --oneline -12
```

Expected: both suites green (two backend subprocess tests, `tests/test_migration_process.py`, are known to time out under full-suite load and pass alone: rerun them alone if they fail); ruff clean; `git status` shows only the untracked `docs/plans/2026-10-14-session-timeline-handoff.md` (never committed), this plan file and the design document if they are still untracked; the log shows the ten commits of tasks 1 to 10 (this task's docs commit comes in Step 4, which is why the ruff command above uses `HEAD~10`).

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git add CLAUDE.md AGENTS.md frontend/public/tips/project-page.md
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && git commit -F - <<'EOF'
docs(timeline): document the display feed, the signal and the Timeline tab

Add the provider column and its central resolution, the GET /api/timeline/ feed (row-value
keyset, after_id live fetch, side table), the throttled timeline_changed signal and the
Timeline tab (bare route of every scope page, Stats at /stats) to the TimelineEvent
entry of CLAUDE.md and AGENTS.md, and link the display design.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
```

- [ ] **Step 5: Hand over to the user (do none of this yourself)**

Report these to the user, in this order:

1. **Migrations.** `migrate` on his own instance applies **four** new migrations: `0156_timeline_event`, `0157_timeline_event_session_kind_index`, `0158_timeline_event_origin` and `0159_timeline_event_provider` (`devctl.py start` applies pending migrations at startup).
2. **Restart** the backend and the frontend through `devctl.py` (the signal starts with the server; the new route and WebSocket frame need the new bundle).
3. **First boot.** The backfill writes thousands of derived `session.created` rows dated at the sessions' creation: the global Timeline is flooded until filters exist (known limit, spec section 12).
4. **Browser check (Firefox, the user's browser).** What the node tests cannot show:
   - `/projects`, `/projects?workspace=<id>`, `/project/<id>` open on the Timeline; `/projects/stats` and `/project/<id>/stats` show Stats; Alt+Shift+1..5 and the two palette commands switch tabs; key 4 does nothing in a project without a git root.
   - Normal height: the page header and the band stay still, only the list scrolls. Window under 60 rem high (compact mode): the navigation list is the first row and scrolls away, the band stays on top.
   - Scroll to the end: older events load by themselves, the view does not jump to the bottom (the scroller's bottom pin is off), a spinner shows while loading.
   - With the view scrolled down, trigger an event (send a message in a session): the visible rows do not move; at the top the new row appears at the top within about a third of a second.
   - Switch to Stats and back, stop and restart the backend: the list catches up. A session title is a link that keeps the current frame; a hidden session is plain text with an eye marker; estimated times show `~` and a lighter style; the band looks right with "reduce transparency" and "reduce effects".
   - `/projects/stats?workspace=<id>` shows Stats of that workspace and the rail keeps the workspace highlighted on Stats (P16).
   - Stop the backend and reload the page: the first-page error shows its message AND the Retry button (a button put in a slot that `wa-callout` does not have would not render). Start the backend, click Retry: the list loads. Do the same for the next-page error: scroll to the end with the backend down, check the footer error and its Retry.
   - Open `/project/<id>/stats` directly, then click the Timeline tab: the list renders, measures and scrolls (its scroller is created inside a `display: none` panel).
   - Scrolled down, trigger an event: the visible rows still do not shift once the real row heights replace the 34 px estimate.
   - Leave the Timeline for another tab and come back (the tab panel is `display: none` meanwhile): the scroll position survives, in Firefox.
5. **CHANGELOG.** No entry was written (project rule). Proposal for him to approve, under `## [Unreleased]` / `### Added`:
   `- **Timeline** — A new first tab on every workspace, project and worktree page lists what happened to your sessions, newest first, and updates live: sessions created, turns, approvals, renames, setting changes, archive and pin changes, and TwiCC starts and stops. Stats moves to its own tab.`
6. **Documents.** The design (`2026-10-15-timeline-display-design.md`), this plan and the handoff are untracked on purpose: he commits them on his explicit request.
7. **Rows with a NULL provider.** The rows written before this migration, if any, and any row no source could name keep a NULL `provider` (the spec has no data migration). The provider filter of the API (no UI control yet) will not match them. A one-off SQL fill is optional and his call; it is NOT part of the migration.
8. **Query plans.** The `EXPLAIN QUERY PLAN` assertions of Task 2 depend on the SQLite version (3.53.1 here) and on the absence of `ANALYZE` (as in production); a different version or an `ANALYZE` may choose other indexes and fail them without any regression. The timing tests of Task 3 have fixed margins (a 0.1 s window, `2 <= n <= 8` for the sustained stream): on a flaky CI, repeating the run is the first thing to do.
9. **Known runtime characteristics** (accepted for this version, not defects):
   - `ProjectDetailPanel` is kept alive per scope without a maximum, so each scope page visited keeps one live Timeline page for the whole session of the app.
   - Each live page can run up to about three `after_id` fetches per second while events stream in (the 0.3 s throttle), plus one per page on every reconnect and each return to its tab. Each is a primary-key range read of a few rows.
   - A list is never trimmed: it only grows with scrolling and live rows.
   - A page opened directly on `/stats` still fetches the Timeline's first page (the Timeline tab panel is mounted whatever tab is active).

---

## Self-Review

### How this plan was checked

The code blocks are not typed from memory: each task was applied, in order, to a scratch copy of the worktree (`src/`, `tests/`, `frontend/src/`, same Python environment), one commit per task, and the tests of the task were run at its own commit (and the red step on its parent). Results: backend T1 to T3 green with the whole timeline family; the full backend suite green except tests that need files the copy lacked (a spec document under `docs/plans/`); frontend T4 to T10 green, and the whole suite green except a few tests under `tests/browser/*` (and one for a missing file) that need the git history or files the copy lacked (they pass in the worktree). The `diff` blocks above are the exact `git diff -U4` of those task commits.

### Spec coverage (sections)

| Spec section | Task | Concrete test |
|---|---|---|
| 1 Goal and scope | all | the tasks below |
| 2 Vocabulary | T5 (`group`, `head`, `separator`), T2 (`side table`), T3 (`signal`) | rows and API tests |
| 3.1 Tab and routes | T9 | `projectTabs.test.js`, `projectTabsRouting.test.js` |
| 3.2 Header (unchanged, compact copy) | T8, T9 | `compact-height mode: the navigation copy is the first row of the list`; `ProjectDetailHeader.vue` is not touched |
| 3.3 Band | T8 | `the band carries the label "Timeline" in the glass-sticky style and nothing else`, panel-veil 5c |
| 3.4 Scroll, loading, states | T8, T5, T10 | panel tests, `isNearEnd`, source pins; browser checklist (T11) |
| 4 Scope rules | T5, T8 | `scope.test.js`, `a workspace sends every project id...`, `an unknown project shows "Project not found"...` |
| 5.1 Row anatomy | T7 | `TimelineEventRow.test.js` |
| 5.2 Grouping, separators | T5 | `rows.test.js` |
| 5.3 Identity and project name | T4, T7 | `sessions.test.js`, row tests |
| 5.4 Hidden and archived | T4, T7 | `a hidden and archived session is plain text with both markers...` |
| 5.5 Click behaviour | T7 | links only on session title, project name, label sessions (row tests) |
| 6.1 The 19 kinds | T4 | `the 19 kinds: wording, icon and severity` |
| 6.1.1 `process.ended` reasons | T4 | `process.ended: every reason has its wording and severity` |
| 6.1.2 `awaiting_input` wording | T4 | `awaiting_input: request_type wins over tool_name...` |
| 6.1.3 Fallbacks | T4 | see the D36 row below |
| 6.2 `settings.changed` diff | T4 | `settingsDiff.test.js` |
| 6.3 Author label | T4 | `authorLabel.test.js` |
| 6.4 Estimated, tooltips, content rules | T4, T7 | `time: ...`, `tooltip: ...`, `strings from data are text, never markup` |
| 6.5 Background-work badge | T4, T7 | `backgroundBadge.test.js`, row badge test |
| 7.1 Request | T2 | `test_bad_parameters_answer_400...`, `test_parse_request_defaults_and_normalisation` |
| 7.2 Response | T2 | `test_the_event_shape_is_exactly_the_public_one`, `latest_id` tests |
| 7.3 Side table | T2 | six tests: `test_the_side_table_holds_every_cited_session_by_event_by_actor_ref_and_by_spawned_by`, `test_a_user_actor_ref_is_not_looked_up_as_a_session`, `test_the_side_table_applies_the_pending_title_override_and_flags`, `test_an_untitled_session_has_a_null_title_in_the_side_table`, `test_a_session_id_with_no_row_has_no_entry`, `test_the_side_table_lookup_is_chunked` |
| 7.4 Ordering, keyset, live fetch | T2 | instance-event, paging and after_id tests, plan tests |
| 7.5 Errors | T2 | `test_a_database_error_answers_500`; the view never writes: `test_the_view_never_writes_the_table_and_refuses_other_methods` (the table is unchanged after the view ran, 405 on every other method) and `test_the_feed_service_issues_no_write_statement` (the statements of the service, read in the test thread) |
| 8 Data model | T1 | `test_timeline_provider.py` |
| 9.1 Server signal | T3 | `test_timeline_signal.py` |
| 9.2 Client rules | T6, T10 | `feed.test.js`, `liveEvents.test.js`, panel live tests |
| 9.3 Tests' implications | T3 | on-commit helper not needed: the tests use real transactions (`transaction=True`) and spy on `poke`; burst, sustained, backfill, dedupe tests exist |
| 10 Interaction with existing behaviour | T9, T8 | routing tests, the nine existing suites rerun in T9 Step 4 |
| 11 Decisions | below | below |
| 12 Known limits | T11 | listed in the hand-over |
| 13 Out of scope | none | nothing implemented: no filter UI, no share exposure, no CLI/MCP read, no i18n, no banner |

### Decisions D1 to D42

| D | Task | Test or proof |
|---|---|---|
| D1 tab, routes, shortcuts, palette, scope memory, settings | T9 | `projectTabs*.test.js` (all) |
| D2 header and band, scroll per mode | T8, T9 | band test, compact test; header untouched |
| D3 one line, time right, head identity, separators, indentation | T5, T7 | `rows.test.js`, row tests |
| D4 project name rules, worktree rows | T4, T7 | `sessions.test.js` (`the project name shows...`, `on a project page only the rows of its worktrees carry the name`), row project test |
| D5 everything shown | T2, T4, T7 | 19 kinds, instance events in the four cases, markers |
| D6 simple scroller, ~50 per page, estimate, auto load | T2, T5, T8 | default limit, `estimateRowHeight`, `isNearEnd`, panel near-end test |
| D7 signal then `after_id`, sorted insertion, no jump | T3, T6, T10 | signal tests, `fetched rows are inserted by (occurred_at, id)...`, scroll tests |
| D8 provider column, central resolution, indexes, API parameter, icon from side table | T1, T2, T4, T7 | `test_timeline_provider.py`, `test_the_provider_filter...`, head provider fallback test |
| D9 icons and English strings | T4 | the 19-kind table |
| D10 endpoint contract | T2 | `test_timeline_api.py` |
| D11 row-value keyset, no `OR project_id IS NULL`, second query for instance events | T2 | plan tests (`" OR " not in sql`), instance tests |
| D12 missing session has no entry, UI falls back | T2, T4, T7 | `test_a_session_id_with_no_row_has_no_entry`, `a session with no entry shows its id as plain text` |
| D13 owner-only | T2 | `test_the_endpoint_is_behind_the_instance_authentication` |
| D14 backfill silent, one signal | T3 | `test_the_backfill_is_silent_per_session_and_signals_once...` |
| D15 / D31 instance events with a provider; no second query on global without provider | T2 | `test_the_instance_events_query_runs_in_every_case_but_the_global_one_without_provider`, 4-case test |
| D16 estimated "~", tooltips | T4, T7 | `time: ...`, `tooltip: ...`, estimated row test |
| D17 `turn.ended` not collapsed, badge | T4, T5 | `backgroundBadge.test.js`, `re-emissions of turn.ended are not collapsed` |
| D18 workspace uses the archived-blind list | T8 | source pin `getAllProjectIds`, no `getVisibleProjectIds` |
| D19 / D22 `latest_id`, live cursor rules | T2, T6 | `test_latest_id_is_read_before_the_events`, `a before page never advances the live cursor`, `the live cursor is the latest_id of the response...` |
| D20 author label from actor and via | T4 | `the table of section 6.3, row by row` |
| D21 no request for an empty or unresolved scope | T5, T8 | `scope.test.js`, panel loading/empty/not-found tests |
| D23 `before` cursor is the last fetched row; old live row not inserted | T6 | `the pagination cursor stays the last fetched row...`, `a live row older than the loaded tail...` |
| D24 instance query (refined by D31) | T2 | as D31 |
| D25 UTC microseconds with `Z` | T2 | round-trip tests, `TIMESTAMP` regex |
| D26 day separator labels rows below, starts the list | T5 | `the separator labels the rows BELOW it...`, `the list starts with a separator...` |
| D27 scroll per mode | T8 | compact-row test; browser checklist |
| D28 hidden plain text + marker, archived keeps link, no `type` | T2, T4, T7 | side-table entry shape test, marker tests |
| D29 `request_type` wins; parent once | T4 | `awaiting_input: ...`, `session.created: the parent appears once...` |
| D30 `resync_required` left alone; derived tooltip | T4, T10 | `resync_required is not a timeline event...`, `a resync request triggers nothing timeline-specific`, `time:` test |
| D32 one queue | T6 | `the queue race...`, `signals during a live fetch coalesce...` |
| D33 null `latest_id`, null cursor = 0, deferred first-page signals | T2, T6 | `test_an_empty_feed_has_a_null_latest_id...`, `a null live cursor on an empty feed counts as 0...`, `a signal during the first page is deferred...` |
| D34 set change reloads, old response ignored | T6, T8 | `a project-id set change reloads...`, `a response for a set that is no longer current is ignored...`, panel set-change test |
| D35 shortcut map | T9 | `the fixed shortcut map...`, `without a git root key 4 does nothing...` |
| D36 every field has a fallback | T4 | session.created origin (`session.created: origin wording`), process.started cause, error null, settings unknown reason / empty / unknown field (`settingsDiff.test.js`), title new null / unknown source, turn.ended unknown shape, backend.ended clean null, data not an object, old `''`, unknown precision (`time:` and author tests) |
| D37 no author label when not exact | T4 | `no label on a row whose precision is not exact, for every actor` |
| D38 link rules everywhere; later response overwrites | T4, T6 | `the session of an agent label follows the link rules...`, `a later response overwrites the side-table entry of a session` |
| D39 500 cap on the merged set; `limit` integer 1..100 | T2 | `test_the_cap_applies_to_the_merged_set...`, 400 table |
| D40 first OPEN and tab return | T10 | `a reconnection and the first OPEN of the socket catch up, as one fetch`, `coming back to the Timeline tab catches up once` |
| D41 `after_id` ascending; client ignores `has_more` | T2, T6 | `test_after_id_returns_only_newer_ids_in_ascending_order...`, `the has_more of an after_id response is ignored...` |
| D42 neutral robot, unknown precision exact, empty `old`, unknown project | T4, T8 | 19-kind table, `time:` unknown precision, `Titled` with `old: ''`, not-found test |

### Section 14 verification items

**Backend**

| Item | Task | Test |
|---|---|---|
| Newest first, id tie-break, paging over equal timestamps | T2 | `test_events_come_newest_first_and_the_id_breaks_ties`, `test_paging_by_before_returns_every_row_exactly_once_with_many_equal_timestamps` |
| Global includes NULL project; scope excludes; worktrees | T2 | `test_global_includes_null_project_rows...`, `test_a_project_with_worktrees_returns_both...` |
| Instance events in the four cases, position, boundary, once | T2 | `test_an_instance_event_appears_exactly_once_in_each_of_the_four_cases`, `..._merged_at_its_time_position_across_a_page_boundary`, `test_a_row_that_both_queries_return_is_listed_once` |
| Provider filter, 400s, cap | T2 | `test_the_provider_filter_keeps_only...`, `test_bad_parameters_answer_400_with_an_error_body` (24 cases), `test_a_limit_over_100_is_capped` |
| `after_id` ascending, instance events, truncation on the merged set, empty feed, 400s, `latest_id` | T2 | `test_after_id_*` (3), `test_the_cap_applies_to_the_merged_set...`, `test_an_empty_feed_has_a_null_latest_id...`, `test_latest_id_is_the_highest_id_whatever_the_scope` |
| Side table by event, `actor_ref`, `spawned_by`; pending title; missing id | T2 | the six side-table tests listed under section 7.3 |
| `EXPLAIN QUERY PLAN`: index, row-value form, no multi-index OR | T2 | `test_the_page_query_uses_an_index_with_the_row_value_keyset_and_no_or` (4 params), `test_paging_over_equal_timestamps_in_a_multi_project_scope`, `test_the_instance_query_uses_the_session_index`, `test_the_after_id_query_scans_a_primary_key_range_never_a_scope_index` (4 params, read from the real `_after_queryset`) |
| Column filled by every writer; guard; NULL without error; resolution order | T1 | the writer tests (`test_the_single_path_*`, `test_the_bulk_path_*`, `test_the_batch_fallback_*`, `test_the_bulk_archive_*`, `test_the_boot_reconciliation_*`, `test_the_process_hooks_*`, `test_session_created_*`, `test_the_title_recorder_*`, `test_the_settings_recorder_*`, `test_the_flag_hooks_*`, `test_the_backfill_writes_the_session_provider`), `test_guard_no_row_with_a_session_keeps_a_null_provider_after_any_writer_ran`, `test_the_guard_itself_detects_a_null_provider`, and the twelve resolver tests (order, cache, enum, non-string hint, instance event, no row, bound, failure) |
| Migration: column and two indexes only; existing rows unchanged | T1 | `test_migration_0159_adds_the_column_and_the_two_indexes_only`, `test_the_two_display_indexes_exist...`, `test_the_models_and_the_migrations_agree`, `test_a_row_written_before_the_column_reads_as_null` |
| Signal: one per burst, after commit, nothing on dedupe or failed insert, none per backfilled session, one at end of run, no-op without a loop, no exception on a failed send, writer succeeds | T3 | `test_timeline_signal.py` (29 tests; the batch has its own after-commit test, a robust-at-commit-exit test and a throttle-constant pin) |

**Frontend**

| Item | Task | Test |
|---|---|---|
| Routing, tab strip, active tab, scope memory, palette, shortcuts, settings text | T9 | `projectTabs.test.js`, `projectTabsRouting.test.js` |
| List shape: newest first, time right, head once, indent, separator breaks group, project name per scope | T4, T5, T7 | `rows.test.js`, `sessions.test.js`, row tests |
| Rendering: every kind and reason, unknown kind and reason, author labels, "~", escaping, truncation | T4, T7 | `describeEvent.test.js`, `authorLabel.test.js`, `strings from data are text, never markup` |
| Virtual scrolling: next page near the end, not re-entrant, Retry, stop at `has_more=false`, bottom pin off | T5, T6, T8 | `isNearEnd`, `loading is not re-entrant...`, `a failed first page shows an inline error...`, `a failed next page shows an inline error whose Retry sends one new request`, `the next page is requested only when the end of the list is in view` (60 events, a scroller double), `.at-bottom` pin; height estimates: `every row gets a height estimate once the scroller exists...`, `a row the scroller already measured keeps its measure`, `a new scroller (the list came back) is seeded again...` |
| Live: known id nothing, new id one fetch, coalesced, insertion order, duplicate, pagination cursor, back-dated, `truncated`, reconnect, resync nothing, no jump | T6, T10 | `feed.test.js`, panel "Staying current" tests |
| Day separators: local days, rows below, start of list | T5 | `rows.test.js` |
| Hidden and archived rules; purged and untitled fallbacks | T4, T7 | `sessions.test.js`, marker and no-entry row tests |
| Live insertion splits or merges groups; old live row not inserted while `has_more` | T5, T6 | `a live insertion recomputes heads...`, `mergeEvents` tests |
| Empty or unloaded workspace: state, no request | T5, T8 | `scope.test.js`, panel tests |
| Queue race | T6 | `the queue race: a row committed after a before query read but inside the new range is not lost` |
| Signal during the first page replayed; null cursor on an empty feed | T6 | two named tests |
| Set change reloads; old response ignored | T6, T8 | named tests |
| Shortcuts, key 4, settings text, palette | T9 | `projectTabsRouting.test.js` |
| Author labels none on non-exact; link rules for head, label, `spawned_by`; overwrite; every 6.1.3 fallback | T4, T6 | `authorLabel.test.js`, `describeEvent.test.js`, `sessions.test.js`, `feed.test.js` |
| `latest_id` and live cursor rule | T2, T6 | `test_latest_id_is_read_before_the_events`, `a before page never advances the live cursor` |
| Cursor round-trips microseconds, URL encoding included | T2, T4, T6 | `test_the_cursor_round_trips_microseconds...`, `the comparison keeps the microseconds...`, `buildTimelineUrl encodes the cursor and keeps after_id 0` |
| Instance events not duplicated on global | T2, T6 | `test_an_instance_event_appears_exactly_once...`, `mergeEvents ignores a duplicate id...` |
| `awaiting_input` wording matrix; parent once in `session.created` | T4 | two named tests |
| Compact-height mode: copy scrolls away, band stays; normal mode fixed | T8 (+ checklist) | `compact-height mode: the navigation copy is the first row of the list; normal mode has none`; real scrolling is in the browser checklist |
| Loading, empty, not-found states and no request | T8 | named tests |
| `settings.changed` diff | T4 | `settingsDiff.test.js` |
| Background-work badge | T4, T7 | `backgroundBadge.test.js`, badge row test |
| `derived` and `estimated` tooltips differ | T4 | `time: clock in the locale, "~" and a flag for estimated and derived, different captions` |
| Local-day boundaries at midnight and DST | T5 | three named tests (Europe/Paris, both changes) |
| First connect and tab return: one coalesced fetch each | T10 | two named tests |
| `after_id` ascending; client ignores `has_more` | T2, T6 | named tests |
| Existing tests that pin Stats markup updated and pass | T8, T9 | glass whitelist and 5c edit; Step 4 of T9 reruns the nine suites |
| No static router import | T8 | `no timeline module imports the router statically (circular imports break the hot reload)` |

### Task-independence audit

Each task was committed in the scratch copy and its own tests were run at that commit; the red step was run on the parent commit.

| Task | Depends on | Suite run at its commit | Result |
|---|---|---|---|
| T1 | nothing | `tests/test_timeline_*.py` (no bench) + `test_title_automation.py` | 536 passed |
| T2 | T1 (`provider` column, indexes) | `tests/test_timeline_api.py` | 80 passed |
| T3 | T1 (writers), T2 (none) | signal + provider + api + backfill + bulk suites | 193 passed |
| T4 | none (existing `backgroundWork.js`) | `utils/timeline/*.test.js` | 68 passed |
| T5 | T4 (`parseOccurredAt`, `groupHeadIdentity`) | `utils/timeline/*.test.js` | 95 passed |
| T6 | T4 (`compareNewestFirst`, `cursorOf`) | `utils/timeline/*.test.js` | 136 passed |
| T7 | T4, T5 | `components/timeline`, `utils/timeline` | 150 passed |
| T8 | T4 to T7 | + glass, panel-veil | 202 passed |
| T9 | T8 (`TimelinePanel.vue`) | whole frontend suite | green apart from the scratch-only failures (git history under `tests/browser/*`, one missing file) |
| T10 | T3 (frame), T6, T8, T9 | whole frontend suite | same |
| T11 | all | docs only | n/a (full run in its Step 3) |

Two intentional cross-task edits: T10 edits `TimelinePanel.vue`/`TimelinePanel.test.js` (T8) and the mount pin in `projectTabsRouting.test.js` (T9), and says so. T9 mounts `TimelinePanel` before it has the `active` prop; the prop's default (`true`) keeps the page behaving until T10. No commit leaves a suite red.

### Vacuity audit (mutation checks)

Run in the scratch copy: apply the mutation, run the suite, expect the named tests to FAIL, revert. The implementer repeats the ones of his task.

| Task | Mutation | Tests that must fail |
|---|---|---|
| T1 | drop `provider=provider` from the bulk-archive `EventSpec` | `test_the_bulk_archive_takes_the_provider_from_the_update_not_from_a_query` |
| T1 | drop `"provider": run.provider` in `timeline_boot.py` | `test_the_boot_reconciliation_uses_the_process_run_provider_even_without_a_session_row` |
| T1 | drop `provider=_provider_of(agent)` in `_write` | `test_the_process_hooks_pass_the_agent_provider` |
| T1 | drop `provider=session.provider` in `_row` (backfill) | `test_the_backfill_writes_the_session_provider` |
| T1 | drop `provider=session.provider` in `_apply_visibility_flag` | `test_the_flag_hooks_pass_the_session_provider` |
| T1 | drop `provider=row.provider` of the batch fallback | `test_the_batch_fallback_carries_the_provider` (a session with no row; the cache is emptied while the batch fails) |
| T1 | give the migration field a default | `test_migration_0159_adds_the_column_and_the_two_indexes_only` (null, no default, width 50) |
| T1 | stop passing `provider=` in the backfill row (or any constructor of `src/`) | `test_every_timeline_event_constructor_in_src_passes_the_provider` (an AST guard, with the writer test) |
| T1 | stop cutting the provider to `PROVIDER_MAX_LENGTH` | `test_a_provider_longer_than_the_column_is_cut_not_rejected` |
| T2 | cut `format_occurred_at` to milliseconds | `test_the_event_shape_is_exactly_the_public_one`, `test_the_cursor_round_trips_microseconds_for_rows_equal_to_the_millisecond` |
| T2 | compute `latest_id` over rows with a project only | `test_latest_id_is_the_highest_id_whatever_the_scope` (an instance event is the highest row) |
| T2 | change a documented limit (50 / 100 / 500) | `test_the_documented_limits` |
| T2 | order by `-occurred_at` only (drop the SQL `-id` tie-break) | `test_paging_over_equal_timestamps_in_a_multi_project_scope` (single-project and global scopes get `id DESC` from the index, so only a multi-project scope shows it) |
| T2 | slice the side-table ids without the chunk bound | `test_the_side_table_lookup_is_chunked` (no `IN` carries more than 2 ids) |
| T2 | adapt the cursor with `strftime("%Y-%m-%d %H:%M:%S.%f")` instead of `adapt_datetimefield_value` | `test_paging_by_before_returns_every_row_exactly_once_with_many_equal_timestamps` and the instance-merge paging tests FAIL (the loops are bounded, they do not hang) |
| T2 | drop the `OverflowError` handling / the id range check of `parse_cursor` | `test_bad_parameters_answer_400_with_an_error_body` (the `0001-01-01T00:00:00+05:00` and `99999999999999999999` cases) |
| T2 | drop the length guard of `_integer` (a 5000-digit text reaches `int()`) | the three `9 * 5000` cases of `test_bad_parameters_answer_400_with_an_error_body` (a plain `ValueError` would be a 500) |
| T2 | build the `after_id` query without the scope filter / with a plain `project_id IN` | `test_after_id_applies_the_scope_and_provider_and_includes_instance_events`; the plan test reads the real `_after_queryset` |
| T2 | drop `or None` after the side-table title | `test_an_untitled_session_has_a_null_title_in_the_side_table` |
| T2 | exclude `/api/timeline/` from the password-protected paths | `test_the_endpoint_is_behind_the_instance_authentication` (a real 401 once a password hash is set) |
| T2 | read `latest_id` after the events | `test_latest_id_is_read_before_the_events` |
| T2 | remove the unary plus of `_after_scope` | the two `test_the_after_id_query_scans_a_primary_key_range_never_a_scope_index` cases with a project set or a provider |
| T2 | never run the instance query | eight tests, among them `test_the_provider_filter_keeps_only_that_provider_and_instance_events`, `test_the_instance_query_uses_the_session_index` |
| T2 | key the merge by position instead of id | `test_a_row_that_both_queries_return_is_listed_once` |
| T3 | call `poke()` instead of `poke_on_commit()` | `test_a_single_write_requests_the_signal_only_after_the_commit`, `test_a_rolled_back_write_sends_nothing` |
| T3 | remove the coalescing guard `_timer is not None` | `test_a_burst_sends_one_message_with_the_latest_id`, `test_a_sustained_stream_sends_at_the_throttle_rate` |
| T3 | read the latest id under `run_under_db_write_lock` | the burst, sustained and failed-send tests |
| T3 | drop the end-of-run `poke()` of the backfill | `test_the_backfill_is_silent_per_session_and_signals_once_at_the_end_of_a_run_that_wrote` |
| T3 | poke on an empty or all-duplicate batch | `test_a_non_empty_batch_requests_one_signal_and_an_all_duplicate_batch_none` |
| T3 | the bulk path calls `poke()` instead of `poke_on_commit()` | `test_a_batch_requests_the_signal_only_after_the_commit` |
| T3 | arm the timer directly from the calling thread instead of `loop.call_soon_threadsafe(_arm)` | `test_pokes_from_worker_threads_reach_the_loop_safely` (asyncio debug mode refuses it, the poke is dropped, nothing is sent) |
| T3 | change `THROTTLE_SECONDS` | `test_the_throttle_is_a_third_of_a_second` |
| T3 | `transaction.on_commit(poke)` without `robust=True` | `test_a_failing_poke_at_commit_time_does_not_break_the_transaction_exit` |
| T3 | remove the shutdown `stop_timeline_signal()` after the backfill cancellation | the shutdown-block assertion of `test_the_server_source_starts_the_signal_before_the_backfill_and_stops_it_at_shutdown` |
| T3 | move `stop_timeline_signal()` after the cancellations in the outer `finally` of `run.py` | `test_the_server_source_starts_the_signal_before_the_backfill_and_stops_it_at_shutdown` (a SOURCE pin) |
| T4 | remove the precision rule of the author label | `no label on a row whose precision is not exact, for every actor` |
| T4 | show the parent next to an agent label | `session.created: the parent appears once...` |
| T4 | let `ask_user_question` show the tool | `awaiting_input: request_type wins over tool_name...` |
| T4 | link a hidden session | `a hidden session is plain text...`, `the session of an agent label follows the link rules...` |
| T4 | show four settings changes | `at most three changes are written...` |
| T4 | drop `isSubagent` from the agent label or from the `spawned_by` mention | `a known subagent is plain text in an agent author label and as the parent of session.created` |
| T4 | drop the `twicc_pid` / `pid` tooltip lines | `the tooltips of process and backend rows carry their technical facts` |
| T4 | drop the "~" of estimated times | `time: clock in the locale...` |
| T4 | order events with `Date.parse` | `the comparison keeps the microseconds that Date.parse would lose` (the later row has the lower id), `mergeEvents orders equal times by id and keeps microseconds` |
| T5 | do not break the group at a separator | `a day separator breaks the group...` |
| T5 | resolve a workspace before the projects are loaded | `an unresolved workspace, or projects not loaded yet...` |
| T6 | drop the "older than the tail" rule | `fetched rows are inserted by (occurred_at, id)...`, `a live row older than the loaded tail...` |
| T6 | let a `before` response advance the live cursor | `a before page never advances the live cursor` |
| T6 | skip the execution-time guard of the live task | `a deferred signal that the first page already covers is not replayed` |
| T6 | ignore the scope generation | `a response for a set that is no longer current is ignored...` |
| T6 | run tasks in parallel instead of serially | `the queue race...`, `signals during a live fetch coalesce...`, `a signal during the first page is deferred...` |
| T6 | append instead of sorting | six tests, among them `fetched rows are inserted by (occurred_at, id)...` |
| T6 | cursor = id of the newest event instead of `latest_id` | `the live cursor is the latest_id of the response, not the id of the newest event` |
| T6 | let a queued `before` page run after a truncated live reset (no `pageCursor` guard in `runBefore`) | `a next page queued behind a live fetch that comes back truncated is dropped: no wasted first-page request` |
| T6 | take the live cursor from the rows of the response instead of `latest_id` | `in a scoped feed the live cursor follows latest_id, which is global, even when no row is in scope` (a row-derived cursor never moves on an empty in-scope response, so every later signal would refetch) |
| T6 | let `notifyLatest` queue a fetch while the first page failed | `a signal while the first page failed does nothing: the Retry action reloads` |
| T6 | let a disposed feed accept `setScope` or `loadMore` | `a disposed feed ignores every call and keeps its state as it was` |
| T6 | on an `after_id` response, move the cursor to the highest id of the rows actually inserted | `the live cursor takes the latest_id of an after_id response even when its only row is dropped` |
| T6 | ignore `truncated` | `truncated: the list and the cursors are discarded...` |
| T6 | compare the scope by order | `a project-id set change reloads the first page; the same set in another order does not` |
| T6 | allow a second load while one runs | `loading is not re-entrant...` |
| T7 | render the wording with `v-html` | `a plain row...`, `strings from data are text, never markup`, `an unknown kind does not break the row` |
| T7 | pass `markers` to the mentions of the author label or of the wording | `the markers are on the group head only: the session of an author label and a parent carry none` |
| T8 | drop the near-end check (`isNearEnd`) | `the next page is requested only when the end of the list is in view`, `a failed next page shows an inline error whose Retry sends one new request` (a bare `view === 'list'` loads at once; the older 3-row test alone could not tell) |
| T8 | seed in a `flush: 'pre'` watcher on `items` only | the three height-estimate tests (the scroller does not exist yet at that moment) |
| T8 | seed a row the scroller already measured (no `getItemHeight` guard) | `a row the scroller already measured keeps its measure` |
| T8 | do not clear / do not trim the `seeded` set | `a new scroller (the list came back) is seeded again, and a row that left the list is seeded again if it returns` |
| T8 | unplug the Retry button of the footer | `a failed next page shows an inline error whose Retry sends one new request` |
| T8 | put the Retry button back in `slot="footer"` of the callout (a slot `wa-callout` does not have) | `a failed first page shows an inline error whose Retry reloads` and the next-page test, through `assertRenderableCallouts` |
| T8 | drop `@became-visible` / the `state.events.length` watcher / `refreshVisibleEnd` | `a scroller that becomes visible again asks the next page if the end is in view`, `a page that lands with the end of the list still in view asks for the next one by itself` |
| T8 | drop `feed.dispose()` on unmount | `unmounting the page disposes its feed` |
| T8 | drop the compact-height first row | `compact-height mode: the navigation copy is the first row...` |
| T8 | delete the navigation block (copy + divider) of `.timeline-state-area` | `compact-height mode: every state (loading, empty, not found, error) carries the navigation copy and its divider; normal mode none` |
| T8 | delete only the divider of that block | the same test (it counts both elements per state) |
| T8 | send `[]` for an unresolved scope | three scope-state tests |
| T9 | remove `project-stats` / `projects-stats` from the scope-memory sets | `scope memory restores a remembered Stats location as Stats...` |
| T9 | remove a name from `PROJECT_DETAIL_ROUTES` | `every route name the tabs can open exists in the router, and the scope page set matches them` |
| T9 | match only `project` / `projects-all` in `SidebarRail.scopeSelected` | `the rail keeps a scope selected on its Stats tab as well as on its Timeline` |
| T9 | redirect a git-less Git route to Stats instead of the scope home | `a scope page without a git root leaves the Git tab for the scope home, which is the Timeline` |
| T9 | change the shortcut map or the settings text | `projectTabs.test.js`, `the settings panel text follows the fixed map` |
| T10 | refresh on every `active` change | `coming back to the Timeline tab catches up once` |
| T10 | restore the scroll with a `flush: 'post'` watcher (capture after the DOM moved) | `scrolled down, rows inserted above put the first visible row back; at the top nothing moves` (the double's first visible row is read from the rendered rows, so the capture names the wrong row and no restore happens) and the shape pin |
| T10 | restore without `await nextTick()` | the same test (the rows rendered when the restore is called are still the old ones) and the shape pin |
| T10 | do not bind / do not unbind the window events | the live tests / `an unmounted page stops listening...` |
| T10 | skip `scrollKeeper.restore` | `scrolled down, rows inserted above put the first visible row back...` |
| T10 | restore even when the anchor index is unchanged | `scrolled down, rows added below (an older page): the scroll is left alone`, `planScrollRestore` |

Accepted weaker pins: the redirect of a scope page without a git root to the Timeline is pinned by a text assertion on the code and its comment plus the indirect `routeNameForTab` / `tabForRouteName` assertions, not by running the Vue watcher. The `wa-divider` of the navigation ROW inside the list (compact mode) is cosmetic and covered by the browser checklist only; the one of the other states is pinned. The scroller double does not model `suspended` / `disposed` (the real `scrollToAnchor` then silently does nothing and the scroller re-anchors by key on resume), a `display: none` container (`scrollTop` 0), nor the real row heights replacing the 34 px estimate: these are covered by the browser checklist of Task 11 ("scrolled down, trigger an event: the rows still do not shift", "leave the Timeline and come back").

Survivors that are equivalent to the rules in practice, not gaps: a `before` cursor taken from the list tail after a page (while `has_more` is true the tail IS the last fetched row, the very invariant of the insertion rule); `AFTER_ID_CAP + 1` against `501` (the same number); `disposed` in the `stale` test of the feed (`dispose()` also bumps the generation); in the signal module, the timer cancel in `stop_timeline_signal` (its `_fire` re-checks the loop, now `None`) and `except RuntimeError` in `poke` (the generic `except Exception` below it catches the same error and logs it); removing ONE of the three unary plus of `_after_scope` (`+project_id`, `+provider`, `+session_id`): the `OR` with a non-indexable term already blocks the scope indexes, so only removing all three is visible. The live cursor is NOT in this list: for a scoped page `latest_id` is global and a response holds in-scope rows only, so a cursor derived from rows would stay stale (T6 pins it).

Two guards are redundant on purpose and have no killing test of their own: the early return of `notifyLatest` is absent (the execution-time guard of the live task decides, and a no-op task costs nothing), and the `liveQueued` flag only avoids queuing no-op tasks (the fetch count is the same either way). Their behaviour is pinned through `signals during a live fetch coalesce...` and `a signal with a known latest id does nothing`.

### Placeholder scan

No `TBD`, `TODO`, "add appropriate handling", "similar to Task N" or empty step remains: every step shows the code (a complete file or the exact `git diff`), the command and the expected result. The `grep` that proves it:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-session-timeline && grep -n -E "TBD|TODO|FIXME|similar to Task|appropriate handling|XXX" docs/plans/2026-10-15-timeline-display-plan.md
```

Expected: no match except this line and the table rows that quote those words.

### Type and name consistency

| Name | Defined in | Used in |
|---|---|---|
| `resolve_provider(session_id, *, explicit=None, data=None)`, `clear_provider_cache()`, `PROVIDER_MAX_LENGTH`, `EventSpec.provider`, `record_event_sync(..., provider=None)` | T1 | T1 writers, T3 tests |
| `timeline_feed.parse_request`, `fetch_feed`, `FeedError`, `FeedRequest`, `AFTER_ID_CAP`, `SESSION_LOOKUP_CHUNK`, `format_occurred_at` | T2 | T2 view and tests |
| `timeline_signal.start_timeline_signal`, `stop_timeline_signal`, `poke`, `poke_on_commit`, `THROTTLE_SECONDS`, `MESSAGE_TYPE` | T3 | T3 writers, `cli/run.py`, tests |
| Frame `{"type": "timeline_changed", "latest_id": N}` | T3 | T10 (`useWebSocket.js`, `liveEvents.js`) |
| Response keys `events`, `has_more`, `truncated`, `latest_id`, `sessions`; side-table fields `title`, `project_id`, `provider`, `hidden`, `archived` | T2 | T4 (`sessions.js`), T6 (`feed.js`), T6 test server |
| `compareNewestFirst`, `cursorOf`, `parseOccurredAt`, `formatClock`, `truncateText`, `splitFirstLine`, `ARROW` | T4 `eventTime.js` | T4, T5, T6 |
| `sessionMention` shape `{id, title, provider, projectId, link, hidden, archived}` | T4 | `authorLabel.js`, `describeEvent.js`, T7 mention component |
| `describeEvent` view-model keys `id, kind, icon, severity, parts, tag, badge, detail, author, time, tooltip` | T4 | T7 row template |
| `EventRow` keys `key, type, event, instance, head, indented, identity`; `DayRow` keys `key, type, dayKey, label`; row keys `event-<id>`, `daysep-<dayKey>`, `timeline-nav` | T5, T8 | T7, T8 |
| `scope` `{state, kind, projectId, projectIds}` and `scope.kind` values `global`, `workspace`, `project`, `worktree` | T5 | `groupHeadIdentity` (T4), T8 |
| `createTimelineFeed` API `setScope`, `loadMore`, `retry`, `notifyLatest`, `refresh`, `idle`, `dispose`; state keys `status`, `events`, `sessions`, `hasMore`, `liveCursor`, `pageCursor`, `loadingMore`, `loadError` | T6 | T8, T10 |
| Window events `twicc:timeline-changed` (`detail.latestId`), `twicc:ws-reconnected`, `twicc:ws-first-open` | T10 | `useWebSocket.js`, `liveEvents.js`, panel |
| `MAX_EVENT_ID`, `_MAX_INTEGER_TEXT`, `_after_queryset` (`timeline_feed.py`), the `seedHeights` watcher on `[items, scrollerRef]` and the scroller API `getItemHeight` | T2, T8 | before / after_id range check; `TimelinePanel.vue`, scroller double |
| `onBecameVisible`, `refreshVisibleEnd` (`TimelinePanel.vue`), `assertRenderableCallouts` (panel test) | T8 | T8 panel and its test |
| Scroller API `getScrollState`, `getScrollAnchor`, `scrollToAnchor`, `scrollToTop`, `seedItemHeight`, `getVisibleRange` | existing `VirtualScroller.vue` | T8, T10 (`scrollKeeper.js`) |
| `PROJECT_TABS`, `DIRECT_TAB_MAP`, `visibleProjectTabs`, `directTab`, `tabForRouteName`, `routeNameForTab`; `PROJECT_DETAIL_ROUTES` | T9 | `ProjectDetailPanel.vue`, `App.vue`, `staticCommands.js`, tests |
| CSS classes `timeline-band`, `timeline-state`, `timeline-list`, `timeline-scroller`, `timeline-footer`, `timeline-nav-list`, row classes of T7 | T7, T8 | tests, styles |
| Route names `project-stats`, `projects-stats`; command ids `nav.project-tab.timeline`, `nav.project-tab.stats` | T9 | T9 tests |
| Index names `idx_timeline_occurred`, `idx_timeline_provider`; migration `0159_timeline_event_provider` | T1 | T2 plan tests, T11 hand-over |

### Not verified while writing this plan

- Nothing was run in a real browser or against a running server: scroll behaviour in Firefox, the glass look, the compact-height layout, the native bottom-pin override and the live WebSocket delivery on the dev server are covered by structure tests and by the checklist of Task 11 Step 5. The signal was verified end to end only up to the real channel layer (`test_a_message_reaches_a_websocket_client_through_the_updates_group`).
- The `wa-badge` `appearance="outlined"` value and the icon `clock-rotate-left` were checked in the installed Web Awesome typings and in the icon analysis, not rendered.
- The scratch-only frontend and backend failures (git history, missing files) were not reproduced in the worktree itself (the full suites are the first step of Task 11).
- `get_pending_title` is read per request in the side-table builder; its in-memory store is not shared across processes, which is fine because the view runs in the main process.
- The wiring of `cli/run.py` (start before the backfill, stop at shutdown, stop first in the partial-start `finally`) is pinned by a test of the SOURCE text, not by running the server; the real shutdown order is part of the user's restart check.
