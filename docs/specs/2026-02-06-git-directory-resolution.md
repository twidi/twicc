# Git Directory & Branch Resolution from Tool Use Paths

**Date:** 2026-02-06
**Status:** DRAFT

## Overview

Claude Code's JSONL session files store a `cwd` and `gitBranch` per item, but these always reflect the directory where Claude Code was **launched** (the main repo), not where the work actually happens. When working in a git worktree, the `gitBranch` field shows the main repo's branch (e.g., `main`) instead of the worktree's branch (e.g., `feature/upload`).

This feature resolves the **actual** git directory and branch by analyzing file paths in tool_use inputs (Read, Edit, Write, Grep, Glob) and walking up the filesystem to find `.git` entries.

## Problem

Given a session launched from `/home/user/project` (on branch `main`), working in a worktree at `/home/user/project-feature` (on branch `feature/upload`):

- **JSONL `cwd`**: always `/home/user/project`
- **JSONL `gitBranch`**: always `main` (or whatever the main repo's branch is)
- **Tool use paths**: `/home/user/project-feature/src/foo.py`, etc.

The tool use paths are the only reliable indicator of where work actually happens.

## Resolution Chain

For each tool_use with a file path, walk up the directory tree to find `.git`:

```
/home/user/project-feature/src/components/Foo.vue
/home/user/project-feature/src/components/        → no .git
/home/user/project-feature/src/                   → no .git
/home/user/project-feature/                       → .git found!
```

Then determine the type:

```
┌──────────────────────────────┬──────────────────────────────┐
│ .git is a DIRECTORY          │ .git is a FILE               │
│ = main repo                  │ = worktree                   │
│                              │                              │
│ git_directory = this path    │ git_directory = this path    │
│ git_branch ← .git/HEAD      │ Read .git file content:      │
│                              │ "gitdir: /path/to/.git/      │
│                              │          worktrees/name"     │
│                              │ git_branch ← gitdir/HEAD     │
└──────────────────────────────┴──────────────────────────────┘
```

Reading `HEAD` gives either:
- `ref: refs/heads/feature/upload` → branch = `feature/upload`
- A raw commit hash → detached HEAD (store the hash)

## What Gets Stored

### SessionItem: two new nullable fields

- `git_directory` (`CharField`, nullable) — The resolved git root directory
- `git_branch` (`CharField`, nullable) — The resolved branch name

Only items containing tool_use blocks with file paths get values. All other items remain `NULL`.

### Session: two new fields

- `git_directory` (`CharField`, nullable) — Last non-null `git_directory` from items
- `git_branch` (`CharField`, nullable) — Last non-null `git_branch` from items

These replace the existing `git_branch` field semantically (the old one came from JSONL and was unreliable for worktrees). The existing `git_branch` field (from JSONL) is kept as-is for backward compatibility, renamed internally to reflect it comes from JSONL.

**Naming note:** The existing `Session.git_branch` field (populated from JSONL's `gitBranch`) is renamed to `jsonl_git_branch`. The new `git_branch` field contains the resolved value. This avoids confusion between the two sources. The serializer exposes the resolved `git_branch` to the frontend, with fallback to `jsonl_git_branch` when no resolved value exists.

## Which Tools Provide Paths

| Tool | Field | Example |
|------|-------|---------|
| Read | `input.file_path` | `/home/user/project/src/foo.py` |
| Edit | `input.file_path` | `/home/user/project/src/foo.py` |
| Write | `input.file_path` | `/home/user/project/src/foo.py` |
| Grep | `input.path` | `/home/user/project/src/` |
| Glob | `input.path` | `/home/user/project/src/` |

Bash commands are explicitly **excluded** — parsing paths from shell commands is unreliable and not worth the complexity.

Only absolute paths (starting with `/`) are considered.

## When an Item Has Multiple Tool Uses

A single assistant item can contain multiple tool_use blocks, potentially targeting different directories. Resolution rule:

- Extract paths from all relevant tool_use blocks in the item
- Resolve each to its git root
- If all resolve to the same git directory → use it
- If they resolve to different git directories → use the most frequent one in the item
- If there's a tie → use the first one encountered

In practice, multiple git directories in a single item is extremely rare (0 occurrences in analyzed sessions).

## Caching Strategy

### Path-to-git-directory cache

During compute, maintain a dictionary mapping directory paths to their resolved git root:

```python
# path → (git_directory, git_branch) or None
_git_resolution_cache: dict[str, tuple[str, str] | None] = {}
```

When resolving `/home/user/project-feature/src/components/Foo.vue`:
1. Check if `/home/user/project-feature/src/components/` is in cache → yes? return cached value
2. Walk up to find `.git`, resolve git_directory and branch
3. Cache **all intermediate paths** that were traversed:
   - `/home/user/project-feature/src/components/` → `(project-feature, feature/upload)`
   - `/home/user/project-feature/src/` → `(project-feature, feature/upload)`
   - `/home/user/project-feature/` → `(project-feature, feature/upload)`

This means the filesystem walk happens at most once per unique git root per compute run.

### Cache lifetime and invalidation

The cache lives for the duration of a single `compute_session_metadata()` call. It is **not** persisted across calls.

**Important consideration:** A worktree's branch can change over time (e.g., `git checkout` inside the worktree). Within a single compute run processing one session, this is unlikely to matter. Across separate compute runs, the cache is rebuilt anyway.

For the full recompute (all sessions), the cache persists across sessions within the same process invocation, which is beneficial — the same worktree paths will be resolved once and reused.

## Compute Flow

### Background compute (`compute_session_metadata`)

For each item in the session:

1. **If `git_directory` is already set in DB** → preserve it (skip resolution). This protects historical data when worktrees have been deleted since the original compute.

2. **If `git_directory` is NULL** → attempt resolution:
   - Parse the item's JSON content
   - If it's an `assistant` type with tool_use blocks containing paths → extract paths
   - Resolve each path to a git directory using the cache
   - If resolution succeeds → set `git_directory` and `git_branch` on the item
   - If resolution fails (directory doesn't exist anymore) → leave as NULL

3. Track `last_git_directory` and `last_git_branch` (last non-null values) for the Session-level fields.

At session level:
- `Session.git_directory` = last non-null `git_directory` from items
- `Session.git_branch` = last non-null `git_branch` from items

### Live compute (`compute_item_metadata_live`)

When a new item arrives during an active session:

1. Parse the JSON content
2. If it's an assistant item with tool_use paths → resolve git_directory/git_branch
3. Store on the SessionItem
4. Update Session.git_directory and Session.git_branch if non-null

For live compute, the cache can be simpler — a module-level cache that persists across live calls, similar to `_project_directories` cache.

### The recompute protection rule

When `CURRENT_COMPUTE_VERSION` is bumped and all sessions are recomputed:

- Items that **already have** `git_directory` set → preserved as-is
- Items that have `git_directory = NULL` → resolution is attempted again

This ensures that data resolved when a worktree still existed on disk is never lost, even if the worktree is later deleted. The cost of re-attempting resolution on NULL items is negligible since most will simply fail to find a `.git` (the directory doesn't exist) and remain NULL.

## Serialization

### Session serializer

```python
def serialize_session(session):
    return {
        ...
        "git_branch": session.git_branch or session.jsonl_git_branch,
        "git_directory": session.git_directory,
        ...
    }
```

The frontend receives a single `git_branch` that is the best available value: resolved if available, JSONL fallback otherwise.

### Session items metadata endpoint

The items metadata endpoint already returns computed fields. Add `git_directory` and `git_branch` to the response for items that have them.

## Edge Cases

### Worktree deleted, old session recomputed

The item's `git_directory` was set during the original compute → preserved. No data loss.

### Worktree deleted, session never computed before

The filesystem walk fails (directory doesn't exist). `git_directory` and `git_branch` remain NULL. The session falls back to JSONL's `gitBranch`. This is the best we can do.

### "Merge back to main and delete worktree" workflow

When the user asks Claude to merge a worktree branch into main and delete the worktree, Claude typically runs Bash commands (git merge, git worktree remove) without any Read/Edit/Write. The last resolved `git_directory` and `git_branch` remain from the worktree — which is technically correct: this session's primary work was in that worktree.

### Multiple worktrees in one session

Each item gets its own `git_directory`/`git_branch`. The Session-level fields reflect the **last** worktree used. The per-item data preserves the full history.

### Files outside any git repo

Paths that don't have a `.git` ancestor (e.g., `/tmp/scratch.py`) → no resolution, fields stay NULL for that item.

### Detached HEAD

If `HEAD` contains a raw commit hash instead of a ref → store the hash as `git_branch`. The frontend can detect this (no `/` in the value) and display it differently if needed.

## Files to Modify

| File | Action | Description |
|------|--------|-------------|
| `src/twicc/core/models.py` | **Modify** | Add `git_directory`, `git_branch` to SessionItem. Rename Session's `git_branch` to `jsonl_git_branch`, add new `git_directory` and `git_branch` |
| `src/twicc/core/migrations/` | **Create** | Migration for new fields |
| `src/twicc/compute.py` | **Modify** | Add git resolution logic in `compute_session_metadata` and `compute_item_metadata_live` |
| `src/twicc/core/serializers.py` | **Modify** | Expose resolved `git_branch` with JSONL fallback |
| `src/twicc/settings.py` | **Modify** | Bump `CURRENT_COMPUTE_VERSION` |
