# Live Sync Duplicate Session Reads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the certain duplicate `core_session` reads of the live sync's content storage helpers, with no change to any decision, certificate, epoch, promotion, invalidation or concurrency behaviour.

**Architecture:** One shared primitive loads session metadata rows (ancestry, cold-policy and storage-state columns) and accepts rows already read in the same transaction (`known`). The content storage helpers gain an optional, keyword-only input for those rows; without it they behave as today. `lifecycle_change` reads each state (before / after its write) once; the live slice reads its session ancestry once and passes it to the helpers that run before its lifecycle write.

**Tech Stack:** Python 3.13, Django 6.1 ORM on SQLite (WAL, deferred `BEGIN`), pytest + pytest-django.

**Spec:** `docs/plans/2026-10-26-live-sync-duplicate-session-reads-design.md`

## Global Constraints

- No change to any decision, certificate, epoch, promotion target or cold-cache invalidation. The golden equivalence test of Task 1 is the arbiter; it must pass unchanged after every task.
- No path may run more queries than today.
- The conditional storage-incarnation `UPDATE` stays the first write of a live slice (SQLite write-lock reservation, spec "Why these reads are duplicates").
- Storage-state columns (`storage_incarnation`, `content_storage_version`, `content_state_epoch`, `plaintext_verified_epoch`) are never reused across a write to their own row. A value this transaction writes itself is written through into the shared row.
- Each site that passes already-read rows carries a short comment saying why they are still current (spec "Interface principle"): the slice metadata construction (Task 5), the `insert_ingested_items` call and `bind_persisted_items` (Task 6), and the places in `lifecycle_change` where `before_rows` / `after_rows` feed captures and carries (Task 4).
- Every new parameter is keyword-only and optional. Callers that pass nothing keep today's behaviour, except the root-is-session skip of `initialize_ingestion_session` (all callers, spec C1).
- Python style: ruff line length 120 (`uvx ruff check`), English names and comments, `NamedTuple` over dataclass, no cosmetic import aliases.
- Tests: `uv run pytest` from the repo root. Never set `TWICC_DATA_DIR`.
- Every test written by this plan runs under the `frozen_now` fixture (Task 1, in `tests/conftest.py`): both new modules declare `pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frozen_now")]`. The fixture lines are dated around `T0` (2026-09-27); with the real clock their sessions turn cold from 2026-10-27 and both decisions and query counts change.
- Commits: Conventional Commits, descriptive body, `Co-Authored-By` trailer with the running model. Stage files by name.

## Review Focus

1. **A subagent slice of more than 50 lines whose root holds a certificate.** The root must still hold a valid certificate after the slice (`plaintext_verified_epoch == content_state_epoch`), because each batch re-reads the epochs. Test: Task 6, `test_multibatch_child_slice_keeps_root_certified`.
2. **A writer outside the coordinator commits during a slice.** The first write of the slice must still be the conditional incarnation `UPDATE`, right after the slice-start reads. Test: Task 1, `test_first_write_of_a_slice_is_the_incarnation_update` (it pins today's order, so later tasks cannot move it).
3. **A `lifecycle_change` that re-parents a session.** The old root is no longer an ancestor of a changed row; its certificate carry must still load what it needs and give today's result. Test: Task 4, `test_reparent_carries_old_root_certificate_without_after_ancestry`.
4. **Missing or cyclic ancestry.** `load_native_metadata` must terminate and the resolution must stay unresolved, as today. Test: Task 2, `test_load_native_metadata_terminates_on_cycles_and_marks_missing_rows`.
5. **Non-live callers of `lifecycle_change` on several sessions at once** (bulk archive/hide). Same epochs, wakeups and invalidations as today. Test: Task 4, `test_bulk_lifecycle_change_keeps_wakeups_and_epochs`, plus the existing `test_timeline_bulk_archive.py`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/twicc/core/content_storage/state.py` | Native ancestry metadata, root decisions, certificates, incarnation | Add the metadata primitive and `ensure_known_storage_incarnation`; optional `metadata` / `storage_fields` / `fields` inputs |
| `src/twicc/core/content_storage/session_policy.py` | Per-session cold decisions | Optional `metadata` input |
| `src/twicc/core/content_storage/lifecycle.py` | `lifecycle_change` | Read each state once (C3) |
| `src/twicc/core/content_storage/ingestion.py` | Live ingestion helpers | Optional `metadata` input (C1, C2, C5); root-is-session skip |
| `src/twicc/core/content_storage/references.py` | `bind_persisted_items` | Optional `metadata` input (C4) |
| `src/twicc/providers/compute_base.py` | `sync_session_slice` / `_sync_session_slice` | Build the slice metadata once and pass it |
| `tests/test_live_sync_session_reads.py` | New: golden equivalence + slice query budgets | Create |
| `tests/fixtures/live_sync_session_reads.json` | New: golden record of today's behaviour | Create (generated) |
| `tests/test_content_session_metadata.py` | New: unit tests of the primitives and helper budgets | Create |

---

### Task 1: Characterize today's behaviour (golden equivalence + slice budgets)

No production change. Everything here runs against today's code and must stay green after every later task.

**Files:**
- Create: `tests/test_live_sync_session_reads.py`
- Create: `tests/fixtures/live_sync_session_reads.json` (generated by the test)
- Modify: `tests/conftest.py` (add the `frozen_now` fixture, not autouse)

**Interfaces:**
- Produces (used by Tasks 2–7): the `frozen_now` and `effects` fixtures in `tests/conftest.py` and the module constant `NOW`; the test module's helpers `storage_snapshot(session_ids) -> dict`, `slice_query_count(provider, kind, tmp_path) -> tuple[int, list[str]]`, plus the constant `BUDGETS: dict[tuple[str, str], int]`.

- [ ] **Step 1: Write the harness and the scenarios**

In `tests/test_live_sync_session_reads.py`:

- In `tests/conftest.py`: module constant `NOW = T0 + timedelta(days=1)` (`T0` imported at module level from `tests.codex_agent_run_fixtures`) and fixture `frozen_now(monkeypatch)`, which patches `django.utils.timezone.now` to return `NOW` (every content-storage module calls `timezone.now()` through the module, so one patch covers them). **Every test of this plan uses it** (see Global Constraints), golden replays and slice budgets alike: the fixture lines are dated around `T0` (2026-09-27), so with the real clock the sessions turn cold after 30 days and the counts change (a Codex root one-line slice runs 53 queries hot, 47 cold).
- Fixture `effects(monkeypatch)`, also in `tests/conftest.py` (Task 4 uses it from another module), returns an object with two lists, `wakeups` and `cold`. It wraps `twicc.core.content_storage.lifecycle.request_root_promotion` (records the session id, then calls the original) and `twicc.core.content_storage.cold_cache.schedule_cold_invalidation` (records `(sorted(ids), mappings, sorted(lifecycle_session_ids) if lifecycle_session_ids is not None else None)`, then calls the original). Each slice record stores both lists **sorted**: `invalidate_roots` iterates a set, so their order depends on `PYTHONHASHSEED`. Both are imported lazily by their callers, so patching the module attribute is enough.
- `storage_snapshot(session_ids) -> dict`: per session (sorted id): `incarnation_set` (bool), `content_storage_version`, `content_state_epoch`, `plaintext_verified_epoch`; per item (sorted `(session_id, line_num)`): current and original content `sha256` hex and `codec`; per content row (sorted sha256): `codec` and sorted `session_ids`.
- `replay(scenario, tmp_path, django_capture_on_commit_callbacks) -> list[dict]`: creates the sessions and their empty files with `LiveReplay` (`tests/test_codex_agent_runs_live.py:75`) for the Codex fixtures, then, for each slice of the scenario, appends that slice's lines to the session file itself and calls `compute.sync_session_slice(session_id, path, limits=LiveSyncLimits())` once (the default 500 lines / 4 MiB fits every scenario, the 121-line slice included) (not `LiveReplay.sync`, which drains until the file ends), each inside `django_capture_on_commit_callbacks(execute=True)` so commit-time wakeups run. After each slice it appends `{"slice": n, "session": sid, "wakeups": [...], "cold": [...], "storage": storage_snapshot(all sessions of the scenario)}` and clears the two lists. Every record snapshots all sessions of the scenario, not only the synced one: a child slice can change its root's incarnation, epochs and certificate.

Scenarios (one parametrized test id each). A scenario that needs another clock patches `timezone.now` again inside the test, after `frozen_now`. "Versioned" below means the session row has an incarnation and `content_storage_version=1` (set with a queryset `update` before the replay): an unversioned session fails `session_plaintext_metadata` (`state.py:250-253`) and becomes a promotion target on every slice, which would hide cold/hot decisions. Each scenario also asserts that it reached the path it is named after, with a spy, so it cannot pass vacuously. Every spy forwards `*args, **kwargs` to the original (Tasks 3–6 add keyword arguments to the spied helpers, and the golden test must not need edits). A spy patches the name in the module that calls it: `lifecycle` and `ingestion` import `capture_plaintext_certificate` / `carry_plaintext_certificate` from `state` at module level, so patch `lifecycle.capture_plaintext_certificate`, `ingestion.carry_plaintext_certificate`, and `lifecycle.update_lifecycle` for the stop step (`agent_runs.py` imports it lazily from `lifecycle`).

| Id | Setup | Precondition asserted |
|---|---|---|
| `t20_per_line` | `LiveReplay(ALL_FIXTURES["t20_sequence"]())`, root then child, one line per slice | sessions start with no incarnation; the stop step's `update_lifecycle` changed a row at least once |
| `t20_one_chunk` | same, one slice per session | — |
| `child_before_root` | t20, sync the whole child file before any root line | the child's root has no incarnation when the child's first slice starts |
| `certified_root_multibatch_child` | root synced, then certified (`storage_incarnation` set, `content_storage_version=1`, `plaintext_verified_epoch=content_state_epoch`); root lines from `_root_start()` (`tests/codex_agent_run_fixtures.py:188`); child file of 121 lines (`Rollout(AGENT_A, subagent=True)`: `meta`, then 60 `task_started`/`task_complete` pairs) synced in one slice | `bind_persisted_items` ran 3 times in that slice; root certified after it |
| `certified_root_replay_changed_date` | root synced and certified; set its `last_updated_at` to `NOW - 2 days`, `last_offset=0`, `last_line=0`; sync again | no new item inserted; `lifecycle.carry_plaintext_certificate` called at least once |
| `active_set` | clock at `T0 + 40 days` (every session cold unless active); t20 one chunk with `lifecycle.active_session_ids` set to `{ROOT, AGENT_A}` around each slice | wakeups are non-empty (the same replay without the active set records none) |
| `cold_root_becomes_hot` | root versioned, with `created_at`, `last_started_at`, `last_updated_at` at `NOW - 40 days`; sync t20 root lines | `ROOT` is among the wakeups (with a hot versioned root it is not) |
| `certified_root_new_lines` | t20 root synced up to line 5, then certified; sync root lines 6–8 in one slice | both captures inside the slice's `lifecycle_change` calls return `None` (the bindings invalidated the root before the first one, that first one's invalidation precedes the stop step's) and the final carry of `ingestion_plaintext_change` returns `True`: this is the path of the C2 capture at slice start (`ingestion.py:48`, carried at `:64`) |
| `owner_abort_two_children` | root spawns `AGENT_A` and `AGENT_B` (`spawn` at 2 and 3), `turn_aborted(30, "t1")`; child A: `meta(2.1)`, `task_started(2.5)`; child B: `meta(3.1)`, `task_started(3.5)` (children dated after the abort are excluded by the stop step, `agent_runs.py:561-562`); sync both children, then the root in one slice | the root slice's stop step changed two rows (two `update_lifecycle` calls returning 1) |
| `subagent_stops_itself_and_its_child` | `ALL_FIXTURES["subagent_owner_usage_limit"]()`, plus a `Session` row for `AGENT_G` (subagent of `ROOT`; `LiveReplay` does not create it because the fixture has no rollout for it); sync the root, then `AGENT_A` in one slice | in `AGENT_A`'s slice, two `update_lifecycle` calls each return 1, one of them on `AGENT_A` itself |
| `claude_root_per_line` | Claude session, lines from `tests.test_claude_agent_runs.line`: user, assistant text, user | — |

- [ ] **Step 2: Write the golden test**

`test_live_sync_matches_golden_record[scenario]`: one test per scenario, each in its own database. The golden file holds one key per scenario id. The test renders its own scenario's records; when `TWICC_UPDATE_FIXTURES=1`, it reads the file (if present), replaces its own key, and writes it back with `orjson.dumps(..., option=OPT_INDENT_2 | OPT_SORT_KEYS)`; then it asserts the file holds its key and that the stored value equals the rendered records after a JSON round-trip (`orjson.loads(rendered)`, as at `tests/test_attachment_pipeline_roundtrip.py:238`: the records hold tuples). Same update switch and messages as `tests/test_attachment_pipeline_roundtrip.py:233-242`.

- [ ] **Step 3: Write the slice budget tests**

`slice_query_count(provider, kind, tmp_path)`: creates a root (and, for `kind="child"`, a subagent of it; then only the child file gets lines, the root file stays empty and is never synced), writes all lines but the last to the measured session's file, syncs, appends the last line, and captures the queries of that one-line slice with `CaptureQueriesContext`. Lines: Codex `Rollout`: `meta(0)`, `task_started(1)`, `task_complete(2)`, `task_started(3)`; Claude: user, assistant text, user.

`BUDGETS = {("claude_code", "root"): 51, ("codex", "root"): 53, ("claude_code", "child"): 60, ("codex", "child"): 52}` (today's counts).

- `test_one_line_slice_query_budget[provider-kind]`: `assert count <= BUDGETS[(provider, kind)]`.
- `test_first_write_of_a_slice_is_the_incarnation_update[provider-kind]`: the first captured statement that is not `SELECT`, `SAVEPOINT` or `RELEASE` starts with `UPDATE "core_session" SET "storage_incarnation"`.

- [ ] **Step 4: Generate the golden file and run the module**

Run: `TWICC_UPDATE_FIXTURES=1 uv run pytest tests/test_live_sync_session_reads.py -q`, then `uv run pytest tests/test_live_sync_session_reads.py -q`
Expected: both PASS; `tests/fixtures/live_sync_session_reads.json` exists. If a budget differs from today's count, set `BUDGETS` to the measured value and report the difference.

- [ ] **Step 5: Time the full backend suite before any production change**

Run: `/usr/bin/time -f "%e s" uv run pytest -q -p no:cacheprovider`
Expected: all tests pass. Note the wall time; Task 7 compares with it.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_live_sync_session_reads.py tests/fixtures/live_sync_session_reads.json
git commit  # test: pin the live sync content-storage behaviour and query budgets
```

---

### Task 2: Shared native metadata and root/content decisions from loaded rows

**Files:**
- Modify: `src/twicc/core/content_storage/state.py:40-90` (`_resolve_root_decisions`, `resolve_root_decisions`)
- Modify: `src/twicc/core/content_storage/session_policy.py:25-35` (`resolve_content_decisions`)
- Create: `tests/test_content_session_metadata.py`

**Interfaces:**
- Produces, in `state.py`:
  - `STORAGE_STATE_FIELDS = ('storage_incarnation', 'content_storage_version', 'content_state_epoch', 'plaintext_verified_epoch')`
  - `NATIVE_METADATA_FIELDS = ('id', 'type', 'parent_session_id', *COLD_FIELDS, *STORAGE_STATE_FIELDS)`
  - `native_metadata_row(session: Session) -> dict` — the `NATIVE_METADATA_FIELDS` values of a freshly loaded instance (`parent_session_id` for the FK).
  - `load_native_metadata(session_ids: Iterable[str], *, known: Mapping[str, dict | None] | None = None, ancestors: bool = True) -> dict[str, dict | None]` — returns a new dict holding `known` plus a row (or `None` for a missing session) for every requested id and, when `ancestors`, for every ancestor reached through `type == 'subagent'` → `parent_session_id`, including ancestors of rows that came from `known`. It queries only ids absent from the result, by pages of `TREE_PAGE_SIZE`, one level at a time, and stops on cycles. It never mutates `known`.
  - `native_root_ids(session_ids: Iterable[str], *, metadata: Mapping[str, dict | None] | None = None) -> dict[str, str | None]` — native root per session (`None` when unresolved), same walk as today's inner `native_root`, on `load_native_metadata(session_ids, known=metadata)`.
  - `resolve_root_decisions(session_ids, now, active_root_ids, *, metadata=None)` and `_resolve_root_decisions(..., predicate, metadata=None)` — the frontier loop is replaced by `load_native_metadata(requested ∪ active_root_ids, known=metadata)`; results unchanged.
- Produces, in `session_policy.py`: `resolve_content_decisions(session_ids, now, active_ids, *, metadata: Mapping[str, dict | None] | None = None)` — ids present as keys in `metadata` use that row (`None` = missing session); the others are loaded as today.

- [ ] **Step 1: Write the failing tests** (`tests/test_content_session_metadata.py`, `pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("frozen_now")]`, sessions built like `tests/test_content_lifecycle.py:14` with a root and a subagent)

- `test_load_native_metadata_reads_requested_rows_and_ancestors`: for `[child.id]`, `django_assert_num_queries(2)`; result has `child` and `root`, each with every `NATIVE_METADATA_FIELDS` key.
- `test_load_native_metadata_skips_known_rows`: `known={child.id: native_metadata_row(child)}` → `django_assert_num_queries(1)` (only the root); with `known` holding both rows → `django_assert_num_queries(0)`; `known` is unchanged afterwards.
- `test_load_native_metadata_terminates_on_cycles_and_marks_missing_rows`: a subagent whose parent is itself (or a two-session cycle) and an unknown id → returns, unknown id maps to `None`; `native_root_ids` gives `None` for both, as `resolve_root_decisions` does today.
- `test_root_decisions_from_metadata_match_and_skip_reads`: for `[child.id]` with `active_root_ids=frozenset()`, `resolve_root_decisions(..., metadata=load_native_metadata([child.id]))` equals the no-metadata result and runs 0 queries.
- `test_content_decisions_from_metadata_match_and_skip_reads`: `resolve_content_decisions([root.id, child.id], now, frozenset({child.id}), metadata=...)` equals the no-metadata result; 0 queries.
- Existing `tests/test_content_lifecycle.py::test_batch_native_root_resolution_loads_actual_metadata` still runs exactly 2 queries.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_content_session_metadata.py -q`
Expected: FAIL (`ImportError` on `load_native_metadata`).

- [ ] **Step 3: Implement the interfaces above** in `state.py` and `session_policy.py`. Move today's inner `native_root` closure to a module-level `_native_root(session_id, metadata) -> str | None` used by both `_resolve_root_decisions` and `native_root_ids`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_content_session_metadata.py tests/test_content_lifecycle.py tests/test_live_sync_session_reads.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** (`refactor(content-storage): resolve native metadata from rows already read`)

---

### Task 3: Certificate, plaintext and invalidation helpers accept loaded rows

**Files:**
- Modify: `src/twicc/core/content_storage/state.py` (`ensure_storage_incarnation` neighbourhood, `invalidate_roots`, `capture_plaintext_certificate`, `carry_plaintext_certificate`, `session_plaintext_metadata`)
- Test: `tests/test_content_session_metadata.py`

**Interfaces:**
- Consumes: Task 2's `load_native_metadata`, `_resolve_root_decisions(..., metadata=)`, `resolve_content_decisions(..., metadata=)`.
- Produces, in `state.py`:
  - `ensure_known_storage_incarnation(session_id: str, known: str | None) -> str` — runs today's conditional `UPDATE ... WHERE storage_incarnation IS NULL` with a generated uuid; returns that uuid when the update matched, else `known` when truthy, else reads the value as today.
  - `capture_plaintext_certificate(root_id, *, metadata=None, storage_fields: Mapping | None = None)` — `metadata` feeds the root decision (`frozenset()` active set, as today); `storage_fields` holds the root's four storage-state columns and replaces the read only when given. Callers pass `storage_fields` only when no write touched the root row since they read it.
  - `carry_plaintext_certificate(snapshot, *, metadata=None)` — `metadata` feeds the root decision only; the epoch read and the conditional `UPDATE` stay.
  - `session_plaintext_metadata(session_id, *, fields: Mapping | None = None)` — `fields` (with `content_storage_version`, `storage_incarnation`) replaces the row read; the SQL probe on items stays.
  - `invalidate_roots(root_ids, *, promotion_targets=(), metadata=None)` — passes `metadata` to `resolve_content_decisions` for the promotion targets.

- [ ] **Step 1: Write the failing tests** (same module; a certified root = incarnation set, `content_storage_version=1`, `content_state_epoch == plaintext_verified_epoch`, recent dates. The helpers need a caller-owned transaction: the test transaction of the `db` fixture is enough, so the counted block holds no `SAVEPOINT`. Any `metadata` passed is loaded before the counted block.)

- `test_ensure_known_incarnation_keeps_one_write`: on a row with an incarnation and `known` = it → returns `known`, captured statements are exactly one `UPDATE` (no `SELECT`); on a row without one and `known=None` → returns a uuid equal to the stored value, one `UPDATE`.
- `test_capture_from_metadata_and_storage_fields_reads_nothing`: certified root; `capture_plaintext_certificate(root.id, metadata=md, storage_fields=md[root.id])` equals the no-argument result; 0 queries. With `metadata` only: 1 query (the storage fields).
- `test_carry_from_metadata_keeps_epoch_read`: two fresh setups (a carry writes `plaintext_verified_epoch`, so a second carry on the same rows returns `False`). In each: capture the root's certificate, bump its epoch with `invalidate_roots([root.id])`, then carry, once without and once with `metadata=md`. Both return `True`; the metadata carry runs exactly 2 queries (epoch `SELECT` + `UPDATE`), the plain one 3.
- `test_plaintext_metadata_from_fields_keeps_the_probe`: child with an incarnation and `content_storage_version=1` (otherwise the function returns before its probe); `session_plaintext_metadata(child.id, fields=md[child.id])` equals the no-argument result; 1 query (the items probe) against 2 without `fields`.
- `test_invalidate_roots_targets_from_metadata`: with a hot target, `invalidate_roots([root.id], promotion_targets=[child.id], metadata=md)` runs exactly 1 query (the epoch `UPDATE`) and queues the same wakeup as without `metadata` (use `django_capture_on_commit_callbacks`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_content_session_metadata.py -q`
Expected: FAIL (unknown keyword arguments / missing `ensure_known_storage_incarnation`).

- [ ] **Step 3: Implement the interfaces above.**

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_content_session_metadata.py tests/test_content_lifecycle.py tests/test_content_ingestion.py tests/test_live_sync_session_reads.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** (`refactor(content-storage): let certificate helpers take rows already read`)

---

### Task 4: `lifecycle_change` reads each state once (spec C3)

**Files:**
- Modify: `src/twicc/core/content_storage/lifecycle.py:43-96` (`_metadata`, `_root_map`, `lifecycle_change`)
- Test: `tests/test_content_session_metadata.py`

**Interfaces:**
- Consumes: Tasks 2–3 (`load_native_metadata`, `native_root_ids`, `resolve_content_decisions(metadata=)`, `capture_plaintext_certificate(metadata=, storage_fields=)`, `session_plaintext_metadata(fields=)`, `invalidate_roots(metadata=)`, `carry_plaintext_certificate(metadata=)`).
- Produces: `lifecycle_change(session_ids, *, defer_plaintext_inspection=False)` — same signature and effects.

New sequence (spec C3):
1. Before the yield: `before_rows = load_native_metadata(ids)`; `before` = rows of `ids` that exist; old roots from `native_root_ids(ids, metadata=before_rows)` (unresolved → the session itself, as today); old decisions from `resolve_content_decisions(ids, now, frozenset(), metadata=before_rows)`; certificates from `capture_plaintext_certificate(root, metadata=before_rows, storage_fields=before_rows.get(root))`.
2. After the yield: `after_rows = load_native_metadata(ids, ancestors=False)`; `changed` as today on `LIFECYCLE_FIELDS`; return when empty.
3. When `changed`: `after_rows = load_native_metadata(changed, known=after_rows)` (fresh ancestors only); new roots, decisions (active set), `session_plaintext_metadata(sid, fields=after_rows[sid])`, `invalidate_roots(..., metadata=after_rows)` and `carry_plaintext_certificate(certificate, metadata=after_rows)` all from it. Nothing read before the write feeds the new state. `_metadata` and `_root_map` disappear if unused.

Edge cases kept as today: a row deleted by the body maps to `None` in `after_rows` and counts as absent in `after` (so it is in `changed`, and `mappings` is true for it); an unresolved session maps to itself as its root, for the new roots as for the old ones (`lifecycle.py:53`), so `None` never reaches `schedule_cold_invalidation` or `invalidate_roots`; certificates are still captured for the old roots only.

- [ ] **Step 1: Write the failing tests** (root and subagent of `tests/test_content_lifecycle.py:14`'s shape, both with an incarnation, `content_storage_version=1`, a certified root, recent dates. The counted block is `with django_assert_num_queries(n): with transaction.atomic(): ...`, so each budget includes 2 `SAVEPOINT` and 2 `RELEASE` statements: the test's atomic and `lifecycle_change`'s own.)

- `test_lifecycle_change_budget[root-unchanged]`: `save_lifecycle(root, update_fields=['last_updated_at'])` with no value change → `django_assert_num_queries(7)` (today 11).
- `test_lifecycle_change_budget[child-unchanged]` → 8 (today 12).
- `test_lifecycle_change_budget[root-changed]`: `last_updated_at` moved 5 s later → 10 (today 17); afterwards the root is still certified.
- `test_lifecycle_change_budget[child-changed]` → 13 (today 21); afterwards the root is still certified.
- `test_reparent_carries_old_root_certificate_without_after_ancestry`: two certified roots `r1`, `r2` (epoch 4, verified 4) and a child of `r1`; `update_lifecycle(Session.objects.filter(pk=child.id), parent_session=r2)` → `r1` is invalidated and carried (epoch 5, verified 5), `r2` is invalidated and not carried (epoch 5, verified 4): today certificates are captured for old roots only (`lifecycle.py:66`). `r1` is not in the after-write rows (it is no longer an ancestor of a changed row), so its carry loads it.
- `test_carry_uses_after_write_policy`: a certified, hot, unarchived root (epoch 4, verified 4) with its activity dates at `NOW - 20 days`; `update_lifecycle(Session.objects.filter(pk=root.id), archived=True)` → epoch 5, verified 4: the archived root is now cold (15-day limit), so the carry refuses. A carry fed with the before-write rows would accept and give 5/5. Second case: a certified root that becomes a subagent of another root (`update_lifecycle(..., type='subagent', parent_session=other)`) is not carried either.
- `test_bulk_lifecycle_change_keeps_wakeups_and_epochs`: three versioned sessions (two roots, one child), archived, with their activity dates at `NOW - 20 days` (cold while archived, hot once unarchived: the limits are 15 and 30 days); `update_lifecycle(Session.objects.filter(pk__in=...), archived=False)` → three wakeups (one per session) and each root's epoch +1, written as literal expectations in the test.

- [ ] **Step 2: Run them to verify the budget tests fail**

Run: `uv run pytest tests/test_content_session_metadata.py -k "lifecycle or reparent or bulk" -q`
Expected: the four budget tests FAIL with today's counts (11, 12, 17, 21); the behaviour tests PASS.

- [ ] **Step 3: Rewrite `lifecycle_change`** as described above.

- [ ] **Step 4: Run the tests and the content suites**

Run: `uv run pytest tests/test_content_session_metadata.py tests/test_content_lifecycle.py tests/test_timeline_bulk_archive.py tests/test_compute_lifecycle_publication.py tests/test_live_sync_session_reads.py -q`
Expected: PASS. A budget that differs from the planned number means the design is not applied as planned: stop and report.

- [ ] **Step 5: Commit** (`perf(content-storage): read each lifecycle state once`)

---

### Task 5: Slice-start ancestry and storage incarnation (spec C1, C2)

**Files:**
- Modify: `src/twicc/core/content_storage/ingestion.py:37-64` (`ingestion_plaintext_change`), `:95-115` (`initialize_ingestion_session`)
- Modify: `src/twicc/providers/compute_base.py:4282-4307` (`sync_session_slice`, `_sync_session_slice` signature and first call)
- Test: `tests/test_content_session_metadata.py`

**Interfaces:**
- Consumes: `native_metadata_row`, `load_native_metadata`, `native_root_ids`, `ensure_known_storage_incarnation`, `capture_plaintext_certificate(metadata=, storage_fields=)`.
- Produces:
  - `ingestion_plaintext_change(session, *, metadata: dict | None = None)` — captures with `metadata=metadata, storage_fields=metadata.get(session.id)` when given. Its final carry (`ingestion.py:64`) keeps today's call, without `metadata`: it runs after the lifecycle write (spec Non-goals).
  - `initialize_ingestion_session(session, *, invalidate=False, invalidate_mappings=False, metadata: dict | None = None)`:
    - without `metadata`: today's code, except the root is not ensured again when it is the session itself (all callers);
    - with `metadata`: `ensure_known_storage_incarnation(session.id, metadata[session.id]['storage_incarnation'])`, written through to `session.storage_incarnation` and `metadata[session.id]`; root from `native_root_ids([session.id], metadata=metadata)`; a distinct root whose row in `metadata` has no incarnation is ensured with `ensure_known_storage_incarnation(root_id, None)` (one `UPDATE`, the written value returned) and written through; the `invalidate` branch keeps its behaviour.
  - `BaseSessionCompute._sync_session_slice(self, session, file_path, raw, *, metadata: dict | None = None)`.
  - In `sync_session_slice`, in the branch where the file exists (not when `file_path` is missing: that branch reads nothing else today, and a subagent's ancestry read would add a query): `metadata = load_native_metadata([session.id], known={session.id: native_metadata_row(session)})`, built before `ingestion_plaintext_change` and passed to it and to `_sync_session_slice`. The dict is mutable on purpose (write-through). Its storage-state values are valid only until the slice's first content-storage write; a comment at the construction site says so.

- [ ] **Step 1: Write the failing tests** (the counted block is `with django_assert_num_queries(n): with transaction.atomic(): initialize_ingestion_session(...)`, so each budget includes 1 `SAVEPOINT` and 1 `RELEASE`; `metadata` is built with `load_native_metadata` before the counted block)

- `test_initialize_without_metadata_skips_root_is_session[root]`: root with an incarnation → `django_assert_num_queries(5)` (today 7); stored incarnation unchanged.
- `test_initialize_without_metadata[child]` → 8 (unchanged).
- `test_initialize_with_metadata[root]` → 3 (one `UPDATE`); `session.storage_incarnation` equals the stored value.
- `test_initialize_with_metadata[child-root-has-incarnation]` → 3.
- `test_initialize_with_metadata[child-root-without-incarnation]` → 4; both rows hold an incarnation afterwards, equal to `metadata[...]['storage_incarnation']`.
- `test_initialize_with_metadata[session-without-incarnation]` → 3; afterwards `session.storage_incarnation` and `metadata[session.id]['storage_incarnation']` equal the stored value (no read-back).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_content_session_metadata.py -k initialize -q`
Expected: FAIL (unknown `metadata` keyword; root budget 7).

- [ ] **Step 3: Implement the interfaces above** in `ingestion.py` and `compute_base.py`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_content_session_metadata.py tests/test_content_ingestion.py tests/test_live_sync_session_reads.py tests/test_live_sync_slices.py tests/test_live_sync_rollback.py tests/test_codex_agent_runs_live.py tests/test_claude_agent_runs.py tests/test_inline_artifact_compute.py tests/test_compute_lifecycle_publication.py -q`
Expected: PASS, including `test_first_write_of_a_slice_is_the_incarnation_update`.

- [ ] **Step 5: Commit** (`perf(live-sync): read the slice ancestry once and keep the incarnation write`)

---

### Task 6: Bindings and per-batch policy reuse the slice ancestry (spec C4, C5)

**Files:**
- Modify: `src/twicc/core/content_storage/references.py:171-207` (`bind_persisted_items`)
- Modify: `src/twicc/core/content_storage/ingestion.py:127-181` (`insert_ingested_items`)
- Modify: `src/twicc/providers/compute_base.py` (the `insert_ingested_items` call at `:4629`)
- Test: `tests/test_content_session_metadata.py`

**Interfaces:**
- Consumes: Task 5's slice `metadata`; `native_root_ids`; `capture_plaintext_certificate(metadata=)`; `carry_plaintext_certificate(metadata=)`; `resolve_content_decisions(metadata=)`.
- Produces:
  - `bind_persisted_items(bindings, *, metadata: Mapping | None = None)` — with `metadata`: roots from `native_root_ids`, captures with `metadata=metadata` and **no** `storage_fields`, carries with `metadata=metadata`; without: today's code.
  - `insert_ingested_items(session, items, *, promote=True, prepared=None, metadata: Mapping | None = None)` — the per-batch cold decision uses `resolve_content_decisions([session.id], now, active, metadata=metadata)`; `bind_persisted_items(bindings, metadata=metadata)`. Its own `with ingestion_plaintext_change(session):` (`ingestion.py:137`) gets no `metadata`: in the live slice the outer proof makes it yield at once, and `test_insert_with_metadata_skips_policy_read` counts on its capture still running.
  - `_sync_session_slice` passes its `metadata` to `insert_ingested_items`. `promote_ingested_items` is not changed (it runs after the lifecycle write).

- [ ] **Step 1: Write the failing tests**

- `test_bind_with_metadata_skips_ancestry_reads[child-certified-root]`: same bindings bound twice in two fresh setups, once without and once with `metadata=load_native_metadata([child.id])` (loaded before the counted block) → the metadata run has exactly 4 fewer queries (child ancestry 2, capture's resolution 1, carry's resolution 1) and leaves the same epochs and certificate.
- `test_bind_with_metadata_skips_ancestry_reads[root]` → exactly 1 fewer.
- `test_insert_with_metadata_skips_policy_read`: 60 items (two batches) on a root, `metadata` loaded before both measured runs, measured twice from the same database state (each measurement inside `transaction.atomic()` ending with `transaction.set_rollback(True)`, so the second run does not find the first run's contents) → exactly 4 fewer queries with `metadata` (per batch: the policy read and the root resolution of `bind_persisted_items`) and the same stored items, compared inside each atomic block before its rollback.
- `test_multibatch_child_slice_keeps_root_certified`: certified root; the 121-line subagent slice of the golden scenario, through `sync_session_slice` → afterwards `root.plaintext_verified_epoch == root.content_state_epoch` and the golden scenario `certified_root_multibatch_child` still passes.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_content_session_metadata.py -k "bind or insert or multibatch" -q`
Expected: FAIL (unknown `metadata` keyword); `test_multibatch_child_slice_keeps_root_certified` PASSES already.

- [ ] **Step 3: Implement the interfaces above.**

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_content_session_metadata.py tests/test_content_ingestion.py tests/test_content_storage_*.py tests/test_live_sync_session_reads.py tests/test_codex_agent_runs_live.py tests/test_claude_agent_runs.py tests/test_inline_artifact_compute.py tests/test_compute_lifecycle_publication.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** (`perf(live-sync): reuse the slice ancestry for bindings and batch policy`)

---

### Task 7: Lock the budgets and verify the whole suite

**Files:**
- Modify: `tests/test_live_sync_session_reads.py` (`BUDGETS`)

- [ ] **Step 1: Measure the one-line slices**

Run: `uv run pytest tests/test_live_sync_session_reads.py -k budget -q -s` with a temporary print of the counts (the budget tests run under `frozen_now`, so the sessions are hot).
Expected, exactly: each root budget of Task 1 minus 16, each child budget minus 18. With the setup described in Task 1: Claude root **35** (51 − 16), Codex root **37** (53 − 16), Claude child **42** (60 − 18), Codex child **34** (52 − 18). Per slice, a root drops 2 (C2 capture), 4 (initialize), 1 (C5), 1 (C4), 8 (`lifecycle_change`); a child drops 2 − 1 (C2 capture, plus the ancestor read at slice start), 5, 1, 3, 8. Any other count means a site is not wired as planned (the unit tests of Tasks 4–6 call the helpers directly and do not cover the `compute_base.py` call sites): stop and report, do not pin it.

- [ ] **Step 2: Pin `BUDGETS` to the measured counts and switch the assertion to `==`**, so any added read fails.

- [ ] **Step 3: Run the full backend suite and time it**

Run: `/usr/bin/time -f "%e s" uv run pytest -q -p no:cacheprovider`
Expected: all tests pass. Report the wall time next to Task 1's measurement.

- [ ] **Step 4: Lint**

Run: `uvx ruff check src/twicc/core/content_storage src/twicc/providers/compute_base.py tests/conftest.py tests/test_live_sync_session_reads.py tests/test_content_session_metadata.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit** (`test(live-sync): lock the one-line slice query budgets`), with the before/after counts and the suite timing in the body.
