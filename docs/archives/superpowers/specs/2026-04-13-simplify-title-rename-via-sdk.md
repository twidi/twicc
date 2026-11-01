# Simplify Session Title Rename via SDK

**Date:** 2026-04-13
**Status:** Draft
**Scope:** Backend only (no frontend changes)

## Context

TwiCC writes `custom-title` entries to session JSONL files when users rename sessions. The current implementation in `titles.py` has extensive safeguards:

- Buffered Python I/O (`open("a")`) with post-write verification (tail scan up to 2 MB)
- Retry logic (3 attempts with increasing delays)
- Pending title system that defers writes when a Claude process is in `STARTING` or `ASSISTANT_TURN` state
- Flush mechanism triggered by process state transitions (`USER_TURN` / `DEAD`) with additional 0.5s delay and re-verification

This complexity was designed to handle concurrent writes to the JSONL file shared with the Claude CLI subprocess.

## Why This Is Unnecessary

Investigation of the Claude Agent SDK revealed that:

1. **`rename_session()`** in `claude_agent_sdk` does the same thing we do — appends a `{"type":"custom-title",...}` entry to the JSONL file — but using `os.open(O_WRONLY | O_APPEND)` + `os.write()` (kernel-level atomic append).

2. **POSIX `O_APPEND` guarantees** that each `write()` syscall atomically seeks to EOF before writing. On Linux and common filesystems (ext4, btrfs), small writes (well under a filesystem block, typically 4 KB) are performed as a single atomic data copy by the kernel. A custom-title entry is ~300 bytes — atomic in practice.

3. **The CLI writes via `appendFileSync` / `appendFile`** (Node.js), which also uses `O_APPEND`. Both writers use atomic appends — there is no corruption risk for entries of this size.

4. **Waiting for `USER_TURN` is unnecessary** — the SDK's own `rename_session()` has no state checks and is documented as safe to call while a CLI process is actively writing.

## Design

### New function in `titles.py`

Replace the complex write/verify/retry machinery with a thin wrapper around the SDK:

```python
from claude_agent_sdk import rename_session

def rename_session_in_jsonl(session_id: str, title: str) -> None:
    """Write a custom-title entry to the session's JSONL file via the SDK.

    Uses the SDK's rename_session() which performs a kernel-level atomic
    append (O_APPEND + os.write). Safe to call at any time, even while
    a Claude CLI process is actively writing to the file.

    Silently logs and returns on failure (file not found, etc.) —
    the DB title is already updated by the caller, and the watcher
    will eventually sync if the entry is missing.
    """
    try:
        rename_session(session_id, title)
    except Exception as e:
        logger.warning("rename_session failed for %s: %s", session_id, e)
```

No directory parameter needed — `rename_session()` scans `~/.claude/projects/` and finds the file by session ID.

Note: The SDK's `rename_session()` also strips whitespace and validates non-empty, and produces compact JSON (no spaces in separators). This is fine — the watcher parses JSON, not raw strings.

Note: `rename_session()` performs blocking file I/O. The `views.py` PATCH handler is synchronous Django, so no issue there. For the async pending-title flush in `manager.py`, the call must be wrapped in `asyncio.to_thread()`.

### Functions to remove from `titles.py`

| Function | Reason |
|----------|--------|
| `write_custom_title_to_jsonl()` | Replaced by `rename_session_in_jsonl()` |
| `_verify_title_in_jsonl()` | No longer needed (atomic writes don't need verification) |
| `flush_pending_title()` | No longer needed (no deferred writes for existing sessions) |
| `get_session_jsonl_path()` | No longer needed (SDK handles file lookup) |
| `_VERIFY_MAX_READ_BYTES` | No longer needed |

### Functions to keep in `titles.py`

| Function | Reason |
|----------|--------|
| `validate_title()` | Used by the API view for input validation |
| `MAX_TITLE_LENGTH` | Shared constant with frontend |
| `set_pending_title()` | Needed for draft→real session (JSONL doesn't exist yet) |
| `get_pending_title()` | Needed by serializer to bridge title before watcher syncs |
| `pop_pending_title()` | Needed by manager to clear pending after successful flush |

Note: `set_pending_title()` is simplified — it no longer needs to handle renames of existing sessions. It is only used for draft sessions where the JSONL file does not exist yet.

### Changes in `views.py` (PATCH session endpoint)

Before:
```python
from twicc.titles import set_pending_title, validate_title, write_custom_title_to_jsonl
from twicc.agent.manager import get_process_manager
from twicc.agent.states import ProcessState

manager = get_process_manager()
process_info = manager.get_process_info(session_id)

if process_info and process_info.state in (ProcessState.STARTING, ProcessState.ASSISTANT_TURN):
    set_pending_title(session_id, title)
else:
    write_custom_title_to_jsonl(session_id, title)
```

After:
```python
from twicc.titles import validate_title, rename_session_in_jsonl

rename_session_in_jsonl(session_id, title)
```

No process state check. No branching. No manager import.

### Changes in `manager.py` (`_on_state_change`)

**Remove** the flush-pending-title block (lines 1143-1154):
```python
# DELETE THIS BLOCK:
if state in (ProcessState.USER_TURN, ProcessState.DEAD):
    from twicc.titles import flush_pending_title
    try:
        await asyncio.sleep(0.5)
        await asyncio.to_thread(flush_pending_title, process.session_id)
    except Exception as e:
        logger.error("Error flushing pending title: %s", e)
```

**Add** pending title flush on first `ASSISTANT_TURN` transition. This is the right moment because:
- `ASSISTANT_TURN` means the CLI subprocess is running and has created the JSONL file
- It happens in `ProcessManager._on_state_change()`, which has access to `session_id` and can import the title functions

Implementation:
```python
if state == ProcessState.ASSISTANT_TURN:
    from twicc.titles import get_pending_title, pop_pending_title, rename_session_in_jsonl

    pending = get_pending_title(process.session_id)
    if pending:
        try:
            await asyncio.to_thread(rename_session_in_jsonl, process.session_id, pending)
            pop_pending_title(process.session_id)
        except Exception as e:
            logger.error("Error flushing pending title for session %s: %s", process.session_id, e)
```

This will fire on every `ASSISTANT_TURN` transition, but `get_pending_title()` returns `None` after the first successful flush, so subsequent calls are no-ops.

### Changes in `asgi.py`

**No change.** The `set_pending_title(session_id, title)` call for new session creation stays as-is.

### Changes in `serializers.py`

**No change.** The `get_pending_title(session.id) or session.title` stays as-is — still needed to bridge the gap between draft creation and watcher sync.

### Cleanup in `titles.py`

After removing the functions listed above, also clean up:
- The `_pending_titles` dict stays (used by `set/get/pop_pending_title`)
- Remove `import json` (no longer building JSON manually)
- Remove `import time` (no more sleeps)
- Remove `from pathlib import Path` (no more file path manipulation)
- Remove `from django.conf import settings` (no more `CLAUDE_PROJECTS_DIR`)
- Update module docstring to reflect the simplified role

## What This Does NOT Address

The Claude CLI's `reAppendSessionMetadata()` function periodically re-writes cached metadata (including `customTitle`) to the JSONL file. When the CLI's tail scan (last 64 KB) catches our SDK-written entry, it absorbs the new value — this is the designed behavior documented in the SDK. However, when the entry falls outside the 64 KB tail window (due to large amounts of data written between our entry and the scan), or when `reAppendSessionMetadata` is called with `force=true` (which skips the tail scan entirely), the CLI writes back its stale cached value and our rename is overwritten.

This edge case will be addressed in a follow-up change (protected titles in the watcher).

## Files Changed

| File | Change |
|------|--------|
| `src/twicc/titles.py` | Major simplification: remove 4 functions + 1 constant, add 1 thin wrapper |
| `src/twicc/views.py` | Simplify PATCH handler: remove process state check |
| `src/twicc/agent/manager.py` | Remove flush block from `_on_state_change`, add pending flush on `ASSISTANT_TURN` |
