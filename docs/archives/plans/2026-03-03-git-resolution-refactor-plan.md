# Git Resolution Refactor — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate git resolution cache staleness by disabling the cache for all live/on-demand paths, keeping it only for bulk background compute.

**Architecture:** Add `use_cache` parameter to `_resolve_git_from_path` and `resolve_git_for_item`. All watcher and on-demand callers pass `use_cache=False`. Background compute keeps `use_cache=True`. Move session-level git propagation from `compute_item_metadata_live` to `sync_session_items`. Add CWD fallback for Bash-only sessions.

**Tech Stack:** Python, Django ORM

**Design doc:** `docs/plans/2026-03-03-git-resolution-refactor-design.md`

**Quality approach:** No tests, no linting (per project conventions). Verify by running dev servers and checking behavior.

---

### Task 1: Add `use_cache` parameter to `_resolve_git_from_path`

**Files:**
- Modify: `src/twicc/compute.py:204-258`

**Step 1: Add the parameter and conditional cache logic**

Change the function signature and wrap cache reads/writes in `if use_cache`:

```python
def _resolve_git_from_path(dir_path: str, *, use_cache: bool = True) -> tuple[str, str] | None:
    """
    Walk up from dir_path to find a .git entry and resolve git directory and branch.

    Args:
        dir_path: An absolute directory path to start from
        use_cache: If True (default), use the module-level cache for faster lookups.
            Set to False for live/on-demand resolution where freshness matters.

    Returns:
        (git_directory, git_branch) tuple, or None if no .git found
    """
    traversed: list[str] = []
    current = dir_path

    while True:
        # Check cache for this directory
        if use_cache and current in _git_resolution_cache:
            result = _git_resolution_cache[current]
            # Cache all traversed intermediate paths
            for path in traversed:
                _git_resolution_cache[path] = result
            return result

        traversed.append(current)

        git_path = os.path.join(current, '.git')
        try:
            if os.path.isdir(git_path):
                # Main repo: .git is a directory
                branch = _read_head_branch(os.path.join(git_path, 'HEAD'))
                result = (current, branch) if branch is not None else None
                # Cache all traversed paths
                if use_cache:
                    for path in traversed:
                        _git_resolution_cache[path] = result
                return result

            elif os.path.isfile(git_path):
                # Worktree: .git is a file containing "gitdir: /path/to/.git/worktrees/name"
                result = _resolve_worktree_git(current, git_path)
                # Cache all traversed paths
                if use_cache:
                    for path in traversed:
                        _git_resolution_cache[path] = result
                return result

        except OSError:
            # Permission error or other OS issue, skip this level
            pass

        # Move up one directory
        parent = os.path.dirname(current)
        if parent == current:
            # Reached filesystem root without finding .git
            if use_cache:
                for path in traversed:
                    _git_resolution_cache[path] = None
            return None
        current = parent
```

**Step 2: Commit**

```bash
git add src/twicc/compute.py
git commit -m "refactor: add use_cache parameter to _resolve_git_from_path"
```

---

### Task 2: Add `use_cache` parameter to `resolve_git_for_item`

**Files:**
- Modify: `src/twicc/compute.py:359-398`

**Step 1: Pass through the parameter**

```python
def resolve_git_for_item(parsed_json: dict, *, use_cache: bool = True) -> tuple[str, str] | None:
    """
    Resolve git directory and branch for a session item.

    Extracts paths from tool_use blocks, resolves each to a git root,
    and returns the most common resolution.

    Args:
        parsed_json: Parsed JSON content of the item
        use_cache: If True (default), use the module-level cache.

    Returns:
        (git_directory, git_branch) tuple, or None if no paths or no git found
    """
    paths = extract_paths_from_tool_uses(parsed_json)
    if not paths:
        return None

    resolutions: list[tuple[str, str]] = []
    for path in paths:
        # Use the directory part of the path (for files)
        dir_path = os.path.dirname(path) if not os.path.isdir(path) else path
        result = _resolve_git_from_path(dir_path, use_cache=use_cache)
        if result is not None:
            resolutions.append(result)

    if not resolutions:
        return None

    if len(resolutions) == 1:
        return resolutions[0]

    # Multiple resolutions: use the most frequent git_directory
    counter = Counter(r[0] for r in resolutions)
    most_common_dir = counter.most_common(1)[0][0]
    # Return the first resolution matching the most common directory
    for r in resolutions:
        if r[0] == most_common_dir:
            return r

    return resolutions[0]  # Fallback (shouldn't reach here)
```

**Step 2: Commit**

```bash
git add src/twicc/compute.py
git commit -m "refactor: add use_cache parameter to resolve_git_for_item"
```

---

### Task 3: Update `compute_item_metadata_live` — remove session update, use `use_cache=False`

**Files:**
- Modify: `src/twicc/compute.py:1867-1875`

**Step 1: Change `resolve_git_for_item` call and remove session update**

Replace lines 1867-1875:

```python
    # Resolve git directory/branch from tool_use paths (no cache for live resolution)
    git_resolution = resolve_git_for_item(parsed, use_cache=False)
    if git_resolution is not None:
        item.git_directory, item.git_branch = git_resolution
```

The `Session.objects.filter(id=session_id).update(...)` is removed. Session-level propagation will be handled by `sync_session_items`.

**Step 2: Commit**

```bash
git add src/twicc/compute.py
git commit -m "refactor: remove session-level git update from compute_item_metadata_live

Session git_directory/git_branch propagation is now handled by
sync_session_items, keeping compute_item_metadata_live focused on
item-level metadata only."
```

---

### Task 4: Update `sync_session_items` — add git propagation, CWD fallback, update_fields

**Files:**
- Modify: `src/twicc/sessions_watcher.py:767-785`

**Step 1: Add git propagation and CWD fallback after line 768 (after `session.model = last_model`)**

Insert after line 768:

```python
            # Update resolved git directory/branch from the latest item that has one
            # (items are processed in order, so the last one wins)
            for item, _ in reversed(items_to_create):
                if item.git_directory:
                    if item.git_directory != session.git_directory:
                        session.git_directory = item.git_directory
                        session.git_branch = item.git_branch
                    break

            # Fallback: if no item provided git info, try resolving from the session's cwd.
            # This handles sessions where the agent only uses Bash (no tool_use with file paths),
            # so resolve_git_for_item has nothing to work with.
            if not session.git_directory and session.cwd:
                cwd_git = _resolve_git_from_path(session.cwd, use_cache=False)
                if cwd_git:
                    session.git_directory, session.git_branch = cwd_git
```

**Step 2: Add `git_directory` and `git_branch` to `update_fields` in `session.save()`**

Change line 785 from:
```python
        session.save(update_fields=["last_offset", "last_line", "mtime", "user_message_count", "context_usage", "self_cost", "subagents_cost", "total_cost", "cwd", "cwd_git_branch", "model", "created_at"])
```

To:
```python
        session.save(update_fields=["last_offset", "last_line", "mtime", "user_message_count", "context_usage", "self_cost", "subagents_cost", "total_cost", "cwd", "cwd_git_branch", "git_directory", "git_branch", "model", "created_at"])
```

**Step 3: Update the import**

In `sessions_watcher.py` imports (line 18-26), add `_resolve_git_from_path` to the import from `twicc.compute`. This function is private by convention but used here as an internal cross-module call within the same package — same pattern as other `_`-prefixed imports already present in the file. If preferred, rename it to `resolve_git_from_path` (drop the underscore) since it now has external callers.

**Step 4: Commit**

```bash
git add src/twicc/sessions_watcher.py
git commit -m "feat: propagate git_directory in sync_session_items with CWD fallback

Session git_directory/git_branch are now set in sync_session_items:
- From the last item that resolved git info (via tool_use paths)
- Fallback: resolve from session.cwd for Bash-only sessions
- Both fields added to session.save update_fields

This replaces the per-item Session.objects.update() that was in
compute_item_metadata_live."
```

---

### Task 5: Update `ensure_project_git_root` — use `use_cache=False`

**Files:**
- Modify: `src/twicc/compute.py:143`

**Step 1: Pass `use_cache=False`**

Change line 143 from:
```python
    result = _resolve_git_from_path(directory)
```

To:
```python
    result = _resolve_git_from_path(directory, use_cache=False)
```

**Step 2: Commit**

```bash
git add src/twicc/compute.py
git commit -m "fix: use cache-free git resolution in ensure_project_git_root

Detects newly initialized git repos and removed repos without
requiring a server restart."
```

---

### Task 6: Add CWD fallback in background compute

**Files:**
- Modify: `src/twicc/compute.py:1449-1452`

**Step 1: Add CWD fallback before the `result_queue.put` call**

Insert after line 1452 (after the comment about costs):

```python
    # Fallback: if no item provided git info, try resolving from the session's cwd.
    # This handles sessions where the agent only uses Bash (no tool_use with file paths).
    # Uses use_cache=True since background compute benefits from caching across sessions.
    if not last_resolved_git_directory and last_cwd:
        cwd_git = _resolve_git_from_path(last_cwd)
        if cwd_git:
            last_resolved_git_directory, last_resolved_git_branch = cwd_git
```

Note: this uses `use_cache=True` (default) since background compute processes many sessions at startup and the cache is beneficial there.

**Step 2: Commit**

```bash
git add src/twicc/compute.py
git commit -m "feat: add CWD fallback for git resolution in background compute

Same logic as the watcher: if no item resolved git info, try the
session's cwd. Uses cache since background compute is bulk processing."
```

---

### Task 7: Delete dead code

**Files:**
- Modify: `src/twicc/compute.py:401-403`

**Step 1: Remove `clear_git_resolution_cache`**

Delete lines 401-403:
```python
def clear_git_resolution_cache() -> None:
    """Clear the module-level git resolution cache."""
    _git_resolution_cache.clear()
```

**Step 2: Commit**

```bash
git add src/twicc/compute.py
git commit -m "chore: remove unused clear_git_resolution_cache function"
```

---

### Task 8: Verify

**Step 1: Start dev servers**

```bash
uv run ./devctl.py restart all
```

**Step 2: Check backend logs for errors**

```bash
uv run ./devctl.py logs back --lines=30
```

**Step 3: Manual verification**

- Open the frontend, check that sessions display git branch info correctly
- Start a new Claude Code session, verify git_directory appears after first tool_use
- Check that an existing session's branch info is correct (not stale from cache)
