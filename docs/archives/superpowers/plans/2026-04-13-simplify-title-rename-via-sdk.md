# Simplify Title Rename via SDK — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the complex custom-title write system (retry, verify, deferred flush) with a single call to the Claude Agent SDK's `rename_session()`.

**Architecture:** The SDK provides `rename_session()` which appends a `custom-title` entry to the JSONL using kernel-level atomic I/O (`O_APPEND` + `os.write`). This makes our verification, retry, and deferred-write machinery unnecessary. The pending title system is kept only for the draft→real session case (JSONL doesn't exist yet).

**Tech Stack:** Python, claude_agent_sdk, Django

**Spec:** `docs/superpowers/specs/2026-04-13-simplify-title-rename-via-sdk.md`

---

### Task 1: Rewrite `titles.py`

**Files:**
- Modify: `src/twicc/titles.py` (full rewrite — keep 5 functions, remove 5, add 1)

- [ ] **Step 1: Replace the file content**

The new `titles.py` should contain only:

```python
"""
Session title management.

Provides title validation, a pending-title store for draft sessions
(where the JSONL file doesn't exist yet), and a thin wrapper around
the Claude Agent SDK's rename_session() for writing custom-title
entries to JSONL files.
"""

import logging

from claude_agent_sdk import rename_session

logger = logging.getLogger(__name__)

# Global dict for pending titles (draft sessions only)
_pending_titles: dict[str, str] = {}

# Max title length (matches frontend validation)
MAX_TITLE_LENGTH = 200


def validate_title(title: str | None) -> tuple[str | None, str | None]:
    """Validate and normalize a session title.

    Returns:
        A tuple of (normalized_title, error_message).
        - If valid: (trimmed_title, None)
        - If invalid: (None, error_message)
    """
    if title is None:
        return None, "Title cannot be empty"

    title = title.strip()
    if not title:
        return None, "Title cannot be empty"

    if len(title) > MAX_TITLE_LENGTH:
        return None, f"Title must be {MAX_TITLE_LENGTH} characters or less"

    return title, None


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


def set_pending_title(session_id: str, title: str) -> None:
    """Store a title for a draft session (JSONL doesn't exist yet)."""
    _pending_titles[session_id] = title
    logger.debug("Set pending title for session %s: %s", session_id, title[:50])


def get_pending_title(session_id: str) -> str | None:
    """Get a pending title for a session without removing it."""
    return _pending_titles.get(session_id)


def pop_pending_title(session_id: str) -> str | None:
    """Get and remove a pending title for a session."""
    return _pending_titles.pop(session_id, None)
```

- [ ] **Step 2: Verify the module imports correctly**

Run: `cd /home/twidi/dev/twicc-poc && uv run python -c "from twicc.titles import validate_title, rename_session_in_jsonl, set_pending_title, get_pending_title, pop_pending_title, MAX_TITLE_LENGTH; print('OK')"`

Expected: `OK`

---

### Task 2: Simplify `views.py` PATCH handler

**Files:**
- Modify: `src/twicc/views.py:374-405`

- [ ] **Step 1: Replace the title update block**

Replace lines 374-405 (the `if "title" in data:` block) with:

```python
        # Handle title update
        if "title" in data:
            from twicc.titles import rename_session_in_jsonl, validate_title

            title, error = validate_title(data["title"])
            if error:
                return JsonResponse({"error": error}, status=400)

            # 1. Update DB immediately
            session.title = title
            session.save(update_fields=["title"])

            # 2. Re-index for full-text search (title is a searchable document)
            if search.is_initialized():
                try:
                    search.reindex_session(session_id)
                except Exception:
                    pass  # Non-critical: search will catch up on next startup

            # 3. Write to JSONL via SDK (atomic append, safe at any time)
            rename_session_in_jsonl(session_id, title)
```

This removes: the `get_process_manager` import, the `ProcessState` import, the process state check, and the `set_pending_title` import.

---

### Task 3: Update `manager.py` — replace flush block

**Files:**
- Modify: `src/twicc/agent/manager.py:1143-1154`

- [ ] **Step 1: Replace the flush-pending-title block**

Replace lines 1143-1154:

```python
        # Flush pending title when process becomes safe to write.
        # We add a small delay to let Claude CLI finish flushing its own I/O
        # buffers to the JSONL file — the ResultMessage arrives via the SDK stream
        # before Claude CLI has necessarily finished writing to disk.
        if state in (ProcessState.USER_TURN, ProcessState.DEAD):
            from twicc.titles import flush_pending_title

            try:
                await asyncio.sleep(0.5)
                await asyncio.to_thread(flush_pending_title, process.session_id)
            except Exception as e:
                logger.error("Error flushing pending title: %s", e)
```

With:

```python
        # Flush pending title for draft→real sessions.
        # On ASSISTANT_TURN, the CLI has created the JSONL file, so we can
        # now write the title that was stored when the draft was sent.
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

---

### Task 4: Commit

- [ ] **Step 1: Commit all changes**

```bash
git add src/twicc/titles.py src/twicc/views.py src/twicc/agent/manager.py
git commit -m "refactor: replace custom title write system with SDK rename_session()

Replace the complex write/verify/retry/deferred-flush machinery in
titles.py with a single call to claude_agent_sdk.rename_session(),
which uses kernel-level atomic I/O (O_APPEND + os.write).

- Remove write_custom_title_to_jsonl, _verify_title_in_jsonl,
  flush_pending_title, get_session_jsonl_path, _VERIFY_MAX_READ_BYTES
- Add rename_session_in_jsonl: thin wrapper around SDK
- Simplify views.py PATCH: no more process state check
- Simplify manager.py: flush pending on ASSISTANT_TURN (draft only)"
```
