# Protect Session Titles from Stale CLI reAppendSessionMetadata

**Date:** 2026-04-13
**Status:** Draft
**Scope:** Backend only (no frontend changes)
**Depends on:** `2026-04-13-simplify-title-rename-via-sdk.md` (already implemented)

## Context

The Claude CLI has a `reAppendSessionMetadata()` function that periodically re-writes cached metadata (including `customTitle`) to the end of the JSONL file. Before writing, it reads the last 64 KB of the file to absorb newer values. However, when the custom-title entry falls outside the 64 KB tail window (pushed out by large amounts of assistant output), or when the function is called with `force=true` (which skips the tail scan entirely), the CLI writes back its stale cached value.

This causes a regression: the user renames a session via TwiCC, but the CLI overwrites the rename with an older title. The watcher then picks up the stale entry and applies it to the DB, undoing the user's rename.

**Evidence:** 6 confirmed regressions found in real JSONL files (e.g., "Release 1.4" overwritten by "Ajout images workspaces changelog 1.3").

## Design

### New state in `titles.py`

Add a protected titles dict alongside the existing pending titles:

```python
_protected_titles: dict[str, str] = {}
# {session_id: title} — last user-set title, protected from CLI overwrites
```

### New functions in `titles.py`

**`protect_title(session_id: str, title: str) -> None`**

Store the user-set title as protected. Called by the API PATCH handler after a rename.

**`check_protected_title(session_id: str, incoming_title: str) -> TitleCheck`**

Called by the watcher when processing a `custom-title` entry from the JSONL. Returns a `NamedTuple`:

```python
class TitleCheck(NamedTuple):
    should_apply: bool
    correction: str | None
```

Logic:
- No protection for this session → `TitleCheck(True, None)` — apply normally
- Protected and `incoming_title == protected_title` → `TitleCheck(True, None)` + clear protection (CLI absorbed the correct value)
- Protected and `incoming_title != protected_title` → `TitleCheck(False, protected_title)` — don't apply the stale title, return the correct title for re-writing

**`clear_protected_title(session_id: str) -> None`**

Remove protection for a session. Called when the process dies (no more `reAppendSessionMetadata` to worry about).

### Changes in `views.py`

After calling `rename_session_in_jsonl(session_id, title)`, also call `protect_title(session_id, title)`:

```python
# 3. Write to JSONL via SDK (atomic append, safe at any time)
try:
    rename_session_in_jsonl(session_id, title)
except Exception:
    pass  # Non-critical: DB is already updated, watcher will sync

# 4. Protect from CLI stale re-append
protect_title(session_id, title)
```

The protection is set regardless of whether the JSONL write succeeded, because the DB title is the source of truth for the user's intent.

### Changes in `sessions_watcher.py`

Replace the "Apply title updates" block (around line 922-924):

Before:
```python
for target_session_id, title in session_title_updates.items():
    Session.objects.filter(id=target_session_id).update(title=title)
    if target_session_id == session.id:
        session.title = title
```

After:
```python
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

Note: `rename_session_in_jsonl` performs blocking I/O. The watcher runs in a thread (`asyncio.to_thread` in the main watcher loop), so this is safe.

### Changes in `manager.py`

**Protect draft titles on flush.** In the existing `ASSISTANT_TURN` block that flushes pending titles, also call `protect_title()` after the successful write:

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

This ensures that draft session titles are also protected from stale CLI re-appends. At session start, the 64 KB window is unlikely to be exceeded, but `force=true` calls could still overwrite the title.

**Clear protection on process death.** In `_on_state_change`, within the existing `if state == ProcessState.DEAD:` block:

```python
if state == ProcessState.DEAD:
    from twicc.titles import clear_protected_title
    clear_protected_title(process.session_id)
    # ... existing DEAD handling ...
```

No active process → no `reAppendSessionMetadata` → no stale writes to protect against.

### Self-healing cycle

The re-write mechanism creates a self-healing loop:

```
1. User renames to "C" → TwiCC writes custom-title "C", protects "C"
2. CLI reAppendSessionMetadata writes stale "A"
3. Watcher sees "A", checks protection → "A" ≠ "C" → skip, re-write "C"
4. CLI next reAppendSessionMetadata → tail-scan finds "C" → absorbs → writes "C"
5. Watcher sees "C", checks protection → "C" == "C" → apply, clear protection
6. Stable ✓
```

The loop resolves in one cycle. If the CLI uses `force=true` (which skips the tail scan), it will keep writing stale values and the watcher will keep blocking and re-writing — but the DB title is never overwritten, which is the main goal. The JSONL accumulates redundant custom-title entries, which is harmless.

### Memory management

The `_protected_titles` dict grows by one entry per renamed session. Entries are cleaned up when:
- The CLI writes back the correct title (step 5 above) → protection cleared
- The process dies → `clear_protected_title` called from manager

In practice, only sessions with active Claude processes will have entries, so the dict stays small.

## Known Limitations

- **Server restart loses protections.** The `_protected_titles` dict is in-memory and does not survive a server restart. If TwiCC restarts while a Claude CLI process is still running, and the CLI writes a stale title after restart, the watcher will apply it. This is accepted: the window is narrow (restart + active CLI + stale write), and the user can simply rename again. Persisting protections to DB would add a migration for an edge case that is unlikely and self-correcting.
- **Renames done directly in Claude Code CLI** (not via TwiCC) are not protected. This is correct — if the user renames in the CLI, that's their intent and TwiCC should accept it.
- **The `reAppendSessionMetadata` behavior itself** is a Claude CLI feature that we cannot disable.

## Files Changed

| File | Change |
|------|--------|
| `src/twicc/titles.py` | Add `_protected_titles` dict, `protect_title()`, `check_protected_title()`, `clear_protected_title()`, `TitleCheck` NamedTuple |
| `src/twicc/views.py` | Add `protect_title()` call after rename |
| `src/twicc/sessions_watcher.py` | Replace title update block with protection check + re-write |
| `src/twicc/agent/manager.py` | Add `clear_protected_title()` on process DEAD |
