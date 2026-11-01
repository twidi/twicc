# Protect Titles from Stale CLI reAppend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the Claude CLI's `reAppendSessionMetadata()` from overwriting user-set session titles with stale cached values.

**Architecture:** Add a protected titles dict in `titles.py` that tracks user-set titles. The watcher checks incoming `custom-title` entries against this protection — blocking stale values and re-writing the correct title to the JSONL so the CLI absorbs it on its next tail-scan.

**Tech Stack:** Python, claude_agent_sdk, Django

**Spec:** `docs/superpowers/specs/2026-04-13-protect-titles-from-stale-reappend.md`

---

### Task 1: Add protection functions to `titles.py`

**Files:**
- Modify: `src/twicc/titles.py`

- [ ] **Step 1: Add the TitleCheck NamedTuple and protected titles dict**

Add after the `_pending_titles` dict (line 17):

```python
from typing import NamedTuple


class TitleCheck(NamedTuple):
    """Result of checking a title against protection."""
    should_apply: bool
    correction: str | None


# Titles protected from CLI stale re-appends.
# Set when the user renames via the API; cleared when the CLI absorbs the
# correct value or when the process dies.
_protected_titles: dict[str, str] = {}
```

- [ ] **Step 2: Add the three protection functions**

Add at the end of the file:

```python
def protect_title(session_id: str, title: str) -> None:
    """Mark a title as protected from CLI stale re-appends."""
    _protected_titles[session_id] = title
    logger.debug("Protected title for session %s: %s", session_id, title[:50])


def check_protected_title(session_id: str, incoming_title: str) -> TitleCheck:
    """Check an incoming custom-title from JSONL against protection.

    Returns:
        TitleCheck with should_apply=True if the title should be applied to DB,
        or should_apply=False with correction set to the correct title to re-write.
    """
    protected = _protected_titles.get(session_id)
    if protected is None:
        return TitleCheck(True, None)

    if incoming_title == protected:
        # CLI absorbed the correct value — clear protection
        del _protected_titles[session_id]
        logger.debug("Protection cleared for session %s (CLI absorbed correct title)", session_id)
        return TitleCheck(True, None)

    # Stale title from CLI — block and return correction
    logger.info(
        "Blocked stale title for session %s: %r -> keeping %r",
        session_id, incoming_title, protected,
    )
    return TitleCheck(False, protected)


def clear_protected_title(session_id: str) -> None:
    """Remove title protection for a session (e.g. when the process dies)."""
    if _protected_titles.pop(session_id, None):
        logger.debug("Cleared title protection for session %s (process died)", session_id)
```

- [ ] **Step 3: Verify imports**

Run: `cd /home/twidi/dev/twicc-poc && uv run python -c "from twicc.titles import TitleCheck, protect_title, check_protected_title, clear_protected_title; print('OK')"`

Expected: `OK`

---

### Task 2: Add `protect_title` call in `views.py`

**Files:**
- Modify: `src/twicc/views.py:374-398`

- [ ] **Step 1: Add protect_title after the JSONL write**

Change the import on line 376 and add the protection call after the try/except block:

```python
        # Handle title update
        if "title" in data:
            from twicc.titles import protect_title, rename_session_in_jsonl, validate_title

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
            try:
                rename_session_in_jsonl(session_id, title)
            except Exception:
                pass  # Non-critical: DB is already updated, watcher will sync

            # 4. Protect from CLI stale re-append
            protect_title(session_id, title)
```

---

### Task 3: Add protection check in the watcher

**Files:**
- Modify: `src/twicc/sessions_watcher.py:922-927`

- [ ] **Step 1: Replace the title update block**

Replace lines 922-927:

```python
    # Apply title updates
    for target_session_id, title in session_title_updates.items():
        Session.objects.filter(id=target_session_id).update(title=title)
        # If updating the current session, update the object too
        if target_session_id == session.id:
            session.title = title
```

With:

```python
    # Apply title updates (with protection against CLI stale re-appends)
    from twicc.titles import check_protected_title, rename_session_in_jsonl

    for target_session_id, title in session_title_updates.items():
        result = check_protected_title(target_session_id, title)
        if result.should_apply:
            Session.objects.filter(id=target_session_id).update(title=title)
            if target_session_id == session.id:
                session.title = title
        elif result.correction:
            # CLI wrote a stale title — re-write the correct one.
            # This places the correct title at the end of the JSONL,
            # so the CLI's next tail-scan will absorb it.
            try:
                rename_session_in_jsonl(target_session_id, result.correction)
            except Exception:
                pass  # Will retry on next stale entry
```

---

### Task 4: Add protection + cleanup in `manager.py`

**Files:**
- Modify: `src/twicc/agent/manager.py:1143-1172`

- [ ] **Step 1: Add `protect_title` to the ASSISTANT_TURN pending flush**

Replace lines 1146-1155:

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

With:

```python
        if state == ProcessState.ASSISTANT_TURN:
            from twicc.titles import get_pending_title, pop_pending_title, protect_title, rename_session_in_jsonl

            pending = get_pending_title(process.session_id)
            if pending:
                try:
                    await asyncio.to_thread(rename_session_in_jsonl, process.session_id, pending)
                    pop_pending_title(process.session_id)
                    protect_title(process.session_id, pending)
                except Exception as e:
                    logger.error("Error flushing pending title for session %s: %s", process.session_id, e)
```

- [ ] **Step 2: Add `clear_protected_title` in the DEAD block**

Add at the start of the `if state == ProcessState.DEAD:` block (line 1158), before the existing try block:

```python
        if state == ProcessState.DEAD:
            from twicc.titles import clear_protected_title
            clear_protected_title(process.session_id)

            try:
                from django.utils import timezone as dj_timezone
                ...
```

---

### Task 5: Verify and commit

- [ ] **Step 1: Verify all imports**

Run: `cd /home/twidi/dev/twicc-poc && uv run python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings'); django.setup(); from twicc.views import session_detail; from twicc.agent.manager import ProcessManager; from twicc.sessions_watcher import sync_session_items; print('OK')"`

Expected: `OK`

- [ ] **Step 2: Verify no stale references**

Run: `cd /home/twidi/dev/twicc-poc && rg 'check_protected_title|protect_title|clear_protected_title|TitleCheck' src/twicc/ --type py`

Expected: references in `titles.py`, `views.py`, `sessions_watcher.py`, `manager.py` — no orphans.

- [ ] **Step 3: Commit**

```bash
git add src/twicc/titles.py src/twicc/views.py src/twicc/sessions_watcher.py src/twicc/agent/manager.py docs/superpowers/specs/2026-04-13-protect-titles-from-stale-reappend.md docs/superpowers/plans/2026-04-13-protect-titles-from-stale-reappend.md
git commit -m "fix: protect session titles from CLI stale reAppendSessionMetadata

The Claude CLI periodically re-writes cached metadata to the JSONL file.
When the user's custom-title entry falls outside the 64KB tail-scan window,
the CLI overwrites it with a stale cached value.

Add a protected titles mechanism: user-set titles are tracked in memory
and checked by the watcher. Stale entries are blocked and the correct
title is re-written to the JSONL, allowing the CLI to absorb it on its
next tail-scan."
```
