# Git State Validation After Bash Commands — Design

## Status: Implemented

## Problem

When Bash commands modify git state (branch switch, worktree deletion, merge), `session.git_directory` and `session.git_branch` remain stale. The resolution system only updates these fields when Read/Edit/Write/Grep/Glob tool_use items provide file paths. Bash commands are invisible to it.

We cannot extract paths from Bash commands because:
- `cat /etc/hosts` would resolve to no git repo and erase a valid `git_directory`
- `git checkout main` has no exploitable file path
- `ls ~/other-project/` would resolve to the wrong repo

## Approach: Validate rather than detect

Instead of parsing Bash commands, **verify that current git info is still correct** at the end of each batch in `sync_session_items`. This is non-invasive: the resolution mechanism stays the same, we just add a post-batch validation step.

Cost: one `os.path.isdir()` + one small file read per batch. Negligible.

## Design

### Step 1: Validate git_directory existence

After the existing git propagation (reversed loop + CWD fallback), if `session.git_directory` is set, check it still exists on disk. If it doesn't, re-resolve through a fallback chain:

```
session.git_directory exists on disk?
  YES → keep it, go to step 2
  NO  → session.cwd exists on disk?
      YES → re-resolve git from cwd (resolve_git_from_path(cwd))
      NO  → project.git_root exists on disk?
          YES → use it directly as git_directory (already a resolved git root)
          NO  → project.directory exists on disk?
              YES → re-resolve git from project.directory
              NO  → git_directory = None (everything is gone)
```

"Re-resolve" means calling `resolve_git_from_path(path, use_cache=False)` which walks up the filesystem to find the nearest `.git`.

`project.git_root` is used directly (not re-resolved) because it is already a resolved git root path.

### Step 2: Refresh branch from HEAD

Regardless of whether `git_directory` changed or not, re-read the current branch from the git HEAD file. This catches `git checkout` done via Bash within the same repo (same directory, different branch).

Use `read_head_branch()` (compute.py) to read the `.git/HEAD` file and extract the branch name.

If the branch differs from `session.git_branch`, update it.

### Placement in the code

In `sync_session_items`, after the existing block:
1. Reversed loop (propagate git from items)
2. CWD fallback (for Bash-only sessions with no git_directory yet)
3. **NEW: Validation step** (verify git_directory exists + refresh branch)

Then `session.save(update_fields=[..., "git_directory", "git_branch"])` persists everything.

### What this solves

| Scenario | Before | After |
|----------|--------|-------|
| `git checkout main` via Bash | Branch stays stale | HEAD re-read, branch updated |
| Worktree deleted via Bash | git_directory points to deleted path | Detected, re-resolved from cwd/project |
| `cat /etc/hosts` via Bash | No impact | No impact (git_directory still exists, branch unchanged) |
| `cd ~/other-project` + work there | git_directory stays on old repo | If old repo still exists: no change (debatable). If deleted: re-resolved |

### What this does NOT solve

- Session `cwd` changes to another repo while the original `git_directory` still exists on disk. The validation would see the old directory is still valid and keep it. This is an extremely rare edge case (working in two repos in the same session while both exist).

### Dependencies

- `read_head_branch` (compute.py): reads `.git/HEAD` and returns branch name or commit hash. Renamed from `_read_head_branch` to public.
- `resolve_git_from_path` (compute.py): with `use_cache=False` support.
- `get_project_git_root()` (compute.py): returns cached git_root for a project.
- `get_project_directory()` (compute.py): returns cached directory for a project. Created for this feature (same pattern as `get_project_git_root`).

### Implementation note: Step 2 scope

When step 1 re-resolves `git_directory` through the fallback chain (calling `resolve_git_from_path`), the branch is already refreshed as part of the resolution. Step 2 (explicit HEAD re-read) is only needed when `git_directory` has NOT changed — i.e., `git checkout` within the same repo. The implementation skips the HEAD re-read when step 1 re-resolves.
