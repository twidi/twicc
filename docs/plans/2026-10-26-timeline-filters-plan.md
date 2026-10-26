# Timeline Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Filter the events of the Timeline tab by view, event family, provider, author and session, server-side, with a per-timeline in-memory memory of the filters.

**Architecture:** The backend gets a definitions module (families, views, problems, the six-author partition, as Django `Q` builders) and new parameters on `GET /api/timeline/`, applied to the first page, the `before` pages and the `after_id` live fetch. The frontend gets a pure filters module (labels, normalisation, query, pills), a Pinia store keyed by the page id, a feed controller that carries a query next to the scope, a single sticky band holding a new `TimelineFilterBand.vue`, and an "Only this session" control in the session block headers.

**Tech Stack:** Django 6 / SQLite (WAL in production, in-memory shared cache in tests), pytest + pytest-django; Vue 3 `<script setup>`, Pinia, Web Awesome 3 (`wa-popover`, `wa-checkbox`, `wa-radio-group` through `SegmentedControl.vue`), node:test.

**Spec:** `docs/plans/2026-10-26-timeline-filters-design.md` (read it before any task; section numbers below refer to it).

**Status:** reviewed by independent adversarial subagents until PASS (9 rounds; the query plans and the band layout were measured, the latter in headless Chrome and Firefox with the real Web Awesome components).

## Global Constraints

- English for code, UI strings, comments and docs.
- Python: `orjson` for any JSON, `NamedTuple` for immutable records, ruff line length 120 (`uvx ruff check <files>`), no aliased imports without a real conflict.
- Backend tests: `cd /home/twidi/dev/twicc-poc && uv run pytest <paths>`; never `TWICC_DATA_DIR=`, never `--active`, never `uv pip`.
- Frontend tests: `cd /home/twidi/dev/twicc-poc/frontend && npm test` (node:test). Never `npm install` / `npm ci`.
- No timeline module imports the router statically (an existing test pins it); no store↔store static import cycle.
- Every Web Awesome component used is already imported in `frontend/src/main.js` (`popover`, `checkbox`, `radio-group`, `radio`, `tag`, `button`, `icon` are). Check attributes, slots and events in `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt`. Guard every `wa-show`/`wa-hide`/`wa-after-*` handler against bubbling from nested `wa-*` children (`event.target !== event.currentTarget`).
- Firefox parity (the owner uses Firefox only). Sizes in rem / tokens.
- No real DB writer in a test that is not about the writer (`tests/db_write_helpers.py::run_with_direct_writes` when a write must go through the lock helpers).
- No CHANGELOG entry. Commits: Conventional Commits, descriptive body, trailer `Co-Authored-By: Claude <exact running model name> <noreply@anthropic.com>`. Stage files by name (never `git add .`), never `git stash`.
- Keys sent to the API (copied from here into both sides; a sync test pins them, Task 4):
  - views: `all`, `without_turns`, `waits_problems`;
  - families: `sessions`, `processes`, `turns`, `waits`, `settings`, `organization`, `twicc`;
  - authors: `unknown`, `automatic`, `you`, `agent_cli_mcp`, `external_mcp`, `session_agent`;
  - providers: the `Provider` values (`claude_code`, `codex`).
- UI labels (spec 3.1, 3.2, 4.2, 4.3): views `All` / `Without turns` / `Waits & problems`, short `All` / `No turns` / `Waits`; families `Sessions`, `Processes`, `Turns`, `Waiting for you`, `Settings`, `Organization`, `TwiCC`; authors `Unknown`, `Automatic`, `You`, `Agent via CLI/MCP`, `External MCP`, `The session's agent`.

## Review Focus

1. A filter change while a first page, a `before` page or a live fetch is in flight: the late answer of the old filter must never land in the new list (Task 5, feed test "a response of the previous query is dropped").
2. A session row with a NULL provider and an instance row under a provider filter: both appear exactly once, at their time position, across a page boundary (Task 2, API test "rows without a provider are kept once across pages").
3. Two timelines (project A, project B) with different filters, switching between them and coming back: each keeps its own filters and reloads with them (Task 5 store test + Task 6 panel test "a page keeps its filters when shown again").
4. A forged `actor`/`via` pair or an unknown actor: the row is "Unknown", never dropped by a fully checked "Done by" group (Task 1, partition test over the full matrix).
5. The live `after_id` fetch with a filter over a busy range: rows that do not match are not shown, the cursor still advances to `latest_id`, the 500-row truncation still resets the list with the filter kept (Task 2 API test "after_id applies the filter after the scope range", Task 5 feed test "a truncated live fetch reloads with the same query").

---

### Task 1: Backend filter definitions

**Files:**
- Create: `src/twicc/core/services/timeline_filters.py`
- Test: `tests/test_timeline_filters.py`

**Interfaces:**
- Produces:
  - `VIEWS: tuple[str, ...] = ("all", "without_turns", "waits_problems")`
  - `FAMILIES: dict[str, tuple[str, ...]]` — family key → `TimelineKind` values (spec 4.2), insertion order as in Global Constraints.
  - `AUTHORS: tuple[str, ...]` — the six author keys, in rule order (`unknown`, `automatic`, `you`, `agent_cli_mcp`, `external_mcp`, `session_agent`).
  - `PROBLEM_REASONS: frozenset[str]` — `timeout_starting`, `timeout_assistant_turn`, `cron_restart_timeout`, `lost`, `auth_required`, `startup-failed`, `error`.
  - `KNOWN_ORIGIN_PAIRS: frozenset[tuple[str, str | None]]` — spec 4.3 "known pairs".
  - `class TimelineFilter(NamedTuple)`: `view: str = "all"`, `families: tuple[str, ...] = ()`, `authors: tuple[str, ...] = ()`, `providers: tuple[str, ...] = ()`, `session_id: str | None = None`. Empty tuple = no filter on that group.
  - `def problem_q() -> Q`, `def view_q(view: str) -> Q | None`, `def families_q(families: tuple[str, ...]) -> Q | None`, `def author_q(author: str) -> Q`, `def authors_q(authors: tuple[str, ...]) -> Q | None`, `def match_q(flt: TimelineFilter) -> Q | None` (AND of view, families and authors; `None` when nothing filters). Providers and session are NOT in `match_q`: Task 2 applies them in the scope part of each query (index use).
  - `def author_of(row: Mapping) -> str` — the pure-Python mirror of the partition (keys `kind`, `precision`, `actor`, `via`, `data`), used only by tests and by Task 3's generator to label synthetic rows.

- [ ] **Step 1: Write the failing tests** in `tests/test_timeline_filters.py`:
  - `test_every_kind_belongs_to_exactly_one_family` — the union of `FAMILIES` values equals `set(TimelineKind.values)` and their total length equals `len(TimelineKind)`.
  - `test_the_author_partition_is_total_and_disjoint` — build rows (`TimelineEvent.objects.bulk_create`, `django_db`) for the full matrix: every kind × actor in (`user`, `agent`, `system`, `external`, `ghost`) × via in (`None`, `ui`, `cli`, `rpc`, `mcp_internal`, `mcp_external`) × precision in (`exact`, `derived`, `estimated`), with the default `data={}`; plus `title.changed` rows with `data={"source": "auto"}`, `{"source": "agent"}`, `{"source": None}` and `{}` for every actor/via pair. Run ONE query per author (`set(TimelineEvent.objects.filter(author_q(a)).values_list("id", flat=True))`): the six sets are pairwise disjoint, their union is every row id, and each row's set equals `author_of(row)`.
  - `test_author_examples_from_the_spec` — table-driven, one assertion per line: derived `session.created`/system → `unknown`; estimated `process.ended` lost → `unknown`; exact `session.created`/system → `unknown`; `user`/`rpc` → `you`; `user`/`ui` → `you`; `agent`/`cli` → `agent_cli_mcp`; `agent`/None `turn.started` → `session_agent`; `title.changed` source `auto` actor `agent` via None → `automatic`; `title.changed` source `agent` → `session_agent`; exact `process.ended` reason `shutdown`/system → `automatic`; `external`/`mcp_external` → `external_mcp`; `external`/`cli` → `unknown`; `user`/`mcp_external` → `unknown`; actor `ghost` → `unknown`.
  - `test_problems_are_the_warning_and_danger_ends_and_the_unclean_stop` — `process.ended` with each of the 15 frontend reasons: matches `problem_q()` iff in `PROBLEM_REASONS`; `backend.ended` with `data={"clean": False}` matches, with `{"clean": True}` and `{}` does not; `awaiting_input.started` does not.
  - `test_views` — `view_q("all") is None`; `without_turns` excludes exactly `turn.started`/`turn.ended`; `waits_problems` keeps `awaiting_input.started` and problems only (not `awaiting_input.ended`).
  - `test_match_q_is_none_without_filters_and_ands_the_groups` — `match_q(TimelineFilter()) is None`; a filter with `families=("processes",)` and `authors=("automatic",)` keeps a system shutdown end and drops a user stop and a system `turn.ended`.

- [ ] **Step 2: Run and see them fail**
  Run: `uv run pytest tests/test_timeline_filters.py -q` — Expected: ImportError on `twicc.core.services.timeline_filters`.

- [ ] **Step 3: Implement `timeline_filters.py`.**
  - **Kind comparisons never drive an index.** Every comparison on `kind` in this module goes through one helper `kind_in(*kinds) -> Q` returning `Q(RawSQL("+kind IN (%s, …)", kinds, output_field=BooleanField()))` (the unary-plus technique of `_after_scope` in `timeline_feed.py`). A plain `kind IN (…)` makes SQLite (no ANALYZE) pick `idx_timeline_kind` and sort every matching row (`USE TEMP B-TREE FOR ORDER BY`: ~450k rows for Turns on the bench table); with `+kind` the page query keeps walking `idx_timeline_occurred` / the scope index in order and stops at `limit + 1`.
  - `generated_title = kind_in(TITLE_CHANGED) & Q(Exact(Coalesce(KT("data__source"), Value("", output_field=TextField())), "auto"))` — never NULL (`data` is not nullable, so Django adds no `IS NOT NULL` guard under negation; a title row without `source` would otherwise make `NOT (… = 'auto')` NULL and match no author).
  - `auto_base = generated_title | Q(actor=SYSTEM)`.
  - Partition, in rule order (spec 4.3): `unknown = Q(precision__in=(DERIVED, ESTIMATED)) | (kind_in(SESSION_CREATED) & Q(actor=SYSTEM)) | ~known_pair_q()`; `automatic = ~unknown & auto_base`; then every later rule is `~unknown & ~auto_base & <its own condition>`: `you` → `Q(actor=USER)`; `agent_cli_mcp` → `Q(actor=AGENT, via__in=("cli", "mcp_internal"))`; `external_mcp` → `Q(actor=EXTERNAL, via="mcp_external")`; `session_agent` → `Q(actor=AGENT, via__isnull=True)`. `known_pair_q()` is the OR of `KNOWN_ORIGIN_PAIRS` (a `None` via is `via__isnull=True`).
  - `problem_q() = (kind_in(PROCESS_ENDED) & Q(reason__in=PROBLEM_REASONS)) | (kind_in(BACKEND_ENDED) & Q(data__clean=False))`; `families_q` = `kind_in(<the kinds of the checked families>)`; `view_q("without_turns") = ~kind_in(TURN_STARTED, TURN_ENDED)`; `view_q("waits_problems") = kind_in(AWAITING_INPUT_STARTED) | problem_q()`.
  - Module docstring cites the spec sections and explains the unary plus. `author_of` applies the same rules in the same order on a mapping (a missing or `None` `source` is not `auto`).

- [ ] **Step 4: Run the tests** — Run: `uv run pytest tests/test_timeline_filters.py -q` — Expected: all pass. Then `uvx ruff check src/twicc/core/services/timeline_filters.py tests/test_timeline_filters.py`.

- [ ] **Step 5: Commit** `feat(timeline): define the filter families, views, problems and author partition` with the two files.

---

### Task 2: Filters on `GET /api/timeline/`

**Files:**
- Modify: `src/twicc/core/services/timeline_feed.py` (`FeedRequest`, `parse_request`, `_page_querysets`, `_after_scope`, `_after_queryset`, `_fetch_after`, module doc)
- Modify: `src/twicc/views.py:3416-3436` (docstring of `timeline` only: the new parameters)
- Test: `tests/test_timeline_api.py` (update `test_parse_request_defaults_and_normalisation`, provider tests), new `tests/test_timeline_api_filters.py`

**Interfaces:**
- Consumes: Task 1 (`TimelineFilter`, `match_q`, `VIEWS`, `FAMILIES`, `AUTHORS`).
- Produces:
  - `FeedRequest(project_ids: tuple[str, ...], limit: int, before, after_id, filter: TimelineFilter)` — the `provider` field moves into `filter.providers`.
  - Query parameters: `view=<key>`, `families=<k1,k2>`, `authors=<k1,k2>`, `provider=<p1,p2>` (comma list; a single value keeps working), `session=<session id>`. Unknown key in any list or unknown view → `FeedError` (400) naming the parameter. Empty items are dropped; duplicates collapse; order kept as given. **Normalisation** (spec 4.5): a group listing every value of its set (all families, all authors, all providers) becomes `()`. `view=all` is the default.

- [ ] **Step 1: Write the failing tests** in `tests/test_timeline_api_filters.py` (reuse `call`, `make_event`, `make_instance_event`, `ids`, `projects` by importing them from `tests.test_timeline_api` or a small local copy):
  - `test_unknown_filter_values_answer_400` — parametrised over `{"view": "x"}`, `{"families": "turns,nope"}`, `{"authors": "robot"}`, `{"provider": "codex,gemini"}`; status 400 and `"error"` in body.
  - `test_every_value_of_a_group_is_the_same_as_none` — `parse_request({"families": ",".join(FAMILIES)}).filter.families == ()`, same for authors and `provider=claude_code,codex`.
  - `test_the_view_and_the_groups_filter_the_first_page` — one row per family/author combination needed; `view=without_turns` drops the turns; `view=waits_problems` keeps a lost end and a wait start only; `families=processes,twicc&view=waits_problems` keeps only the problems; `authors=you` keeps only user rows.
  - `test_rows_without_a_provider_are_kept_once_across_pages` — scope one project; rows: codex at 1, claude_code at 2, a session row with `provider=None` at 3, an instance row at 4, codex at 5; `provider=codex&limit=2` then the `before` page: ids in time order `[5, 4]` then `[3, 1]`, no duplicate, no claude_code row.
  - `test_the_session_filter_keeps_that_session_and_drops_instance_rows` — `session=s2` returns only `session_id == "s2"` rows (with and without `project_ids`, with and without `provider`).
  - `test_after_id_applies_the_filter_after_the_scope_range` — 10 rows after the cursor, 3 matching `families=processes`: the `after_id` answer holds the 3, `latest_id` is the highest id of the table; with `AFTER_ID_CAP` monkeypatched to 5 and 6 scope rows, `truncated` is true even if fewer than 5 match.
  - `test_instance_rows_follow_the_twicc_family_and_the_authors` — `families=sessions` drops a `backend.started`; `authors=automatic` keeps it; `authors=unknown` keeps an estimated unclean `backend.ended`.
  - In `tests/test_timeline_api.py` (query-plan section, with the `busy` fixture and `plan_of`): `test_a_family_or_view_filter_keeps_the_ordered_index_and_sorts_nothing` — parametrised over `{"families": "turns"}`, `{"families": "sessions,processes"}`, `{"view": "without_turns"}`, `{"view": "waits_problems"}`, each alone and with `project_ids=-tl-api-a` (one project): the main page query's plan has no `USE TEMP B-TREE`, does not use `idx_timeline_kind`, and has no `MULTI-INDEX OR`. With a multi-project scope (`-tl-api-a,-tl-api-b`) assert only no `idx_timeline_kind` and no `MULTI-INDEX OR` (a multi-value `IN` always sorts per value, harmless: SQLite stops at the limit per value).
  - `test_a_session_filter_uses_the_session_index` — parametrised over `{"session": "s1"}`, `+ provider=codex`, `+ project_ids=-tl-api-a`, `+ both`: the single page query uses `idx_timeline_session` and has no `USE TEMP B-TREE`.
  - `test_the_null_provider_query_uses_the_provider_index` — with `project_ids=-tl-api-a&provider=codex`: the null-provider query (third of `_page_querysets`) uses `idx_timeline_provider`.
  - Update in `tests/test_timeline_api.py`: `test_parse_request_defaults_and_normalisation` expects `FeedRequest(("a", "b"), 50, None, None, TimelineFilter(providers=("codex",)))`; `test_the_provider_filter_keeps_only_that_provider_and_instance_events` also asserts a NULL-provider session row is kept; the query-count test `test_the_instance_events_query_runs_...` gets the new counts (provider filter: main + instance + null-provider = 3; session filter, with or without provider and project ids: 1).

- [ ] **Step 2: Run and see them fail** — `uv run pytest tests/test_timeline_api_filters.py tests/test_timeline_api.py -q` — Expected: failures on the new parameters and the changed `FeedRequest`.

- [ ] **Step 3: Implement.**
  - `parse_request` builds the `TimelineFilter` (one small helper `_keys(params, name, allowed) -> tuple[str, ...]` for the three lists).
  - `_page_querysets`: the `match_q` predicate (if any) is ANDed into every query. **One plain scope column per query, the most selective; the others are unary-plus `RawSQL`** (two plain scope columns make SQLite pick the wrong index: measured 225 ms for a session + provider on 1M rows, against 0.1 ms with `+provider`):
    - **With a session filter**: ONE query. `session_id = %s` plain; providers as `RawSQL("(+provider IN (…) OR +provider IS NULL)")`; project ids as `RawSQL("+project_id IN (…)")`. No instance query, no null-provider query.
    - **Without a session filter**: main query = keyset + `project_id__in` (if scoped, plain) + providers (plain `provider__in` when not scoped; `+provider IN (…)` when scoped); instance query (`session_id__isnull=True`) when scoped or providers are set; null-provider query when providers are set: `provider__isnull=True` plain + `session_id__isnull=False` + project ids as `+project_id IN (…)`.
    - `_fetch_page` already merges by id and cuts to `limit + 1`.
  - `after_id`: keep `_after_queryset` as the scope range query (ids, primary-key range, unary-plus columns, cap + 1); extend `_after_scope` with `(+provider IN (...) OR +provider IS NULL)` for providers and `+session_id = %s` for a session (then no `OR +session_id IS NULL`). `_fetch_after` reads the range ids, sets `truncated` from the range, then reads `TimelineEvent.objects.filter(id__in=range_ids)` with `match_q` (if any), ordered by id.
  - Module doc: one paragraph on the filters and why providers/session sit in the scope part (index) while `match_q` is a plain predicate.

- [ ] **Step 4: Run the tests** — `uv run pytest tests/test_timeline_api_filters.py tests/test_timeline_api.py tests/test_timeline_filters.py -q` — Expected: all pass, including the existing query-plan tests (`test_the_page_query_uses_an_index_...`, `test_the_after_id_query_scans_a_primary_key_range_...`). `uvx ruff check` on the touched files.

- [ ] **Step 5: Commit** `feat(timeline): filter the feed by view, family, provider, author and session`.

---

### Task 3: Speed of the filtered feed

**Files:**
- Create: `tests/test_timeline_filters_bench.py` (opt-in, skipped unless `TWICC_TIMELINE_BENCH=1`, like `tests/test_timeline_bulk_archive_bench.py`)
- Modify: `src/twicc/settings_test.py` — add `TWICC_TIMELINE_BENCH` to `_TEST_SWITCHES` (today the settings delete every other `TWICC_*` variable before collection, so the bench, and the existing `test_timeline_bulk_archive_bench.py`, are always skipped)
- Modify (only if the measure requires it): `src/twicc/core/models.py` (`TimelineEvent.Meta.indexes`), Create `src/twicc/core/migrations/<next free number>_timeline_filter_indexes.py` (`0164` today; parallel work on `main` may take it first: check `ls src/twicc/core/migrations` at that moment)
- Test: `tests/test_timeline_api.py` (query-plan tests for any new index)

**Interfaces:**
- Consumes: Task 2 (`parse_request`, `fetch_feed`), Task 1 (`author_of` to label generated rows, `FAMILIES`).

- [ ] **Step 1: Write the bench** `tests/test_timeline_filters_bench.py`:
  - Generator (spec 7, verbatim mix): 1,000,000 `TimelineEvent` rows by `bulk_create` in batches of 10,000, over 200 projects; one workspace = 20 projects holding ~30 % of the rows, one project ~10 %; kinds 45 % turns, 15 % waits, 20 % session + title, 10 % process, 10 % the rest (settings, organization, backend); authors 60 % session's agent, 25 % automatic, 8 % unknown, 6 % you, 1 % agent via CLI/MCP, 0 external; providers 70 % claude_code, 30 % codex, 0.1 % NULL; problems 0.5 % of all rows (`process.ended` with a reason of `PROBLEM_REASONS`). Deterministic seed. Assert the generated author shares with `author_of` (±1 point).
  - Measure, for each scope (global, workspace ids, project id), the first page and one `before` page (cursor at the middle of the table), 5 runs, median: each view alone; each single family, author, provider alone; a session filter alone (a session with ~50 rows); `external_mcp` (no row); and every view × one value per group × session combination listed in a table at the top of the file (the builder lists ≤ 30 representative ones, the rarest included).
  - Assertions: single filters < 100 ms; combinations < 500 ms. Print a table of all timings and the `EXPLAIN QUERY PLAN` of the slowest three.

- [ ] **Step 2: Run it** — `cd /home/twidi/dev/twicc-poc && TWICC_TIMELINE_BENCH=1 uv run pytest tests/test_timeline_filters_bench.py -s -q`. The summary must say `1 passed` (never `skipped`: a skip means the switch is not kept). Also check `TWICC_TIMELINE_BENCH=1 uv run pytest tests/test_timeline_bulk_archive_bench.py -q` is no longer skipped. Record the table in the task report.

- [ ] **Step 3: Only if a bound fails:** first look at the slowest plans. A rare family or view scanning the whole ordered index can be fixed by a query shape: one query per kind (`kind = x`, single value, uses `idx_timeline_kind`, no sort) merged in `_fetch_page` like the instance query. If a query shape changes, update `test_a_family_or_view_filter_keeps_the_ordered_index_and_sorts_nothing` and the `kind_in` rule in the Task 1 module docstring to say which shapes use the kind index on purpose. Otherwise add the smallest set of indexes that fixes it (candidate: `(actor, occurred_at, id)`; `idx_timeline_kind` already serves `kind = ? ORDER BY occurred_at DESC, id DESC` without a sort since SQLite index keys end with the rowid, and `(session_id, occurred_at, id)` exists; `tests/test_timeline_provider.py:92` pins the existing index names: never rename one), with a migration `<next free number>_timeline_filter_indexes.py` (no app code imported in the migration). Re-run Step 2 until every bound passes; keep an index only if its removal makes a bound fail (try it). Watch the same trap as `kind`: with an `(actor, occurred_at, id)` index, `authors=you,agent_cli_mcp` becomes an OR of actor equalities (MULTI-INDEX OR + sort); prefer unary-plus actor comparisons unless an index is measured to win. Add one query-plan test per kept index in `tests/test_timeline_api.py`, in the style of `test_the_page_query_uses_an_index_with_the_row_value_keyset_and_no_or`.
  If a bound still fails with indexes, stop and report the measures (do not change the spec's bounds).

- [ ] **Step 4: Run the regular suites of the area** — `uv run pytest tests/test_timeline_api.py tests/test_timeline_api_filters.py tests/test_timeline_filters.py tests/test_timeline_model.py tests/test_timeline_provider.py tests/test_timeline_backfill_index.py -q` — Expected: pass (the bench is skipped without the switch).

- [ ] **Step 5: Commit** `test(timeline): benchmark the filtered feed` (bench + `settings_test.py`). If indexes were added: a second commit `perf(timeline): index the filtered feed` (model, migration, query-plan tests); remind the owner that devctl applies the migration at the next start.

---

### Task 4: Frontend filter definitions

**Files:**
- Create: `frontend/src/utils/timeline/filters.js`, `frontend/src/utils/timeline/filters.test.js`
- Create: `tests/test_timeline_filters_sync.py` (Python test running `node` on the frontend module, the pattern of `tests/test_inline_artifact_compute.py:330`)

**Interfaces:**
- Produces (all pure, no store, no DOM):
  - `VIEWS: Array<{value, label, short}>`, `FAMILIES: Array<{value, label, hint}>`, `AUTHORS: Array<{value, label}>`, `PROVIDERS: string[]` (`['claude_code', 'codex']`), with the keys and labels of Global Constraints. Hints: Sessions "created, renamed"; Processes "started, stopped"; Turns "started, ended"; Waiting for you "approvals, questions"; Settings "model, effort, permissions"; Organization "archived, hidden, muted, pinned"; TwiCC "started, stopped".
  - `createFilters() -> {view: 'all', families: [], providers: [], authors: [], session: null}` (`session`: `{id, title}` or null).
  - `toggleValue(list, value, order) -> string[]` — a new list in the order of `order` (the group's constant order).
  - `toQuery(filters) -> Object<string, string>` — only non-default entries: `view` (unless `all`), `families`, `provider`, `authors` (comma lists in constant order, omitted when empty or complete), `session` (the id). Complete group = omitted (spec 4.5).
  - `queryKey(query) -> string` — stable string of a query (sorted keys).
  - `activeCount(filters) -> number` — number of checked panel values (spec 3.1; the view and the session do not count).
  - `isFiltered(filters) -> boolean` — view ≠ `all`, or any checked value, or a session (spec 3.5).
  - `groupPills(filters, {providerLabel}) -> Array<{group, label, values: string[], shown: string[], more: number, title: string}>` — one per non-empty group in the order Events, Provider, Done by, then the session pill `{group: 'session', label: 'Session', values: [title], ...}`; `shown` = the first 2 value labels, `more` = the rest count, `title` = all labels joined by ", ". Labels `Events`, `Provider`, `Done by`, `Session`. The session value is its captured title, else the first 8 characters of its id.

- [ ] **Step 1: Write the failing tests** `filters.test.js`: `toQuery` omits defaults and complete groups and keeps constant order whatever the toggle order; `queryKey` equal for equal queries built in different orders; `activeCount` ignores view and session; `isFiltered` for each trigger; `groupPills` order, `shown`/`more` with 4 families, the session fallback to the short id; `toggleValue` adds/removes keeping order.
  And `tests/test_timeline_filters_sync.py::test_frontend_keys_match_the_backend` — runs `node --input-type=module -e` importing `./frontend/src/utils/timeline/filters.js` and `./frontend/src/utils/timeline/describeEvent.js`, prints JSON `{views, families, authors, providers, problemReasons}` where `problemReasons` = the `PROCESS_ENDED_REASONS` keys whose severity is `warning` or `danger`; asserts equality with Task 1's `VIEWS`, `list(FAMILIES)`, `AUTHORS`, `[p.value for p in Provider]`, `PROBLEM_REASONS`.

- [ ] **Step 2: Run and see them fail** — `cd frontend && node --test src/utils/timeline/filters.test.js`; `uv run pytest tests/test_timeline_filters_sync.py -q`.

- [ ] **Step 3: Implement `filters.js`** (header comment citing the spec, like the other modules of `utils/timeline/`).

- [ ] **Step 4: Run** both commands again — Expected: pass.

- [ ] **Step 5: Commit** `feat(timeline): frontend filter definitions and query`.

---

### Task 5: Filters memory and the feed query

**Files:**
- Create: `frontend/src/stores/timelineFilters.js`, `frontend/src/stores/timelineFilters.test.js`
- Modify: `frontend/src/utils/timeline/feed.js` (`buildTimelineUrl`, `createTimelineFeed.setScope`, the three `fetchPage` calls), `frontend/src/utils/timeline/feed.test.js`
- Modify: `frontend/src/composables/useTimelineFeed.js` (`fetchTimelinePage` passes `query`)

**Interfaces:**
- Consumes: Task 4 (`createFilters`, `toggleValue`, `FAMILIES`, `AUTHORS`, `PROVIDERS`).
- Produces:
  - `useTimelineFiltersStore()` (Pinia, options style like `stores/sessionSelection.js`, never persisted). State `byPage: {}`. `filtersFor(pageId) -> filters` never writes: it returns the page's entry, or a shared frozen `DEFAULT_FILTERS` (`Object.freeze(createFilters())`) when the page has none yet. Every action creates the entry on its first write (`ensure(pageId)`), so reading inside a `computed` or a render never mutates state. Actions, each taking `pageId` first: `setView(pageId, view)`, `toggle(pageId, group, value)` (`group` in `families` | `providers` | `authors`), `clearGroup(pageId, group)`, `resetPanel(pageId)` (the three groups), `setSession(pageId, {id, title})`, `clearSession(pageId)`, `clearAll(pageId)` (groups, session, view → `all`).
  - `buildTimelineUrl({projectIds, before, afterId, limit, query})` — `query` entries appended after `project_ids`; the old `provider` argument is removed (no caller passes it).
  - `feed.setScope(projectIds, query = {})` — reloads when the project-id SET or `queryKey(query)` changes; every `fetchPage` call receives `{projectIds, query, before, afterId}` with the query of the current generation.

- [ ] **Step 1: Write the failing tests.**
  - Store: two page ids keep independent filters; `filtersFor` on an unknown page returns the frozen default and adds no key to `byPage`; after a write it returns the same object on every call; `toggle` keeps the constant order; `clearAll` resets the view; a fresh Pinia starts empty (no persistence).
  - Feed (`feed.test.js`): `setScope(ids, q)` with the same ids and an equal query (different key order) sends nothing new; a changed query reloads the first page with the new query; **"a response of the previous query is dropped"** — first page of query A pending, `setScope(ids, B)`, resolve A then B: the list holds B's events only; `before` and `after_id` requests carry the current query; **"a truncated live fetch reloads with the same query"**.
  - `buildTimelineUrl` puts the query entries in the URL and percent-encodes them.

- [ ] **Step 2: Run and see them fail** — `cd frontend && node --test src/stores/timelineFilters.test.js src/utils/timeline/feed.test.js`.

- [ ] **Step 3: Implement** the store, the feed change (the key becomes `scopeKeyOf(projectIds) + '\u0001' + queryKey(query)`; `null` project ids still mean "no request"), and `fetchTimelinePage({projectIds, query, before, afterId})`.

- [ ] **Step 4: Run** `npm test` — Expected: all pass. Existing tests keep their meaning; adapt only the `fetchPage` argument assertions to include `query: {}`, in `feed.test.js` and in `TimelinePanel.test.js` (the `deepEqual` at lines 192 and 206).

- [ ] **Step 5: Commit** `feat(timeline): per-timeline filters memory and filtered feed requests`.

---

### Task 6: One band for every state of the Timeline panel

**Files:**
- Modify: `frontend/src/components/timeline/TimelinePanel.vue`, `frontend/src/utils/timeline/rows.js` (a `state` row type and its height estimate), `frontend/src/components/timeline/TimelinePanel.test.js`, `frontend/src/utils/timeline/rows.test.js`

**Interfaces:**
- Consumes: Task 5 (`useTimelineFiltersStore`, `feed.setScope(ids, query)`), Task 4 (`toQuery`, `queryKey`, `isFiltered`).
- Produces: the panel always renders the `VirtualScroller` with the navigation list and ONE `StickyBand` in its `before` slot; the loading / empty / not-found / error / **no-match** states are one item `{key: 'timeline-state', type: 'state', state}` after the top room (not-found included). The band's default slot holds the "Timeline" label for now (Task 7 replaces it with the filter band). `ROW_HEIGHT_ESTIMATES.state` in `rows.js`.

- [ ] **Step 1: Write the failing tests** (TimelinePanel.test.js), keeping every existing state test but asserting the state now renders inside the scroller:
  - `the band is mounted once and is the same element across loading, list, empty and error` — capture the band node, drive the feed through the states, `assert.equal(sameNode, true)`.
  - `a filter change reloads the first page with the new query and scrolls to the top` — set a value in the store for this page id; the feed's `setScope` receives `toQuery(filters)`; the scroller's `scrollToTop` was called.
  - `the no-match state shows only after a successful first page with zero events and a filter set` — with filters set: loading → no "no match"; error → error wins; ready + 0 events → the no-match item with a "Clear all" control whose click calls `clearAll(pageId)`; without filters: the "No events yet" text. The message names the view when it is not `all` ("Nothing matches Waits & problems with these filters", else "Nothing matches these filters").
  - `a page keeps its filters when shown again` — mount the panel for project A, set filters, switch the `projectId` prop to B and back to A (and mount a second panel for A): A's query is sent again, B's is empty.
  - `loadMore is never asked while the state item is shown`.
  - `a failed first page shows one error, not the "load more" footer too` — the footer's error callout shows only for `state.loadError?.phase === 'before'`.
  - Update existing tests whose premise changes: "a new scroller (the list came back) is seeded again…" (line 393) becomes "a row that left the list is seeded again when it returns" (the scroller stays); the `structure()` expectations of the compact-height test (lines 441-456) now see the state item inside the scroller. The test fixture maps the new dependencies `../../stores/timelineFilters` and `../../utils/timeline/filters`.

- [ ] **Step 2: Run and see them fail** — `cd frontend && node --test src/components/timeline/TimelinePanel.test.js src/utils/timeline/rows.test.js`.

- [ ] **Step 3: Implement.** `items` = `[top, ...(view === 'list' ? eventRows : [stateItem])]`; `view` gains `no-match`; the `watch` on `scope.value.projectIds` becomes a watch on `[projectIds, queryKey(toQuery(filters))]` calling `feed.setScope(ids, query)` then `scrollerRef.value?.scrollToTop()` on a query change; the old `.timeline-state-area` markup becomes the state item's template (same texts, same Retry). `maybeLoadMore` keeps its `view === 'list'` guard; the footer's load error is gated on `state.loadError?.phase === 'before'`.

- [ ] **Step 4: Run** `npm test` — Expected: all pass.

- [ ] **Step 5: Commit** `refactor(timeline): keep one sticky band for every state of the panel`.

---

### Task 7: The filter band

**Files:**
- Create: `frontend/src/components/timeline/TimelineFilterBand.vue`, `frontend/src/components/timeline/TimelineFilterBand.test.js`, `frontend/src/utils/timeline/panelController.js` (+ `.test.js`)
- Modify: `frontend/src/components/ui/SegmentedControl.vue` (optional per-option `title`: `:title="option.title ?? option.label"` and `:aria-label="option.title ?? null"` on the `wa-radio`, which carries `role="radio"` on its host), `frontend/src/components/timeline/TimelinePanel.vue` (the band slot renders `<TimelineFilterBand ref="filterBandRef" :page-id="projectId" :active="active" />`; the no-match "Clear all" calls `clearAll(pageId)` then, after `nextTick`, `filterBandRef.value.focusFiltersButton()`), `frontend/src/components/timeline/TimelinePanel.test.js`

**Interfaces:**
- Consumes: Task 4 (`VIEWS`, `FAMILIES`, `AUTHORS`, `PROVIDERS`, `activeCount`, `groupPills`), Task 5 store, provider labels through `getProviderHelpers(provider)?.constructor?.serviceProductLabel` (as `utils/timeline/providerLabels.js` does) with the raw value as fallback.
- Produces:
  - `TimelineFilterBand` props `pageId: String`, `active: Boolean`; exposes `focusSessionPill()` and `focusFiltersButton()` (Task 8 uses them), both implemented with the controller's `focus`.
  - `createPanelController({ setTimeout = globalThis.setTimeout } = {})` in `panelController.js`, pure (no Vue, no DOM globals; elements are passed in). It owns the whole close-and-focus behaviour of the panel, because `wa-popover` closes on a bubble-phase document click (`handleDocumentClick` in `node_modules/@awesome.me/webawesome/dist/chunks/chunk.QJBP6HBR.js`), its native dialog close focuses the anchor, and the band does NOT use `v-popover-focus-fix` (it would undo the focus targets of spec 3.1). State: `keep` (one-shot), `shown`, `fromCode`, `saved` (element), `afterHideTarget` (function returning an element). Methods, called by the band:
    - `onBandClick(event, { band, button })` — band-root `click` listener in the CAPTURE phase (it runs while the clicked node is still attached, before a pill or "Clear all" removes itself, and also on keyboard activation, which fires `click` with no pointer): `keep = band.contains(event.target) && !button.contains(event.target)`, cleared by `setTimeout(0)`.
    - `onShow()` — the popover's own `wa-show`: `shown = true`, `afterHideTarget = null`, `saved = null`.
    - `onHide(event, { activeElement })` — the popover's own `wa-hide`: if `fromCode`, let it close (`saved = null`); else if `keep` (consumed) → `event.preventDefault()` and `afterHideTarget = null` (a vetoed close is no close); else `saved = activeElement` unless an `afterHideTarget` is set.
    - `onAfterHide({ button })` — the popover's own `wa-after-hide`: `shown = false`; if `fromCode` → reset and touch no focus (the user left the page); else if `afterHideTarget` → focus it; else if `saved?.isConnected` → `button.blur()` then `saved.focus()`; then reset `saved` and `afterHideTarget`.
    - `closeFromCode(popover)` — when `active` becomes false: only if `popover.open`, set `fromCode = true` and `popover.open = false` (the flag lives until that close's `wa-after-hide`, so it never bypasses a later veto).
    - `focus(getTarget, popover)` — focus `getTarget()` at once; then, if `shown && popover.open === false` (a close is under way: the document handler already set `open = false`, the dialog close will move the focus), also store `afterHideTarget = getTarget`.
  - Every popover handler of the band is guarded by `event.target === event.currentTarget` (nested `wa-checkbox`/`wa-*` events bubble).

- [ ] **Step 1: Write the failing tests.**
  - `panelController.test.js` (fake band/button/popover objects with `contains`, `focus`, `blur`, `isConnected`, `open`; a fake `setTimeout`). Each test replays an event order. For a user click or a key, the browser runs a microtask checkpoint after each listener, so the `nextTick` of the panel's handlers resolves BEFORE the popover's bubble-phase document listener: the usual order is `focus(target)` while `popover.open === true` → document handler sets `open = false` → `onHide` (saves the active element, which is the target) → `onAfterHide` (`button.blur()` then `saved.focus()`). The other order (`open` already false at the call) is kept as a defensive branch. Cases:
    - a band click on a view or a pill → `wa-hide` cancelled once, the next `wa-hide` (no click) not cancelled; a click on the Filters button → not cancelled; Escape (no click) → not cancelled; a band click, the timeout, then a list click → not cancelled (no stale flag);
    - usual order, for each target (the session pill's remove control, the Filters button — where `blur()` then `focus()` run on the same element): `onShow` → `focus(target)` with `open === true` → `popover.open = false` → `onHide` → `onAfterHide` → the target ends focused;
    - defensive order: `onShow` → `popover.open = false` → `focus(target)` → `onHide` → `onAfterHide` → the target is focused last, the button is not blurred after it;
    - same with a vetoed `onHide` (a band click before): no stored target, nothing focused by `onAfterHide`;
    - a list click on another control while the panel is open: `onHide` saves it, `onAfterHide` blurs the button and focuses it; a saved element no longer connected is not focused;
    - `closeFromCode` with the popover open closes even right after a band click, and its `onAfterHide` touches no focus; with the popover closed it sets nothing (the next band-click veto still works).
  - `TimelineFilterBand.test.js` (harness of `TimelineEventRow.test.js`: `compileComponent`, `makeRenderer`, a fresh Pinia):
    - the band line holds the title "Timeline", a `SegmentedControl` with the three views (labels and values of Global Constraints) bound to the store's view, and a "Filters" button showing `activeCount` only when > 0;
    - the `wa-popover` element comes directly after the Filters button in the DOM (before the pills: Tab from the button reaches the panel first; structure test), with `for` = the button id; it lists the three groups with `wa-checkbox` items in constant order, the family hints, the provider labels; checking calls `toggle(pageId, group, value)`; "Reset" calls `resetPanel(pageId)`; the panel's content wrapper has `max-height: calc(100dvh - 8rem)` and `overflow-y: auto` (pinned with `declarationsOf`);
    - the Filters button id is unique per instance (`useId()`, as `OrchestrationAnnotations.vue:20`) and is the popover's `for`; two bands mounted together have different ids;
    - pills: one per non-empty group plus the session, `+N` when `more > 0`, `title` = the full list; a pill's value text has `max-width`, `overflow: hidden`, `text-overflow: ellipsis`, `white-space: nowrap` (a 200-character session title never makes the band scroll sideways); a pill's remove control calls `clearGroup` / `clearSession`; "Clear all" calls `clearAll` and is absent with no pill;
    - focus targets (spec 3.1) through `focus` of the controller: after removing a pill, the next pill's remove control, else the previous one, else the Filters button; after "Clear all", the Filters button (assert the `focus` call on the stubbed element, the harness has no real focus);
    - the popover handlers are wired to the controller and ignore events whose target is not the popover itself;
    - `active` becoming false calls `closeFromCode`; on return the panel stays closed;
    - the band does not use `v-popover-focus-fix` (source pin);
    - layout levels (spec 3.1), a `layout` ref in `line` | `stacked` | `compact`:
      - Sizing first: `overflow: clip` does not make a scroll container, so a flex item keeps `min-width: auto` (its min-content width) and grows to fit its content: without `min-width: 0` the line never overflows, `scrollWidth === clientWidth` stays true, and the band pushes `StickyBand` (centered flex, `StickyBand.vue:17-25`) into a sideways scroll of the scroller. So: the band root (the flex item of `StickyBand`) is `flex: 1 1 auto; min-width: 0; padding-inline: var(--wa-space-m)` (the title starts at the start, aligned with the cards' inset `--timeline-card-inset`; the padding keeps the focus rings inside the scroller, which clips on both axes: `StickyBand` has no inline padding); the `line` element and the views + button line are `min-width: 0` at every level; the pills container is `min-width: 0` and each pill `min-width: 0; max-width: 100%`.
      - Per-level declarations (the band root holds the `line` element; the `line` element holds the title and the views + button line; pin every cell with `declarationsOf`):

        | Element | `line` | `stacked` | `compact` |
        |---|---|---|---|
        | `line` element | `display: flex; flex-wrap: nowrap; min-width: 0; overflow: clip; overflow-clip-margin: 0.25rem` | `flex-wrap: wrap` | `flex-wrap: wrap` |
        | title | — | `flex-basis: 100%` (its own row) | `flex-basis: 100%` |
        | views + button line | `display: flex; flex-wrap: nowrap; min-width: 0; overflow: visible` (it must NOT clip here, or its overflow never reaches the `line` element and the first threshold never fires) | `flex: 1 1 100%; flex-wrap: nowrap; overflow: clip; overflow-clip-margin: 0.25rem` (it fills its row, so its `clientWidth` is the available width) | `flex: 1 1 100%; flex-wrap: wrap; overflow: clip; overflow-clip-margin: 0.25rem` |
        | view options | full labels | full labels | `short` label, full one as `title`/`aria-label` |
        | Filters button label | shown | shown | visually hidden (never `display: none`) |
        | children of the views + button line (`SegmentedControl` root, Filters button) | `flex: none` | `flex: none` | `flex: none` |

        `flex: none` on the children is required: `wa-radio-group` lays its radios out with `flex-wrap: wrap` (`node_modules/@awesome.me/webawesome/dist/chunks/chunk.OJS3MSZI.js:29`), so a shrinkable control wraps its segments into rows and the line never overflows (measured: 2 to 3 rows of segments between ~470 and ~250 px). `flex: none` on the `SegmentedControl` root is the whole guard (measured: one row of segments at every width); no `::part` rule.

        `overflow-clip-margin` is a literal `0.25rem` (the app's focus ring: `--wa-focus-ring-width: 0.25rem` + `--wa-focus-ring-offset: 0rem`, the glow halo of `frontend/src/styles/glow.css:42-46`), with a comment saying so: Chrome rejects math functions in this property (`calc()`, `max()`, and a token that is itself a `calc()` all compute to `0px`, cutting the Filters button's ring at top and bottom); never write a `calc()` or a calc-valued token there.

      - Thresholds: `line` → `stacked` when the `line` element overflows (`scrollWidth > clientWidth`); `stacked` → `compact` when the views + button line overflows with full labels. In `compact` the Filters button hides its label span visually only, so the button keeps "Filters" (with the count) in its accessible name — the SAME button element: `wa-popover` binds its anchor only when `for` changes, so the button is never swapped for another element. The measured line must fill its row (above), else its `clientWidth` follows its content and a level never goes back.
      - Hysteresis: one record per threshold (`record.line`, `record.stacked`). A record is written ONLY when a threshold moves the level up (the overflowing line's `scrollWidth`, with full labels); going down keeps the record of the level above and never writes one. Go back one level only when the available width (`clientWidth` of that line, which fills its row) is `>=` that record. Known limits, accepted: under ~240–260 px of panel width (depending on the browser's fonts) the compact views no longer fit and the `line` element clips the last segment (nothing scrolls sideways; below phone widths); a sub-pixel overflow (≤ 0.5 px) at the line threshold is hidden by `scrollWidth` rounding (only that much of a ring's outer edge is cut); a record goes stale if the content width changes at a constant line width (font-size setting change, late web-font load); the band may then stay one level too low until the next width or count change.
      - Measured by a `ResizeObserver` on the lines AND their children (the `SegmentedControl` root, the Filters button). The post-flush `watch` on `activeCount` sets the level back to `line` and measures again from there (the count appearing or disappearing changes the content, not the line box, and a recorded width would be stale).
      - Tests: both thresholds, both ways back, the count appearing at a fixed width moves the level and the count disappearing moves it back, the button node is the same across the three levels and keeps "Filters" in its accessible text in `compact`, and the declarations of each level (the `min-width: 0` ones, the children's `flex: none`, the literal `overflow-clip-margin: 0.25rem` and the band root's `padding-inline` included) pinned with `declarationsOf`; a down-then-up sequence (compact → stacked → compact) writes no record on the way down. The test injects a fake `ResizeObserver` (set on `globalThis` for the test, restored after) and stub elements with `scrollWidth`/`clientWidth`.
  - `TimelinePanel.test.js`: the no-match "Clear all" clears the filters and calls the band's `focusFiltersButton()` after the next tick.
- [ ] **Step 2: Run and see them fail** — `cd frontend && node --test src/components/timeline/TimelineFilterBand.test.js src/utils/timeline/panelController.test.js`.
- [ ] **Step 3: Implement** the controller and the component. Styles: tokens only; pills wrap (`flex-wrap: wrap`); nothing scrolls sideways.
- [ ] **Step 4: Run** `npm test` — Expected: all pass, including the existing `TimelinePanel.test.js` band tests updated for the new slot content.
- [ ] **Step 5: Commit** `feat(timeline): the filter band (views, Filters panel, pills)`.

---

### Task 8: "Only this session"

**Files:**
- Modify: `frontend/src/components/timeline/TimelineEventRow.vue` (header control, emit), `frontend/src/components/timeline/TimelineEventRow.test.js`, `frontend/src/components/timeline/TimelinePanel.vue` (listen, store, focus through the band's methods of Task 7), `frontend/src/components/timeline/TimelinePanel.test.js`

**Interfaces:**
- Consumes: Task 5 store (`setSession`, `clearSession`), Task 7 band (`focusSessionPill()`, `focusFiltersButton()`).
- Produces: `TimelineEventRow` new prop `sessionFilterId: String|null` (default null) and emit `toggle-session` with `{id, title}`, where `title = context.sessions[sessionId]?.title || null` (NOT `identity.mention.title`: the header mention is built with `fullId: true` and falls back to the full id, so the short-id fallback of `groupPills` would never run).

- [ ] **Step 1: Write the failing tests.**
  - Row: a head row renders a button "Only this session" (`aria-pressed` false) in the header after the title; with `sessionFilterId` equal to the row's session it is pressed and labelled "Show every session"; a click emits `toggle-session` with the id and the side-table title (`context.sessions[id]?.title || null`); an instance row has no such control; the control's CSS: visible by default, `opacity: 0` only under `@media (hover: hover)` until the header is hovered or the control is focused (pin the declarations with `declarationsOf`).
  - Row: an untitled session emits `title: null`, and the pill built from it (Task 4 `groupPills`) shows the first 8 characters of the id.
  - Panel: a `toggle-session` from a row sets the session in the store for this page and calls the band's `focusSessionPill()` after the next tick; a second one on the same session clears it and calls `focusFiltersButton()` (the band's controller handles a close under way, Task 7).
- [ ] **Step 2: Run and see them fail** — `cd frontend && node --test src/components/timeline/TimelineEventRow.test.js src/components/timeline/TimelinePanel.test.js`.
- [ ] **Step 3: Implement** (a native `<button>` reset like `.timeline-row__expand`, with a `wa-icon name="filter"` and a visually hidden text; the header keeps its flex layout: title, control, project).
- [ ] **Step 4: Run** `npm test` — Expected: all pass.
- [ ] **Step 5: Commit** `feat(timeline): show only one session from its block header`.

---

### Task 9: Documentation and full verification

**Files:**
- Modify: `CLAUDE.md` and `AGENTS.md` (the `TimelineEvent` paragraph: the filter parameters of `GET /api/timeline/`, the per-timeline in-memory filters, the design link). The two paragraphs already differ in length: add the same sentence(s) to both.

- [ ] **Step 1:** Edit the two docs (one or two sentences, the style of the paragraph). The paragraph and the docs around it may already describe the CLI/RPC/MCP timeline (`core/services/timeline_listing.py`, the `twicc-timeline` skill, added on `main` in parallel): the new sentence is about the Timeline tab's filters of `GET /api/timeline/` and must not contradict that text.
- [ ] **Step 2: Full suites** — `cd /home/twidi/dev/twicc-poc && uv run pytest -q` (run in the background, expect all green) and `cd frontend && npm test` (all green). `uvx ruff check` on every Python file touched by the plan.
- [ ] **Step 3: Commit** `docs(timeline): document the timeline filters` with `CLAUDE.md` and `AGENTS.md` only.
- [ ] **Step 4:** Report to the owner (and propose, without editing it, to move "Filters" to done in the untracked note `docs/plans/2026-10-26-session-timeline-followups.md`): restart through devctl (and the migration if Task 3 added one); the browser checks: the band on Firefox desktop and phone width, the panel kept open on a view click and closed on a list click, focus after removing pills, live insertion under a filter.
