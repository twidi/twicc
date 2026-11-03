# Simple logical turn cleanup implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Complete one scoped task with tests and a commit.

**Goal:** Delete old logical intervals and their inputs without retaining expiry history.

**Architecture:** Use the existing admitted DB writer for bounded ORM deletion. Run at startup and hourly. Keep only the runtime handling required for agents to continue after their tracking expires.

**Tech Stack:** Django, SQLite, asyncio.

**Spec:** `docs/plans/2026-11-02-confirmed-turn-completion-spec.md`, section “Live journal cleanup”. The user's explicit rollback and simple cleanup request supersedes the removed retention design.

## Global constraints

- Closed: 48 hours after `closed_at`. Open: 7 days after `opened_at`, including active agents.
- Cascade `LogicalTurnInput`. No new table, model, migration, durable expiry marker, historical cursor protection, or orphan ownership registry.
- Cleanup at startup and hourly. At most 500 intervals per admitted job; yield between batches and use one clock per cycle.
- Purge overdue opens before boot settlement. No agent termination or public timeline/unread changes from cleanup.
- Deleted journal data stays deleted. Native input delivery and actual completion still succeed.
- Preserve existing CLI/MCP APIs and fallback behavior; no guarantees after tracking expires.
- Stay on main; preserve unrelated files; no production migrate, restart, install, or CHANGELOG.

## Review focus

- Exact boundaries and long-running intervals closed recently.
- More than one boot batch before abandoned settlement.
- Live input and terminal publication after deletion, including retries and rollback.
- Shutdown and cancellation must drain admitted work without leaking the periodic task.
- No remnants of `LogicalTurnRetention` or unrelated reader redesign.

### Task 1: Simple cleanup and its runtime integration

**Files:** Create `src/twicc/logical_turn_cleanup_task.py` and `tests/test_logical_turn_cleanup_task.py`. Modify `src/twicc/providers/db_writer.py`, `src/twicc/cli/run.py`, `src/twicc/agent/process_run_cleanup.py`, and minimal `src/twicc/agent/base_manager.py`/`base_agent.py` handling. If necessary, adjust `src/twicc/core/services/logical_turns.py` to skip input binding after deletion. Extend `tests/test_completion_publication.py`/`test_completion_wait_tokens.py` for actual delivery and completion. Include the controller's spec and plan edits in this task's commit.

**Interfaces:** `CLOSED_RETENTION = timedelta(hours=48)`, `OPEN_RETENTION = timedelta(days=7)`, `CLEANUP_INTERVAL_SECONDS = 3600`, `CLEANUP_BATCH_SIZE = 500`. Provide `purge_logical_turns(*, now: datetime, limit: int = 500) -> int`, returning interval count. Use a `NamedTuple` writer job, a fixed-time cycle helper and `start_logical_turn_cleanup_task(stop_event: asyncio.Event) -> None`. The purge can live in the same module as the job; do not add a retention framework.

- [x] Write failing tests for both inclusive TTL boundaries, cascade, recent closure after long work, bounded batches, rollback, no cleanup broadcasts, boot backlog, startup/shutdown and failure retry.
- [x] Write failing tests for live expiry followed by accepted native input, stale snapshot retry, actual normal/exceptional closure, rollback and a new interval. Assert no journal resurrection, no failed native delivery and no duplicate public closure.
- [x] Implement the simple purge, admitted dispatcher, pre-settlement boot drain and hourly lifecycle. Preserve the existing signal-stop-first shutdown guarantee. Use existing runtime IDs/caches only when required; do not track historical expiry.
- [x] Run focused cleanup, publication, token, boot, startup and timeline lifecycle tests with `uv run pytest -q`. Check `makemigrations --check --dry-run --settings=twicc.settings_test`: no changes.
- [x] Commit this task's files only, with a conventional subject, body and `Co-Authored-By: Codex GPT-6.1 <codex@openai.com>`.

## Final verification

Review the task and combined diff against this scope. Run the complete backend suite once the reviews are clean. Preserve the four pre-existing untracked documents. Remove only this plan's temporary SDD workspace after completion.
