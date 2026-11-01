# Scoped Artifact Bookmarks in Session Tabs — Design

**Status:** Implemented
**Date:** 2026-07-15

## 1. Goal

Keep a session's Artifacts tab available whenever either:

- the session has its own artifact files; or
- at least one artifact bookmark is visible from the session's project context.

The tab remains the place for browsing the session's artifact directory under a
physical root named **Session artifacts**, while its navigator gains a virtual
root named **Bookmarked artifacts**. Selecting a bookmark previews it inside the
current session without navigating away. The existing "open in session" action
remains the way to jump to its source session.

The same navigator is used in both responsive layouts:

- wide panel: persistent navigator beside the preview;
- narrow panel: full-panel navigator toggled by the selected artifact title.

## 2. Scope semantics

Bookmark scope is a visibility tier, not a stored workspace id. Session tabs
and the dedicated Artifacts view resolve visibility from the same real project
context.

Let `P` be the session's raw project and `M` its main repository when `P` is a
worktree (otherwise `M = P`). A bookmark is visible when any of the following
is true:

1. **Local context:** in a main-project session, the bookmark belongs to `M`; in
   a worktree session, it belongs to either `P` or `M`, regardless of scope.
2. **Shared workspace:** the bookmark has scope `workspace` or `all`, and its
   raw owning project (including a worktree) belongs, through worktree-aware
   membership, to at least one non-archived workspace that also contains `M`.
3. **Global:** the bookmark has scope `all`, regardless of owning project.

Scope resolution and presentation are deliberately independent. Scope only
decides whether a bookmark enters the list. Once visible, the bookmark is shown
exactly once under its raw owning project; worktrees are distinct project
groups. Consequently an `all` bookmark owned by any worktree or unrelated
project remains visible everywhere without being presented in an `Everywhere`
group.

The dedicated Artifacts view applies these same rules when a project or
worktree is selected. Its flat, recency-sorted presentation remains unchanged;
only inclusion is aligned with session tabs.

## 3. Navigator model

`FilesPanel` currently supports several *alternative* filesystem roots through
`availableRoots` + `selectedRootKey`, but fetches and renders only one at a time.
That mechanism must not be reused for the bookmark root: bookmarks do not share a
physical directory and both roots must be visible together.

The artifact navigator is therefore a small forest:

```text
Session artifacts                 (filesystem root, omitted when unavailable)
  ...session files...

Bookmarked artifacts              (virtual root, omitted when empty)
  <current raw project badge>      (project or worktree)
    ...bookmark targets...
  <main project badge>             (worktree sessions only)
    ...bookmark targets...
  <shared-workspace project badge>
    ...bookmark targets...
  <another shared project badge>
    ...bookmark targets...
  <outside project badge>          (`all` bookmarks)
    ...bookmark targets...
```

The existing filesystem tree stays authoritative for local files. The virtual
root is client-side data derived from the bookmark store. It uses stable bookmark
ids for identity and the bookmark name for display. Provenance lives on each
project/worktree group header rather than being repeated beside every artifact.
Groups are ordered as current raw project, parent main project for a worktree,
other projects reachable through shared workspaces, then outside projects whose
global bookmarks are visible. Projects within the last two tiers are ordered by
display name.

The tree panel keeps one keyboard-navigation surface and one filter. File search
continues to use the backend; bookmark filtering is client-side by name,
relative path and project display name.

## 4. Typed selection

The current panel models selection as one relative file path. The artifact panel
needs two target kinds:

```js
{ kind: 'session-file', relativePath }
{ kind: 'bookmark', bookmarkId }
```

For a session file, preview inputs stay unchanged. For a bookmark, the selected
bookmark supplies:

- absolute path: `bookmark.root + '/' + bookmark.relative_path`;
- confinement root: `bookmark.root`;
- artifact owner: `bookmark.session_id`;
- bookmark metadata and actions.

The preview remains `FilePane`; no second artifact renderer is introduced.
For a bookmarked artifact, its compact action row is ordered as Artifacts list,
source session, Bookmark, then Share. The source-session action opens the
physical file under that session's **Session artifacts** root.

## 5. Routes

Keep the existing session Artifacts route shape:

```text
/session/<session-id>/artifacts/:rootKey?/:filePath?
```

- own file: `rootKey = artifacts`, `filePath = <encoded relative path>`;
- bookmark: `rootKey = bookmarks`, `filePath = <bookmark id>`.

This preserves deep links and browser history without adding another route
family. An unknown, removed or newly out-of-scope bookmark produces the existing
route-warning behavior and falls back to the navigator.

## 6. Responsive behavior

There is one navigator component and one state:

- wide: roots render persistently in the split panel;
- narrow: the same DOM is reparented into the existing overlay;
- selecting a file or bookmark closes the narrow overlay;
- expansion, search, focus and scroll survive layout changes;
- with no selection, the narrow overlay opens automatically;
- the narrow header displays the bookmark name for virtual targets and the file
  path (plus bookmark name, if any) for session files.

## 7. Live updates and lifecycle

- Bookmark create/update/delete WebSocket messages update the virtual root live.
- An artifact-route absence redirect waits for the initial bookmark snapshot, so
  a bookmarks-only deep link cannot be rejected during startup.
- Removing the active bookmark clears the virtual selection and returns to the
  navigator; it never falls through to an unrelated local path.
- Artifact file change events refresh the local tree for the owning session.
- When a selected bookmark belongs to another session, matching file-change
  events reload that preview as well.
- The Artifacts tab presence is recomputed from `session.has_artifacts` OR the
  resolved bookmark list. The tab may disappear after the last visible bookmark
  is removed only when the session has no own artifacts and the tab is not the
  active route; while active, the normal absent-tab redirect closes it safely.

## 8. Compatibility boundaries

- Files and Git retain their current single-tree behavior.
- Existing alternative filesystem-root selection remains unchanged.
- Workspace, All projects and `Show all (ignore scope)` keep their existing
  dedicated-view rules.
- No database migration or new backend endpoint is required.
- `FilePane` continues to enforce every artifact root through
  `rootRestriction`; virtual navigation never weakens path confinement.

## 9. Validation

Cover at minimum:

- project/worktree/workspace/global scope resolution and de-duplication;
- sessions with own files only, bookmarks only, both, and neither;
- bookmark selection, deep linking, deletion and out-of-scope transitions;
- narrow overlay open/close and wide/narrow state preservation;
- local and cross-session artifact file-change refresh;
- frontend production build.
