# MCP logical conclusions fix implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development. One scoped task, focused tests, and a commit.

**Goal:** Preserve distinct MCP logical conclusions and resume live subscriptions after their consumed interval expires.

**Architecture:** Add an optional logical identity to the existing event builder. Recover only a missing consumed logical reference in the event runtime, and clear it with a guarded write on the existing subscription.

**Tech Stack:** Django, SQLite, existing MCP worker and writer queue.

**Spec:** `docs/plans/2026-11-02-confirmed-turn-completion-spec.md`, “MCP conclusion identity and live progress”. The user approves the concrete two-file patch in conversation.

## Global constraints

- Production changes stay in `src/twicc/mcp/events/runtime.py` and `src/twicc/mcp/events/delivery.py`.
- No model, migration, new persisted field, retention marker, CLI change, cleanup change, or event payload schema change.
- Use the consumed logical ID, never a currently open or merely selected interval, for managed conclusion identity.
- Same logical conclusion keeps its event ID across retry/reload. Distinct logical conclusions get distinct IDs without transcript movement.
- Keep pending request identity and unmanaged transcript identity unchanged.
- Recover only when the consumed progress reference is missing in the owning session. Preserve the line/time cursors.
- Guard persisted reset by subscription ID, created_at generation, history numbering, and exact previous logical reference. Delayed writes cannot erase or restore newer logical progress.
- Use the existing admitted writer queue. No worker-thread SQL mutation. No false event or invalid cursor during recovery.
- Stay on main. Preserve unrelated files. No production migrate, restart, install, or CHANGELOG.

## Review focus

- Distinct empty endings with one transcript end; stable retries and reloading.
- Logical progress must not name an open turn or contaminate legacy/pending event identities.
- Idle active subscriptions resume after purge, both in memory and after a backend reload.
- Reset generation/epoch/reference races and delayed callback receipts preserve newer progress.
- Other detector failures must not silently become a progress reset.

### Task 1: Correct MCP logical conclusion identity and recover purged progress

**Files:** Modify `src/twicc/mcp/events/runtime.py`, `src/twicc/mcp/events/delivery.py`. Create `tests/test_mcp_events_logical_turns.py`; extend existing `tests/test_mcp_events_writer.py`/delivery tests only when their fixtures fit. Include the controller's spec and plan edits in the task commit.

**Interfaces:** Extend `build_occurrence(..., logical_turn_id: str | None = None)`; managed terminal keys use `logical_turn:<id>` before hashing. Pending request keys still take priority. Runtime supplies an ID only when the wait consumes a new logical interval. Add an immutable internal writer item for resetting an exact previous `last_logical_turn_id` to null; do not change `CursorWrite`'s existing meaning of None. Reuse `_new_wait(monitor, reset_logical=True)` and keep `monitor.cursor_line`/`cursor_at` unchanged.

- [x] Promote the diagnostic at `/tmp/twicc-session-concluded-diagnostic-01a19d6a.py` into proper tests. Observe the two failures before code changes.
- [x] Add tests for stable logical retry/reload IDs, unchanged pending/legacy IDs, purged progress recovery with a real guarded SQL write and reload, and no consumed-answer replay.
- [x] Add writer tests for changed generation, changed epoch, changed current logical ID, and delayed old/new callback receipt ordering. Verify cursor/time fields remain unchanged by reset.
- [x] Implement both local changes. Build the replacement wait before posting the reset, then update memory only after the post succeeds. Recover on a later tick instead of recursive retry. Do not emit the failed detector result.
- [x] Run focused `uv run pytest -q` covering all MCP events and completion wait/progress integration. Report exact commands and results. Check schema with `makemigrations --check --dry-run --settings=twicc.settings_test`.
- [x] Commit scoped files with a conventional lowercase subject, explanatory body and `Co-Authored-By: Codex GPT-6.1 <codex@openai.com>`.

## Final validation

Independent task review and final combined review verify the two-file scope and event/progress contracts. The controller runs remaining validation after the reviews. Remove only this plan's temporary SDD workspace when done.
