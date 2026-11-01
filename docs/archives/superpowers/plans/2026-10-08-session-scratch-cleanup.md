# Session Scratch Cleanup Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for inline execution.

**Goal:** Unify empty-directory cleanup and expired scratch removal.

**Architecture:** Keep one worker-thread cleanup pass. Protect shared scratch through session references and serialize the pass against agent starts.

**Tech Stack:** Python, Django ORM, asyncio, pytest.

**Spec:** The approved rules in this session.

## Global Constraints

- Run first after 30 minutes, then every 24 hours. Never run during startup.
- Retention is fixed at 30 days. Add no settings or migration.
- Preserve empty-directory cleanup. Use all four lifecycle timestamps.
- Recursively remove archived/hidden stale scratch and orphan scratch directories.
- Protect shared paths through spawn_root and scratch_dir annotations.
- Never follow directory symlinks or modify tmux behavior.
- Preserve the existing worktree disable flag and user-owned changes.

## Review Focus

- New agents starting during cleanup must retain their work directories.
- Nested scratch annotations must protect the whole containing session folder.
- Orphan folders may remain referenced by known sessions.
- An absent timestamp must not authorize recursive deletion.
- Failures for one directory must not stop cleanup of other directories.

## Task 1: Cleanup rules and shared ownership

**Files:** Modify `src/twicc/session_dirs_cleanup_task.py`, `src/twicc/agent/registry.py`, `src/twicc/settings.py`, and `tests/test_session_dirs_cleanup.py`.

**Interfaces:** Preserve `_prune_stale_session_dirs() -> tuple[int, int]`.

- [x] Add filesystem tests for recursive expiry, protected references, timestamps, active agents, symlinks, and orphans.
- [x] Run `uv run pytest tests/test_session_dirs_cleanup.py`; observe missing-behavior failures.
- [x] Implement reference loading, shared-folder protection, guarded cleanup, and updated documentation.
- [x] Run focused tests; expect all to pass.

## Task 2: Delayed scheduling and verification

**Files:** Modify the same cleanup module and test file.

**Interfaces:** Preserve `start_session_dirs_cleanup_task(stop_event)`.

- [x] Add deterministic loop tests for a 1800-second first wait, 86400-second later waits, shutdown, and disabling.
- [x] Observe scheduling failures, then implement the initial wait.
- [x] Run focused tests, relevant work-directory tests, and the backend suite.
- [x] Inspect the final diff. Do not restart servers, commit, or update the changelog.

## Execution Record

- Inline implementation stays on the current branch, as required by AGENTS.local.md.
- Existing changes remain user-owned. No commit, restart, changelog, or dependency change.
- First test run: 9 expected failures, 19 passes.
- Additional reference tests expose two missing protections; both now pass.
- Focused verification: 47 tests pass. Ruff and git diff checks pass.
- Backend suite: 9313 passed, 21 skipped, 7 failed, 75 warnings.
- All seven failures reproduce with the original cleanup and registry loaded from HEAD.
- Existing failures: test_slim_and_full_are_mutually_exclusive (six cases), and test_requested_settings_are_resolved_and_enforced_before_the_target.
- Final focused verification after test import cleanup: 47 passed.
