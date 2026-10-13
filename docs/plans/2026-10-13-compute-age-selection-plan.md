# Compute Age Selection Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Follow the checkboxes and review gates.

**Goal:** Select outdated sessions progressively, newest first, once their source modification is at least 30 seconds old.

**Architecture:** Share the age eligibility rule between both providers. Replace Claude's eager startup command pass with a persistent scheduler. Keep Codex's migration coordinator and apply the same age rule to its candidate selection.

**Tech Stack:** Python, asyncio, Django, SQLite, existing compute processes and per-run IPC.

**Spec:** [Approved behavior](2026-10-13-compute-age-selection-plan.md#approved-behavior).

## Approved Behavior

- Apply this selection to initial full compute and superseded retries for Claude and Codex.
- Select the most recently modified eligible outdated session.
- A session is age-eligible when `now - Session.mtime >= 30` seconds.
- Use the source file modification time already stored in `Session.mtime`, in Unix seconds.
- Recheck actual database state before each dispatch. A new modification postpones eligibility.
- A superseded session remains outdated and returns to the same selection process.
- If only recent sessions remain, wait and recheck until they become eligible. Do not exit early.
- Existing source revision guards remain necessary after selection.

## Global Constraints

- Work only in `.worktrees/feature-content-storage-compression` on its existing branch.
- No live server restart, manual migration, dependency installation, or live data mutation.
- Foreground writes retain priority over compute and maintenance.
- Preserve provider shutdown, run fencing, cancellation-safe retirement, migration leases, and active/external-writer exclusions.
- Keep failure backoff separate from ordinary superseded age deferral.
- Preserve initial top-level progress totals and cumulative database-confirmed readiness.
- Keep startup classification separate from successful readiness. A recent session can release classification without advancing X.
- Do not change content storage, compression, cold rules, scratch cleanup, or aggregate repair.
- Keep SQL and CPU work outside the asyncio event loop. Avoid busy polling and repeated full-history scans.

## Review Focus

- A newest recent session must not block an older eligible session. Empty candidate reads must follow the selection interval.
- New source writes must postpone eligibility without resetting progress or suppressing project notifications during persistent runs.
- Superseded results must retry before the initial population finishes, without duplicate pending commands.
- Worker failures, read failures, and cancellation must not strand work or accept retired results. Normal completion must drain writer bookkeeping.
- Initial age deferral must release startup classification independently of readiness and search indexing.

### Task 1: Progressive Age-Based Selection for Both Providers

**Files:**
- Create: `src/twicc/providers/compute_selection.py`
- Modify: `src/twicc/providers/background_compute_task.py`
- Modify: `src/twicc/providers/codex/background_compute.py`
- Modify: `src/twicc/providers/db_writer.py`
- Test: `tests/test_compute_age_selection.py`
- Test: `tests/test_compute_project_notifications.py`
- Adapt: `tests/test_background_compute_recovery.py`, `tests/test_background_compute_retries.py`, `tests/test_codex_compute_recovery.py`, `tests/test_codex_migration_scheduler.py`

**Interfaces:**
- Shared constant: `COMPUTE_MIN_SOURCE_AGE_SECONDS = 30.0`.
- Shared helper: `compute_age_delay(mtime: float, now: float) -> float`. Return zero at eligibility, otherwise the remaining age delay.
- Shared polling ceiling: `COMPUTE_SELECTION_POLL_SECONDS = 1.0`.
- Claude consumes the existing `ComputeApplied` acknowledgment queue and optional worker status queue, both fenced by run ID.
- Claude selects bounded database candidates ordered by descending `mtime`, then descending ID for ties. Exclude pending sessions and failed candidates whose retry deadline has not elapsed.
- Preserve a persistent worker across successful sessions. Bound Claude outstanding work to two sessions, including computed results awaiting application.
- Codex adds source `mtime` to its candidate observations, with a backward-compatible default for existing candidate constructors.
- Check age before beginning candidate preparation. A Codex migration can update its own source `mtime`; continue that prepared operation under its existing lease rather than deferring it again.
- Send the terminal worker command and await writer completion on success. Reserve abandonment for failure, shutdown, or cancellation.
- Preserve periodic project notifications independently of startup progress publication.
- Poll worker status rapidly while throttling empty candidate reads to the selection interval.
- Add `publish_progress: bool = True` as an optional keyword to `arm_compute_completion`. Coordinators disable writer progress explicitly while retaining project notification batching.
- Recheck actual outdated work after the graceful writer drain before declaring completion.

- [x] **Step 1: Write regression tests and observe RED.** Cover age boundaries, newest eligible selection, postponed modifications, recent-only waiting, superseded re-entry, bounded outstanding work, duplicate exclusion, fixed progress totals, startup classification, and explicit shutdown.
- [x] **Step 2: Implement the shared age helper.** Use wall-clock Unix seconds for source age and stop-aware waits for polling. Keep this module independent of Django imports for spawned workers.
- [x] **Step 3: Replace Claude's eager command pass.** Select from actual outdated DB rows throughout the run. Process worker status and writer acknowledgments. Continue during age deferral and restart failed runs through existing retirement. Keep failure backoff bounded at 60 seconds. Remove obsolete offset-window retry orchestration from the active path.
- [x] **Step 4: Apply age eligibility to Codex selection.** Keep its migration and external-writer gates. Wait while age-deferred candidates remain. Ordinary superseded outcomes return to selection without a special offset window.
- [x] **Step 5: Validate real recovery pipelines.** Run `TWICC_DATA_DIR=$PWD uv run pytest` on affected tests, including real worker/SQLite coverage. Run scoped `uvx ruff check` and `git diff --check`. Do not run concurrent pytest suites.
- [x] **Step 6: Commit and review.** Use a Conventional Commit with an explanatory body and the current model trailer. Require independent spec compliance and code quality approval before completion.

## Final Validation

- [ ] Review the full change against the approved behavior and runtime safety constraints.
- [ ] Run `TWICC_DATA_DIR=$PWD uv run pytest` after the final code and tests are stable.
- [ ] Record results and limits. Leave the running devctl unchanged and explain that activation requires a backend restart.
