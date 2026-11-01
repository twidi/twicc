# Cron Auto-Restart on Process Death — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a Claude process with active cron jobs dies from a non-manual cause, automatically restart the session and its crons with infinite retries — because losing a user's crons is never acceptable.

**Architecture:** There is ONE shared function to restart a session's crons: `restart_session_crons(session_id)`. It collects data from DB, sends the restart message, waits for success, retries infinitely on failure. Both startup and runtime paths call it identically — the only difference is `initial_delay` (0 at startup, 10s at runtime to let the API recover).

**Tech Stack:** Python asyncio, Django ORM, Claude Agent SDK

**Important:** No tests (per project quality approach). No linting. No commits at any step — the user will commit when ready.

---

## Problem Statement

When a Claude session with active cron jobs encounters an API error (e.g., Anthropic 500), SDK crash, or timeout, the process dies and the crons are effectively lost until the user manually intervenes or TwiCC is restarted. The current behavior:

1. `_handle_error()` sets `kill_reason = "error"`, process goes DEAD
2. `_on_state_change()` preserves the `ProcessRun` in DB (since it has crons)
3. **Nothing else happens** — the crons sit idle in the DB
4. Only on next TwiCC startup, `restart_all_session_crons()` attempts to revive them — with only 8 tries

This is unacceptable: if a user sets up a recurring cron and an API error kills the session at 2 AM, the crons should restart automatically, not wait for someone to notice and manually resume or restart TwiCC.

## Kill Reason Decision Matrix

| `kill_reason` | Source | Auto-restart? | Why |
|---|---|---|---|
| `"manual"` | User clicked Stop | ❌ No | Explicit user intent |
| `"shutdown"` | TwiCC shutting down | ❌ No | Will be handled by `restart_all_session_crons()` on next startup |
| `"error"` | API 500, SDK error, crash | ✅ Yes | Transient, should self-heal |
| `"timeout"` | Inactivity timeout | N/A | **Cannot happen:** `check_and_stop_timed_out_processes()` skips processes with active crons |
| `"cron_restart_timeout"` | Timeout during restart attempt | ✅ Yes | Already a failed retry, keep trying |

Note: `"timeout"` is effectively impossible for cron-bearing processes. The trigger uses `kill_reason not in ("manual", "shutdown")`, so it would handle it if it ever occurred.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/twicc/cron_restart.py` | Modify | Refactor `restart_session_crons()` to be self-contained (data collection + infinite retry), extract `_collect_restart_data()`, simplify `_prepare_restarts()` |
| `src/twicc/agent/manager.py` | Modify | Add `_cron_restart_tasks` dict, `_restart_crons_for_session()` wrapper, trigger from `_on_state_change()`, cancel in `send_to_session()` and `shutdown()` |
| `src/twicc/cli/run.py` | Modify | Pass `shutdown_event` to `restart_all_session_crons()` |

## Design overview

```
Startup:                                    Runtime (_on_state_change):
  _prepare_restarts()                         _restart_crons_for_session(session_id)
    cleanup orphan ProcessRuns                  try/except/finally for task cleanup
    dedup ProcessRuns                             │
    filter: only session_ids with crons           │
    → returns [session_id, ...]                   │
  for each session_id in parallel:                │
    │                                             │
    └──► restart_session_crons(sid) ◄─────────────┘
           │
           ├─ _collect_restart_data(sid)  ← fresh DB read on each attempt
           ├─ _build_restart_message()
           └─ send + wait USER_TURN + retry forever
```

Both paths call the same `restart_session_crons()`. Startup just adds cleanup beforehand and parallelizes across sessions.

---

## Task 1: Refactor `cron_restart.py`

**Files:**
- Modify: `src/twicc/cron_restart.py`

### Step 1: Update imports and constants

- [ ] Add `Iterator` import and replace `RETRY_DELAYS`:

```python
# Add to imports:
from collections.abc import Iterator

# Replace:
RETRY_DELAYS = [0, 5, 15, 30, 60, 120, 180, 300]

# With:
RETRY_ESCALATION = [0, 5, 15, 30, 60, 120]
MAX_RETRY_DELAY = 300  # 5 minutes cap between attempts
```

### Step 2: Add `_retry_delays()` generator

- [ ] Add after the constants:

```python
def _retry_delays(initial_delay: int = 0) -> Iterator[int]:
    """Yield retry delays infinitely: initial_delay, then escalation (skipping ≤), then MAX_RETRY_DELAY forever."""
    yield initial_delay
    for delay in RETRY_ESCALATION:
        if delay <= initial_delay:
            continue
        yield delay
    while True:
        yield MAX_RETRY_DELAY
```

Sequences:
- Startup (`initial_delay=0`): 0, 5, 15, 30, 60, 120, 300, 300, ...
- Runtime (`initial_delay=10`): 10, 15, 30, 60, 120, 300, 300, ...

### Step 3: Add `_collect_restart_data()`

- [ ] New private function — queries active crons, loads session, validates cwd. Called inside the retry loop on each attempt (fresh data):

```python
def _collect_restart_data(session_id: str) -> dict | None:
    """Collect restart data for a single session (synchronous, runs in thread).

    Returns a dict with keys matching send_to_session() kwargs (minus text)
    plus crons_data for message building. Returns None if restart not possible
    (no active crons, session not found, or cwd missing).
    """
    from twicc.core.models import Session, SessionCron

    active_crons = list(SessionCron.active_for_session(session_id))
    if not active_crons:
        return None

    try:
        session = Session.objects.get(id=session_id)
    except Session.DoesNotExist:
        logger.warning("Cron restart for session %s: session not found in DB", session_id)
        return None

    cwd = session.cwd
    if not cwd or not os.path.isdir(cwd):
        logger.warning("Cron restart for session %s: cwd '%s' does not exist on disk", session_id, cwd)
        return None

    return {
        "session_id": session_id,
        "project_id": session.project_id,
        "cwd": cwd,
        "crons_data": [
            {"cron_expr": c.cron_expr, "recurring": c.recurring, "prompt": c.prompt}
            for c in active_crons
        ],
        "permission_mode": session.permission_mode or "default",
        "selected_model": session.selected_model,
        "effort": session.effort,
        "thinking_enabled": session.thinking_enabled,
        "claude_in_chrome": session.claude_in_chrome,
        "context_max": session.context_max,
    }
```

### Step 4: Rewrite `restart_session_crons()` — self-contained with infinite retry

- [ ] Replace the entire function. It now takes only `session_id` + control params, collects its own data on each attempt:

```python
async def restart_session_crons(
    session_id: str,
    *,
    stop_event: asyncio.Event,
    initial_delay: int = 0,
) -> None:
    """Restart cron jobs for a single session with infinite retry.

    On each attempt: collects fresh data from DB, sends restart message to Claude,
    waits for the first USER_TURN to confirm success. Retries indefinitely with
    capped exponential backoff until success, cancellation (stop_event), or all
    crons have expired (nothing left to restart).

    Used identically by startup (restart_all_session_crons) and runtime
    (_restart_crons_for_session in ProcessManager).
    """
    from twicc.agent.manager import get_process_manager
    from twicc.agent.states import ProcessState

    manager = get_process_manager()
    delays = _retry_delays(initial_delay)
    attempt = 0

    while True:
        delay = next(delays)
        attempt += 1

        if delay > 0:
            logger.info(
                "Cron restart for session %s: attempt %d in %ds",
                session_id, attempt, delay,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                logger.info(
                    "Cron restart for session %s: cancelled during delay (attempt %d)",
                    session_id, attempt,
                )
                return
            except asyncio.TimeoutError:
                pass  # Normal: delay elapsed, time to retry

        # Collect fresh data on each attempt (crons may expire, settings may change)
        restart_data = await asyncio.to_thread(_collect_restart_data, session_id)
        if restart_data is None:
            logger.info(
                "Cron restart for session %s: no restart data available, stopping (attempt %d)",
                session_id, attempt,
            )
            return

        crons_data = restart_data.pop("crons_data")
        message = _build_restart_message(crons_data)

        try:
            await manager.send_to_session(**restart_data, text=message)

            process = manager._processes.get(session_id)
            if process is None:
                logger.warning(
                    "Cron restart for session %s: process not found after send_to_session (attempt %d)",
                    session_id, attempt,
                )
                continue

            if process.state == ProcessState.DEAD:
                logger.warning(
                    "Cron restart for session %s: process died immediately (attempt %d)",
                    session_id, attempt,
                )
                continue

            # Wait for first USER_TURN (success) or DEAD (failure)
            try:
                await asyncio.wait_for(
                    process._first_turn_done_event.wait(),
                    timeout=300,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Cron restart for session %s: timeout waiting for USER_TURN (attempt %d)",
                    session_id, attempt,
                )
                await manager.kill_process(session_id, reason="cron_restart_timeout")
                continue

            if process._first_user_turn_reached:
                logger.info("Successfully restarted crons for session %s (attempt %d)", session_id, attempt)
                return
            else:
                logger.warning(
                    "Cron restart for session %s: process died before USER_TURN (attempt %d)",
                    session_id, attempt,
                )
                continue

        except Exception as e:
            logger.error(
                "Cron restart for session %s: unexpected error (attempt %d): %s",
                session_id, attempt, e,
            )
            continue
```

### Step 5: Simplify `_prepare_restarts()` — cleanup only, return session_ids

- [ ] `_prepare_restarts()` no longer collects restart data — it just does ProcessRun cleanup and returns session_ids that have active crons:

```python
def _prepare_restarts() -> list[str]:
    """Synchronous DB work: cleanup orphan/stale process runs, return session IDs to restart.

    Called in asyncio.to_thread from restart_all_session_crons().
    """
    from django.db.models import Count

    from twicc.core.models import ProcessRun, Session, SessionCron

    # 1. Delete orphan process runs (no crons attached)
    orphan_count, _ = (
        ProcessRun.objects
        .annotate(cron_count=Count("crons"))
        .filter(cron_count=0)
        .delete()
    )
    if orphan_count:
        logger.info("Cleaned up %d orphan process run(s)", orphan_count)

    # 2. For sessions with multiple process runs, keep only the oldest
    runs_by_session: dict[str, list[ProcessRun]] = defaultdict(list)
    for process_run in ProcessRun.objects.order_by("started_at"):
        runs_by_session[process_run.session_id].append(process_run)

    for session_id, runs in runs_by_session.items():
        if len(runs) > 1:
            stale_pks = [r.pk for r in runs[1:]]
            deleted_count, _ = ProcessRun.objects.filter(pk__in=stale_pks).delete()
            logger.info(
                "Session %s had %d process runs, kept oldest, deleted %d newer one(s)",
                session_id, len(runs), deleted_count,
            )
            runs_by_session[session_id] = [runs[0]]

    # 3. Filter to sessions with active crons, clean up the rest
    session_ids = []
    for session_id, runs in runs_by_session.items():
        process_run = runs[0]

        if not SessionCron.active_for_session(session_id).filter(process_run=process_run).exists():
            process_run.delete()
            logger.info("Session %s: all crons expired, deleted process run %s", session_id, process_run.pk)
            continue

        # Validate session exists (clean up if JSONL was deleted)
        if not Session.objects.filter(id=session_id).exists():
            process_run.delete()
            logger.warning("Session %s: not found in DB, deleted process run %s", session_id, process_run.pk)
            continue

        session_ids.append(session_id)

    return session_ids
```

Note: cwd validation is no longer done here — `restart_session_crons()` handles it on each attempt via `_collect_restart_data()`. If cwd is missing, it returns gracefully. The ProcessRun stays in DB for the next startup.

### Step 6: Update `restart_all_session_crons()` — required `stop_event`, simplified

- [ ] Rewrite with required `stop_event` and simplified logic:

```python
async def restart_all_session_crons(stop_event: asyncio.Event) -> None:
    """Scan ProcessRun table and restart all sessions with persisted crons.

    Steps:
    1. Clean up orphan/stale process runs
    2. Launch restart_session_crons() in parallel for each session with active crons
    """
    from django.conf import settings

    if not settings.CRON_AUTO_RESTART:
        logger.info("Cron auto-restart disabled (TWICC_NO_CRON_RESTART is set)")
        return

    session_ids = await asyncio.to_thread(_prepare_restarts)

    if not session_ids:
        logger.info("No cron jobs to restart")
        return

    logger.info("Restarting cron jobs for %d session(s)", len(session_ids))

    tasks = [
        restart_session_crons(sid, stop_event=stop_event)
        for sid in session_ids
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    succeeded = 0
    cancelled = 0
    for result in results:
        if result is None:
            succeeded += 1
        elif isinstance(result, asyncio.CancelledError):
            cancelled += 1
        elif isinstance(result, BaseException):
            logger.error("Unexpected error in cron restart: %s", result)

    logger.info(
        "Cron restart complete: %d succeeded, %d cancelled (shutdown)",
        succeeded, cancelled,
    )
```

### Step 7: Remove unused import

- [ ] `defaultdict` is still used in `_prepare_restarts()`, so keep it. But the `os` import is now only used by `_collect_restart_data()` — verify it's still at the top of the file (it is, line 10).

---

## Task 2: Add runtime cron restart to `ProcessManager`

**Files:**
- Modify: `src/twicc/agent/manager.py`

### Step 1: Add `_cron_restart_tasks` dict to `__init__`

- [ ] Add after `self._stop_event` (line 91):

```python
    self._cron_restart_tasks: dict[str, asyncio.Task[None]] = {}  # session_id -> restart task
```

### Step 2: Add `_cancel_cron_restart_task()` helper

- [ ]

```python
def _cancel_cron_restart_task(self, session_id: str) -> bool:
    """Cancel a running cron restart task for a session.

    Returns True if a task was cancelled, False if none existed.
    """
    task = self._cron_restart_tasks.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()
        logger.info("Cancelled cron restart task for session %s", session_id)
        return True
    return False
```

### Step 3: Add `_restart_crons_for_session()` — thin wrapper

- [ ] Just calls the shared function with error handling and task cleanup:

```python
async def _restart_crons_for_session(self, session_id: str) -> None:
    """Launch infinite cron restart loop for a session that died at runtime.

    Thin wrapper around restart_session_crons() that handles task lifecycle
    (CancelledError, cleanup from _cron_restart_tasks dict).
    """
    from twicc.cron_restart import restart_session_crons

    RUNTIME_INITIAL_DELAY = 10  # seconds before first attempt (let API recover)

    try:
        await restart_session_crons(
            session_id,
            stop_event=self._stop_event,
            initial_delay=RUNTIME_INITIAL_DELAY,
        )
    except asyncio.CancelledError:
        logger.info("Cron restart task cancelled for session %s", session_id)
        raise
    except Exception as e:
        logger.error("Cron restart task failed for session %s: %s", session_id, e, exc_info=True)
    finally:
        self._cron_restart_tasks.pop(session_id, None)
```

### Step 4: Trigger restart from `_on_state_change()`

- [ ] Inside the `if process.process_run is not None:` block (line 942), after the `if should_delete_run:` deletion logic (~line 967), **before** the dead-process removal from `_processes` (line 970):

```python
                # --- Auto-restart crons for non-manual, non-shutdown deaths ---
                # should_delete_run is False ⟹ process had active crons and died
                # after USER_TURN. Launch a background restart task.
                if (
                    not should_delete_run
                    and process.kill_reason not in ("manual", "shutdown")
                    and settings.CRON_AUTO_RESTART
                    and process.session_id not in self._cron_restart_tasks
                    and self._stop_event is not None
                    and not self._stop_event.is_set()
                ):
                    task = asyncio.create_task(
                        self._restart_crons_for_session(process.session_id),
                        name=f"cron-restart-{process.session_id}",
                    )
                    self._cron_restart_tasks[process.session_id] = task
                    logger.info(
                        "Launched runtime cron restart task for session %s (kill_reason=%s)",
                        process.session_id, process.kill_reason,
                    )
```

Guards explained:
- `not should_delete_run`: process had crons (ProcessRun preserved)
- `kill_reason` check: not manual stop, not TwiCC shutdown
- `CRON_AUTO_RESTART`: feature flag (`TWICC_NO_CRON_RESTART` env var)
- `not in _cron_restart_tasks`: no restart loop already running (prevents double-launch when a retry-spawned process dies — the existing loop handles it)
- `stop_event` check: `_stop_event` exists (always true after first process start) AND not set (TwiCC isn't shutting down)

### Step 5: Cancel cron restart task when user sends a message

- [ ] In `send_to_session()`, at the beginning of `async with self._lock:` (after line 145):

```python
            # Cancel any running cron restart task — user is taking over this session
            self._cancel_cron_restart_task(session_id)
```

### Step 6: Cancel all cron restart tasks during `shutdown()`

- [ ] In `shutdown()`, before `async with self._lock:` (around line 446):

```python
        # Cancel all cron restart tasks — TwiCC is shutting down.
        # ProcessRuns stay in DB for restart_all_session_crons() on next startup.
        if self._cron_restart_tasks:
            logger.info("Cancelling %d cron restart task(s)", len(self._cron_restart_tasks))
            for session_id in list(self._cron_restart_tasks):
                self._cancel_cron_restart_task(session_id)
```

---

## Task 3: Wire up `stop_event` from `run.py`

**Files:**
- Modify: `src/twicc/cli/run.py`

- [ ] In `orchestrator_task()` (around line 208-209):

```python
# Before:
deferred["cron_restart_task"] = asyncio.create_task(restart_all_session_crons())

# After:
deferred["cron_restart_task"] = asyncio.create_task(
    restart_all_session_crons(stop_event=shutdown_event)
)
```

---

## Edge Cases & Attention Points

### 1. No double-restart on death during restart

When the retry loop's `send_to_session()` spawns a process that immediately dies, `_on_state_change()` fires again. The `not in self._cron_restart_tasks` guard prevents launching a second restart task — the existing loop handles the retry.

### 2. ProcessRun lifecycle during restart loop

New process → new ProcessRun. On success (first `USER_TURN`): old ProcessRuns purged by `_old_runs_purged`. On failure (dies before `USER_TURN`): new ProcessRun deleted (`should_delete_run = True`), original preserved for next retry. Both paths already work correctly.

### 3. cwd missing — graceful stop, ProcessRun preserved

If `_collect_restart_data()` returns `None` because cwd is missing, the retry loop stops. The ProcessRun stays in DB (nobody deletes it). This is intentional: the cwd might come back (e.g., external drive remounted). On next TwiCC startup, `_prepare_restarts()` will include this session again, and `restart_session_crons()` will re-check cwd. The ProcessRun self-cleans when its crons expire (max 3 days for recurring).

### 4. Fresh data on each attempt

`_collect_restart_data()` is called on every retry attempt. Benefits:
- Picks up session setting changes (model, permission mode)
- Detects expired crons (stops retrying if none left)
- Detects cwd appearing/disappearing

### 5. User intervenes during restart loop

`send_to_session()` cancels the restart task → `CancelledError` → cleanup. User proceeds normally. If session later dies with crons, a new restart loop begins.

### 6. TwiCC shutdown

Two mechanisms: `stop_event.set()` returns gracefully during sleep delays; `task.cancel()` handles other awaits. ProcessRuns stay in DB for next startup.

### 7. No error-type filtering

All non-manual deaths trigger restart (no distinction between `server_error`, `rate_limit`, `authentication_failed`, etc.). Crons are too important to lose.

### 8. Backoff timing

| Attempt | Startup (initial=0) | Runtime (initial=10) |
|---|---|---|
| 1 | 0s (immediate) | 10s |
| 2 | 5s | 15s |
| 3 | 15s | 30s |
| 4 | 30s | 60s |
| 5 | 60s | 120s |
| 6 | 120s | 300s (cap) |
| 7+ | 300s (forever) | 300s (forever) |

Max 5 minutes between attempts. Retries forever until success or cancellation.
