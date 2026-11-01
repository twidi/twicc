# Worktree parent roots — design

## Context / Problem

TwiCC projects expose a set of "root" directories used by the Files tab, the
Git tab, backend path validation, and the chat's file-path resolution (clickable
markdown links + the "View in Files" button on tool cards). For a normal project
these roots derive from four sources: `session.git_directory`, `session.cwd`,
`project.directory`, `project.git_root`.

When a project is a git worktree (`Project.worktree_of` is set), `project.git_root`
resolves to the *worktree's* own root, not the main repository's. The main repo
checkout (the parent project's `directory` / `git_root`) is therefore unreachable
from the worktree session: you cannot browse it in the Files tab, the backend
rejects its paths, clickable links pointing into it render as broken, and the
"View in Files" button is hidden for tools that touched it.

## Goal

When the current project is a worktree, expose the parent (main repo) project's
directories as additional roots everywhere roots are used — for both session
views and the worktree project's own (session-less) view.

Scope decisions (agreed with the user):

- **Git tab**: the main repo's git root is added as a selectable root.
- **Where**: applies to session views AND the worktree project view.
- **Direction**: one-way — a worktree session gains the parent's roots; a
  main-repo session does NOT gain its worktrees' roots.
- **Depth**: one level only (worktree → main repo; the main repo is not itself a
  worktree).
- **What**: only the parent *project*'s `directory` and `git_root` are added,
  not the parent's sessions' `cwd` / `git_directory`.

## Approach: centralize

The root list is currently re-derived independently in ~7 frontend spots and ~3
backend spots, with drift (e.g. `FilePickerPopup` uses keys `git`/`cwd` where
`FilesPanel` uses `git-root`/`session`). We introduce **one canonical derivation
per side** and migrate every site to it. This both fixes the duplication and
guarantees the parent roots appear consistently, with no site missed.

## Backend

New module `src/twicc/roots.py` (pure; operates on already-fetched model
instances):

- `allowed_base_dirs(project, session=None, parent=None) -> list[str]` — ordered,
  normalized, de-duplicated base directories: `project.git_root` (else
  `project.directory`), then `session.cwd` + `session.git_directory`, then
  `parent.git_root` + `parent.directory`.
- `git_roots_for(project, session=None, parent=None) -> list[str]` — ordered git
  roots: `session.git_directory`, `project.git_root`, `parent.git_root`.

The caller fetches the parent via `project.worktree_of_id` (the fetch is sync in
`validate_path`, async in `_resolve_session_git_directory`, so it cannot live
inside the shared helper).

Consumers:

- `file_tree.validate_path` → uses `allowed_base_dirs`. One change covers
  `directory_tree`, `file_search`, `file_content` GET/PUT, and every file
  mutation endpoint (rename / delete / move / create).
- `views._resolve_session_git_directory` → uses `git_roots_for` to validate a
  requested `?git_dir=` and to pick the default. Worktree-first default is
  preserved (session → project, with the parent git root only as a last resort);
  the parent git root becomes a valid explicitly-requested root. One change
  covers every git endpoint (log, index, commit detail / files, diffs, stage /
  unstage / discard) at both project and session level.

`validate_standalone_root` (workspace / all-projects browsing via `?root=`) is
unrelated to worktrees and is left unchanged.

No model change → **no migration** (`worktree_of` already exists).

## Frontend

New module `frontend/src/utils/projectRoots.js` (pure; takes the store as an
argument, imports no store / router → no import cycle):

- `getWorktreeParent(project, store)` — the parent `Project` or `null`. The
  single source of truth for "who is my parent".
- `deriveFileRoots({ gitDirectory, cwd, projectDirectory, projectGitRoot, parentDirectory, parentGitRoot })`
  — ordered `{ key, label, path, roles }` descriptors. Reproduces the existing
  `FilesPanel` ordering / labels / same-path merge for the four base roots, then
  appends the parent git root (key `parent-git`) and parent directory (key
  `parent-dir`), merged when equal ("Main repo directory (git root)").
- `deriveGitRoots({ gitDirectory, projectGitRoot, parentGitRoot })` — ordered
  `{ key, label, path }`: the worktree's session / project git roots, then `parent`
  ("Main repo git root").
- `fileRootsFromStore(project, session, store)` / `gitRootsFromStore(project, session, store)`
  — convenience wrappers that resolve the parent and call the pure functions, so
  each call site is one line and the session→input mapping lives in one place.

Existing keys (`git-root`, `session`, `project`) are preserved so persisted
Files / Git routes keep resolving; new keys `parent-git` / `parent-dir` (Files)
and `parent` (Git) are added.

Migrated sites:

| Site | Change |
|---|---|
| `FilesPanel.availableRoots` | session / single mode → `deriveFileRoots` + internal `getWorktreeParent(projectId)` |
| `FilePickerPopup.availableRoots` | → `deriveFileRoots` (also unifies its divergent keys) |
| `GitPanel.availableGitRoots` | → `deriveGitRoots` + internal parent lookup |
| `ProjectDetailPanel.filesAvailableRoots` (single-project branch) | → `deriveFileRoots` (the project view passes `externalRoots`, bypassing `FilesPanel`'s own lookup, so the parent must be added here) |
| `fileLinks.classifyHref` | accepts the ordered descriptor array; matches absolute paths against it, anchors relative paths to the `cwd`-role root |
| `SessionItemsList` (`markdownFileLinks` provide) | passes `fileRootsFromStore(project, session, store)` |
| `SessionView.viewFileInFilesTab` | maps the absolute path to a descriptor `key` via `fileRootsFromStore` (drops the hardcoded key mapping; parent keys now resolve) |
| `ToolUseContent.canViewInFilesTab` | root set via `fileRootsFromStore(parentSessionProject, mainSession, store)` |
| `ApplyPatchContent.fileTabRoots` | → `fileRootsFromStore(...).map(r => r.path)` |

`ApplyPatchFileEntry` consumes `fileTabRoots` as a prop (no change). Display-only
helpers (`providers/utils/path.formatRelativePath`, `GitPanel`'s commit-path
relativization) use a single base dir and are out of scope.

## Edge cases

- Parent not in the store / empty `directory` → skipped silently (no crash).
- Parent dir missing on disk → existing 404 + `missingRoots` disables that root
  (Files) / 404 fallthrough (Git).
- Nested worktree (worktree dir inside the main repo): worktree-first ordering
  keeps shared files opening in the worktree context; the parent root still lets
  you reach the rest of the main repo.

## Testing

Per project policy (no tests): manual verification in a worktree session and in
the worktree project view — the Files root dropdown lists the Main repo roots;
browse a main-repo-only file; click a markdown link pointing into the main repo;
"View in Files" on a tool that touched a main-repo file; the Git tab switches to
the main repo root.
