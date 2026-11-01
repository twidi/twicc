# Worktree Creation from the UI — Design

**Date:** 2026-06-09
**Status:** Approved design, pending implementation plan

## Goal

Let the user create a new git worktree directly from TwiCC's "New session" dropdowns.
Creating a worktree produces a new TwiCC project automatically linked to its parent
repository project (`worktree_of`), and immediately opens a draft session in it — the
same flow as "New project", plus the git plumbing.

This is provider-agnostic by design: it does not rely on any provider-specific
worktree tooling (e.g. Claude Code's `EnterWorktree`), so it works identically for
Claude Code and Codex sessions.

## Background / existing building blocks

- `Project.worktree_of` (self FK) already models the worktree→main-repo link.
- `register_project()` (`src/twicc/projects.py`) already calls
  `ensure_worktree_link()` after workspace auto-add: registering a project whose
  directory is a git worktree links it to its parent automatically. This feature
  does not rely on that detection, though: since the creation endpoint knows the
  parent project, it passes the link explicitly (new optional `worktree_of_id`
  parameter on `register_project`) and the detection remains a fallback for all
  other registration flows.
- `src/twicc/git.py` centralizes all git subprocess calls (with `_GIT_TIMEOUT`);
  `get_branches()` already returns local branches (current branch first).
- The "New session" dropdowns live in `frontend/src/views/ProjectView.vue`
  (split-button in single-project mode, plain dropdown in all-projects mode), with
  `frontend/src/components/project/WorktreePickerRows.vue` rendering per-project
  worktree sublists. Selection is handled by `handleNewSessionSelect`, which already
  uses `e.preventDefault()` for the `worktrees-toggle:<id>` pseudo-values.
- "New project" opens `ProjectEditDialog` in create mode; on `@saved`,
  `handleProjectCreated(project)` calls `handleNewSession(project.id)` (trust gate →
  draft session → navigation).
- `DirectoryPickerPopup.vue` (free text input + browse popup) is the established
  directory-selection widget, used by `ProjectEditDialog`.
- `git_root` is already serialized on projects (`core/serializers.py`) and available
  in the frontend store.

## Backend

### 1. `src/twicc/git.py` — new functions

- `create_worktree(repo_root, path, branch, start_point=None) -> tuple[bool, str]`
  - "Existing local branch" is decided by membership in `get_branches(repo_root)`
    (local branch names only — a name like `origin/foo` is therefore treated as a
    new-branch name, never resolved as a remote ref).
  - If `branch` is an existing local branch: `git -C <repo_root> worktree add <path> <branch>`;
    `start_point` is ignored on this path (the caller is expected to pass `None`,
    see the endpoint below).
  - Otherwise: `git -C <repo_root> worktree add <path> -b <branch> [<start_point>]`
    (no `start_point` → branches off the repo's current HEAD, git's default).
  - Returns `(True, "")` on success, `(False, <git stderr>)` on failure. Git's own
    error messages are relayed verbatim (branch already checked out elsewhere,
    target path exists and is not empty, invalid branch name, …) — git remains the
    authority on git-level validation.
  - Uses the same subprocess conventions as the rest of the module
    (`capture_output`, `text`, `_GIT_TIMEOUT`).
- `get_worktree_branches(repo_root) -> set[str]`
  - Parses `git -C <repo_root> worktree list --porcelain` and returns the set of
    branch names currently checked out in any worktree (including the main one),
    from the `branch refs/heads/<name>` lines. Detached-HEAD worktrees emit a
    `detached` line instead of a `branch` line and are skipped.
  - Used by the branches endpoint so the UI can flag unavailable branches.

### 2. `src/twicc/views.py` — two new endpoints

- `GET /api/projects/{project_id}/branches/`
  - 404 if the project does not exist; 400 if it has no `git_root`.
  - Returns `{ "branches": [{ "name": str, "checked_out": bool }, ...] }`, built
    from `get_branches(git_root)` + `get_worktree_branches(git_root)`. Order is the
    `get_branches` order (current branch first, then alphabetical).
  - Feeds the branch autocomplete and the "start from" select in the dialog.

- `POST /api/projects/{project_id}/worktrees/`
  - Body: `{ "path": str, "branch": str, "start_from": str | null }`.
  - Validation:
    - project exists (404 otherwise) and has a `git_root` (400 otherwise);
    - `path` is non-empty and absolute (400). The target is resolved with
      `os.path.realpath`; if `path_to_project_id(resolved)` matches an existing
      project → 409 "A project already exists for this directory" (same rule and
      status as `POST /api/projects/`). Whether the path exists / is an empty
      directory is left to git — `git worktree add` already rejects a non-empty
      existing target with a clear message;
    - `branch` is non-empty (400); when `branch` is an existing local branch,
      `start_from` is ignored; otherwise `start_from`, when provided, must be an
      existing local branch (400).
  - Flow:
    1. `create_worktree(git_root, path, branch, start_from)`; on git failure →
       400 `{ "error": <git stderr> }`.
    2. Under `run_under_db_write_lock`:
       `register_project(path_to_project_id(resolved_path), directory=resolved_path, worktree_of_id=<parent project id>)`.
       The endpoint **knows** the parent (the worktree was just created from its
       row), so the link is set explicitly at row creation rather than left to
       filesystem detection: `register_project` gains an optional
       `worktree_of_id` keyword, threaded down to `register_project_db_only` /
       `_create_or_get_project` (as a `get_or_create` default). When
       `worktree_of_id` is provided, `register_project` skips
       `ensure_worktree_link` — the detection path stays untouched for every
       other flow (watcher, sync, backfill). Because the row is born linked, the
       existing `project_added` broadcast already carries `worktree_of`; no
       follow-up broadcast or re-fetch is needed.
    3. Return the serialized project (201). The usual `project_added` WS
       broadcast reaches all clients, link included.

No new model, no migration.

## Frontend

### 3. New `frontend/src/components/project/WorktreeCreateDialog.vue`

Modeled on `ProjectEditDialog` patterns (form with external submit button via
`setAttribute('form', …)`, focus via `@wa-after-show`, errors in
`wa-callout variant="danger"`, responsive `--width`).

Props: the parent project (id + git_root + name). Exposes `open()` / `close()`.
Emits `created(project)` on success.

Fields, in order:

- **Branch** (required, initial focus): free text input with autocompletion over the
  repo's local branches, fetched from `GET /api/projects/{id}/branches/` when the
  dialog opens. An existing branch is checked out into the new worktree; an unknown
  name creates the branch. Branches already checked out in a worktree are shown as
  unavailable (not selectable as autocomplete suggestions; if typed manually, the
  backend/git error is displayed).
- **Path** (required): absolute path, free text input + `DirectoryPickerPopup`,
  exactly like the directory field of project creation. Placeholder suggests the
  `<repo>/.worktrees/<branch>` convention; the field is never auto-filled.
- **Start from** (optional): `wa-select` over existing local branches, default
  "Current HEAD" (empty value). Only shown when the typed branch name does NOT match
  an existing branch (it is meaningless for an existing-branch checkout). When the
  field is hidden, the submitted payload always carries `start_from: null` — a value
  selected earlier must not leak into an existing-branch submission.

Client-side validation is minimal (required fields, absolute path); everything else
is server/git-validated and surfaced in the error callout.

On 201: call `store.addProject(project)` with the response body **before** emitting
`created` (same as `ProjectEditDialog`, idempotent with the later WS broadcast).
This is required, not an optimization: the subsequent `handleNewSession` flow reads
the project from the store (trust gate lookup, draft provider/settings resolution
through the worktree chain) and degrades silently if the project is not there yet.

### 4. `ProjectView.vue` — dropdown integration

- On every **main project row** of both "New session" dropdowns, when the project
  has a `git_root`: a compact icon button aligned to the far right of the row
  (`code-branch` + `plus` visual, tooltip "New worktree" — native `title` fallback
  if `wa-tooltip` misbehaves inside the dropdown overlay).
- Worktree sub-rows (`WorktreePickerRows.vue`) do **not** get the button: a worktree
  of a worktree is not a flow we support from the UI; new worktrees are always
  created from the main repo's row.
- Click handling: `stopPropagation` so the row's own selection (create session in
  that project) does not fire; close the dropdown manually; open
  `WorktreeCreateDialog` for that project.
- The existing "Worktrees" toggle behavior is **unchanged**: the group still appears
  only when the project has active worktrees. This feature only adds the button.
- On `created(project)`: same flow as `handleProjectCreated` — call
  `handleNewSession(project.id)` (trust gate → draft session → navigate); the
  dialog has already put the project in the store (see §3).

## Error handling

Principle: minimal client-side checks, faithful relay of git errors. Git already
produces clear messages for the tricky cases (branch checked out in another
worktree, non-empty target path, invalid branch name); the backend forwards its
stderr in the 400 payload and the dialog displays it in the danger callout.

## Out of scope (deliberate)

- Worktree deletion/pruning from the UI.
- Remote-tracking branches as start point (local branches only).
- Project name/color/workspaces fields in the dialog — the worktree project is
  created with the usual derived defaults and can be edited afterwards like any
  project.

## Testing

Per project policy: no tests, no linting. Manual verification by the user.
