# Granular URL Routing

**Date:** 2026-04-19
**Status:** DRAFT

## Problem

Today, URLs encode the active tab (Files, Git, Terminal) but nothing about the state inside each tab. Browser Back/Forward therefore jumps between coarse screens but loses the user’s place:

- **Terminal:** which terminal sub-tab was active?
- **Files:** which root directory and which file were open?
- **Git:** which git root, which commit, which diff file?

That makes history, bookmarks, shared links, and reloads much less useful than they should be.

## Goal

Encode navigation-relevant state in the URL so that:

- Back/Forward replays meaningful screens
- reload restores the same place
- bookmarks and pasted URLs open the intended screen
- the frontend does not have to reconcile two competing sources of truth

## Non-Functional Requirements

### 1. URL is the only source of truth for current navigation

For any visible screen, the current location must be derivable from the route alone:

- active tab
- selected terminal
- selected file root
- selected git root
- selected commit
- selected file inside Git or Files

Component-local refs, KeepAlive state, or stores may cache data for performance, but they must not be treated as authoritative navigation state.

### 2. No mirrored navigation cache

This feature must not introduce a second navigation model such as:

- a parent-side `tabSubParams` cache
- “last known sub-state” stored separately from the URL
- background mutation of inactive tabs to keep them “in sync”

If a state matters for navigation, it belongs in the route or it is out of scope for this iteration.

### 3. KeepAlive is a performance tool, not a routing tool

KeepAlive may preserve loaded data, scroll position, or UI affordances, but it must never override an explicit route. Re-activating a cached view means “render the current URL faster”, not “restore a hidden tab state”.

### 4. One write path to the router

Navigation must be easy to reason about:

- user intent -> build target location -> `router.push`
- invalid/incomplete URL -> compute canonical fallback -> `router.replace`

Panels should emit intents. Parent route controllers should build URLs. Routing rules must live in one place, not be scattered across watchers.

### 5. Async-safe deep linking

Some params can only be validated after async data loads:

- file tree
- git roots
- git log
- commit file list
- terminal list from backend

Before that data exists, the app must preserve the incoming target route instead of prematurely replacing it with a fallback. Canonicalization happens only once the relevant data is available.

### 6. Explicit navigation beats background sync

Cross-tab behavior must be explicit:

- “View this git file in Files” should navigate directly to the target Files URL
- switching roots in Git must not silently mutate hidden Files state
- switching tabs must not depend on out-of-band synchronization

This keeps the mental model simple: the app is always showing what the URL says, and only explicit actions change the URL.

### 7. Simple user model over hidden convenience

This iteration prioritizes deterministic behavior over “smart” hidden restoration.

Consequences:

- a granular URL restores exactly the screen it encodes
- a bare tab URL resolves to that tab’s canonical default
- switching tabs from the header goes to the tab entry route, not to an invisible cached sub-state
- exact restoration across tabs is handled by browser history and deep links, not by hidden app memory

If later we want “remember last file per tab”, that must store a full route location and remain strictly secondary to the current URL.

## Current URL structure

```
/project/:projectId                                -> project stats
/project/:projectId/files                          -> project files tab
/project/:projectId/git                            -> project git tab
/project/:projectId/terminal                       -> project terminal tab
/project/:projectId/session/:sessionId             -> session chat
/project/:projectId/session/:sessionId/files       -> session files tab
/project/:projectId/session/:sessionId/git         -> session git tab
/project/:projectId/session/:sessionId/terminal    -> session terminal tab
/project/:projectId/session/:sessionId/subagent/:subagentId -> subagent tab

/projects                                          -> all-projects stats
/projects/files                                    -> all-projects files tab
/projects/git                                      -> all-projects git tab
/projects/terminal                                 -> all-projects terminal tab
/projects/:projectId/session/:sessionId            -> session (all-projects mode)
  (same sub-routes as single-project mode)
```

Query params: `?workspace=<id>` for workspace context.

## Target URL structure

The `...` prefix represents the context prefix described in [Contexts](#contexts).

### Terminal tab

```
.../terminal                    -> terminal entry route
.../terminal/:termIndex         -> specific terminal by index
```

- `:termIndex` is the numeric terminal index
- `.../terminal` is canonicalized to `.../terminal/0` once the tab resolves
- if the requested index no longer exists, fall back to the closest lower existing index, then to `0`
- fallback uses `router.replace`

### Files tab

```
.../files                       -> tab entry route
.../files/:rootKey              -> specific root, no file selected
.../files/:rootKey/:filePath    -> specific root + file selected
```

- `:rootKey` identifies the selected root
- `:filePath` is the path relative to that root, encoded as described below
- `.../files` is allowed as an entry route but is canonicalized to `.../files/:defaultRoot` once roots are known
- invalid roots fall back to the first available root via `replace`
- invalid files fall back to the selected root without a file via `replace`

### Git tab

```
.../git                                     -> tab entry route
.../git/:rootKey                            -> specific root, index, no file
.../git/:rootKey/:commitRef                 -> specific root + commit, no file
.../git/:rootKey/:commitRef/:filePath       -> specific root + commit + file
```

- `:rootKey` identifies the selected git root
- `:commitRef` is either `index` or a commit hash
- `:filePath` uses the same encoding as Files
- `.../git` is allowed as an entry route but is canonicalized to `.../git/:defaultRoot/index`
- invalid roots fall back to the first available git root
- unknown commits fall back to `index`
- invalid files fall back to root + commit without file
- all fallbacks use `router.replace`

## Canonical Screen Semantics

This section defines which URLs are stable screens versus transient entry routes.

### Terminal

- `.../terminal` is a transient entry route
- `.../terminal/0` and `.../terminal/:termIndex` are stable screens
- the app does not keep a hidden “last terminal” outside the URL

### Files

- `.../files` is a transient entry route
- `.../files/:rootKey` is a stable screen meaning “this root, no file selected”
- `.../files/:rootKey/:filePath` is a stable screen meaning “this root, this file selected”
- selecting a different root clears the file unless the target URL explicitly includes a file under that root

### Git

- `.../git` is a transient entry route
- `.../git/:rootKey` is transient and canonicalizes to `.../git/:rootKey/index`
- `.../git/:rootKey/index` is a stable screen meaning “index / uncommitted changes, no file selected”
- `.../git/:rootKey/:commitRef` is a stable screen meaning “this commit, no file selected”
- `.../git/:rootKey/:commitRef/:filePath` is a stable screen meaning “this commit, this file selected”
- selecting a different commit clears the file unless the target URL explicitly includes a file under that commit

### No automatic file selection

Loading data must not silently turn a broader route into a narrower route.

In practice:

- loading a Files root does not auto-open its first file
- loading a Git commit does not auto-open its first changed file
- loading the Git index does not auto-open a file

The URL only becomes more specific when the user explicitly chooses a more specific target or when the app is repairing an invalid URL.

### Explicitly out of scope

- branch filtering is not encoded in the URL
- tree expansion state is not encoded
- scroll position is not encoded
- diff presentation options are not encoded

## Root keys

The URL uses the existing root `key` values already produced by the frontend. Keys remain context-dependent.

### Files tab root keys

**Session-level** (`FilesPanel.vue` with session props):

| Key | Label | When present |
|---|---|---|
| `git-root` | Git root | Session has `git_directory`, or project has `git_root` and session does not |
| `session` | Working directory | Session has a `cwd` |
| `project` | Project directory | Always |

Paths that resolve to the same directory are merged into one entry.

**Project-level, single project** (`ProjectDetailPanel.vue`):

| Key | Label | When present |
|---|---|---|
| `project` | Project directory | Always |
| `git-root` | Git root | Project has a distinct `git_root` |

**Project-level, all projects** (`ProjectDetailPanel.vue`):

| Key | Label | When present |
|---|---|---|
| `home` | Home directory | Always |
| `root` | System root | When home is not `/` |

**Project-level, workspace** (`ProjectDetailPanel.vue`):

| Key | Label | When present |
|---|---|---|
| `common` | Common directory (LCA) | Always |
| `p:<projectId>` | Project directory | One per distinct project directory |

### Git tab root keys

**Session-level** (`GitPanel.vue`):

| Key | Label | When present |
|---|---|---|
| `session` | Session git root (merged or session-owned) | Session and project git roots are the same path, or session has its own git root |
| `project` | Project git root | Project git root exists and differs from session git root |

**Project-level** uses the same pattern adapted to project data.

## Path encoding

File paths cannot use `/` directly because `/` is already the route segment separator.

Encoding strategy:

1. split the real path on `/`
2. `encodeURIComponent` each segment
3. join the encoded segments with `|`

Examples:

| Real path | In URL |
|---|---|
| `src/components/Foo.vue` | `src|components|Foo.vue` |
| `README.md` | `README.md` |
| `src/what?.txt` | `src|what%3F.txt` |
| `docs/my file.md` | `docs|my%20file.md` |
| `src/foo|bar.txt` | `src|foo%7Cbar.txt` |

Helper contract:

```js
function encodePath(path) {
    return path.split('/').map(encodeURIComponent).join('|')
}

function decodePath(encoded) {
    return encoded.split('|').map(decodeURIComponent).join('/')
}
```

If decoding fails because the URL was manually edited into an invalid escape sequence, the route is treated as invalid and canonicalized via `replace`.

## Navigation behavior: push vs replace

- use `router.push` for every intentional user navigation
- use `router.replace` only for canonicalization or disappearance of the currently-addressed resource

That distinction is critical for history semantics.

Examples:

- header click on Files -> `push(.../files)`, then panel canonicalizes with `replace(.../files/:defaultRoot)`
- user selects a file -> `push(.../files/:rootKey/:filePath)`
- user selects a commit -> `push(.../git/:rootKey/:commitRef)`
- requested commit not found after git log loads -> `replace(.../git/:rootKey/index)`

## Contexts

Every granular tab route exists in the same four contexts as today:

| Context | Prefix | Example |
|---|---|---|
| Single project | `/project/:projectId/` | `/project/abc/terminal/2` |
| Single project + session | `/project/:projectId/session/:sessionId/` | `/project/abc/session/xyz/files/git-root/src|App.vue` |
| All projects | `/projects/` | `/projects/files/home/.zshrc` |
| All projects + session | `/projects/:projectId/session/:sessionId/` | `/projects/abc/session/xyz/git/session/index` |

Route naming should keep the current convention:

- `project-*`
- `session-*`
- `projects-*`
- `projects-session-*`

## Route definitions

Leaf tab routes become parameterized routes. The number of named routes does not need to grow.

Examples:

```js
{ path: 'terminal/:termIndex?', name: 'session-terminal' }
{ path: 'files/:rootKey?/:filePath?', name: 'session-files' }
{ path: 'git/:rootKey?/:commitRef?/:filePath?', name: 'session-git' }
```

Important constraint: because these params are positional, later params are only valid when all earlier params are present.

Examples:

- a file route must always have a `rootKey`
- a git file route must always have both `rootKey` and `commitRef`

## Architecture

### Route controller layer

`SessionView.vue` and `ProjectDetailPanel.vue` should own routing concerns for their respective screens:

- read `route.params`
- normalize them into a tab state object
- pass route-derived props into panels
- receive navigation intents from panels
- build target locations
- call `router.push` or `router.replace`

This keeps route parsing and URL construction in a single layer.

### Shared route codec

Introduce a small shared utility dedicated to granular routes. Its job is to centralize:

- `encodePath`
- `decodePath`
- coercion from raw `route.params` strings to normalized tab state
- helpers to build named-route locations for project/session/all-projects contexts
- canonicalization helpers for “same tab, corrected params”

The important property is not the filename; it is having one consistent contract instead of ad hoc param handling in multiple components.

### Controlled panels

Panels should be navigation-controlled components:

- route state comes in through props
- user interactions go out through emitted intents
- panels do not invent an alternate navigation state model

Allowed local state inside panels:

- loading
- fetched data
- tree expansion
- scroll
- temporary selections before data resolution
- visual filters that are explicitly out of URL scope

Disallowed as authoritative state:

- “actual selected route file” that differs from the URL
- hidden fallback cache for a tab not currently shown
- restoring a route from a component ref
- auto-promoting a route to a more specific route after data load without explicit user action

### No background cross-tab synchronization

Files and Git may still share utility code or explicit actions, but they should not silently maintain each other’s selection state behind the scenes.

Implications:

- `syncedGitDirPath` should not be part of the routing design
- if it survives temporarily for UI convenience, it must never compete with route resolution
- explicit actions such as “view file in Files” must compute and navigate to the target Files URL directly

### Bare routes are entry routes, not remembered state

Switching to a tab from the header should navigate to that tab’s entry route:

- Files -> `.../files`
- Git -> `.../git`
- Terminal -> `.../terminal`

That route is then canonicalized to the concrete default once required data is available.

Canonical defaults:

- Terminal -> `.../terminal/0`
- Files -> `.../files/:firstAvailableRoot`
- Git -> `.../git/:firstAvailableRoot/index`

This is a deliberate simplification. The app should not try to remember hidden per-tab substates in parallel with the URL.

## Components affected

### `frontend/src/router.js`

- parameterize the tab routes
- keep current route names

### `frontend/src/views/SessionView.vue`

- become the route controller for session-level granular tabs
- stop restoring tool-tab sub-state from KeepAlive-only memory
- navigate explicitly for cross-tab actions

### `frontend/src/components/ProjectDetailPanel.vue`

- same responsibility for project-level tabs
- tab buttons navigate to entry routes

### `frontend/src/components/TerminalPanel.vue`

- accept a route-driven target terminal index
- emit terminal navigation intents
- resolve invalid/deleted terminals with canonical replacement
- canonicalize bare terminal entry route to `/0`

### `frontend/src/components/FilesPanel.vue`

- accept route-driven root/file props
- apply them once roots/tree data is available
- emit root/file navigation intents
- keep root-only routes stable; do not auto-open a file

### `frontend/src/components/GitPanel.vue`

- accept route-driven root/commit/file props
- canonicalize after git log or commit file data loads
- emit root/commit/file navigation intents
- keep commit-only routes stable; do not auto-open a file

### Navigation callers

Any code that currently jumps to `files`, `git`, or `terminal` must choose one of two behaviors explicitly:

- go to the tab entry route
- go to a fully specified granular URL

No caller should depend on an implicit hidden sub-state.

## Edge cases

### Direct URL, reload, bookmark

Expected flow:

1. parse route params
2. load the minimum data needed to validate them
3. resolve to a concrete screen
4. canonicalize with `replace` if the route is incomplete or invalid

Important distinction:

- incomplete entry routes are canonicalized
- already-specific valid routes stay as they are

### Tab switching

Header tab switching does **not** restore an invisible last file, last commit, or last terminal. It goes to the selected tab’s entry route.

Fine-grained restoration is still available through:

- browser Back/Forward
- bookmarks
- direct links
- explicit in-app links that point to granular URLs

This means:

- previous detailed locations stay reachable through browser history
- the tab switch itself does not need access to child component state

### Resource disappears while visible

- killed terminal: replace to nearest valid terminal route
- missing root: replace to first valid root
- missing commit: replace to `index`
- missing file: replace to the same tab without file selection

### KeepAlive reactivation

Reactivating a cached screen must re-render the current route. It must not rewrite the route based on stale panel state.

### Subagent tabs

Subagent routing is unchanged. `subagentId` is already the navigation state for that screen.
