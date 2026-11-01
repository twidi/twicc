# Granular URL Routing — Implementation Plan

**Goal:** make the URL authoritative for navigation inside Files, Git, and Terminal without introducing hidden parallel state.

**Spec:** `docs/superpowers/specs/2026-04-19-granular-url-routing.md`

## Design Principles

- The current screen is defined by the current route, not by KeepAlive memory, component refs, or store mirrors.
- Tab buttons navigate to tab entry routes. They do not restore an invisible last sub-state.
- Panels emit navigation intents; parent views build URLs and call the router.
- `push` is for user intent. `replace` is for canonicalization and disappeared resources.
- Cross-tab behavior is explicit navigation, not background synchronization.

## Scope

### In scope

- granular URLs for Terminal, Files, and Git
- route parsing/building helpers
- route-driven canonicalization after async data loads
- project-level and session-level routing
- cleanup of routing behavior that currently depends on hidden state

### Out of scope

- encoding branch filters, tree expansion, or scroll position
- remembering hidden per-tab sub-state outside the URL
- redesigning subagent routing

## Implementation Strategy

### Phase 1 — Shared route codec

Create one small utility for granular route state. It should centralize:

- `encodePath` / `decodePath`
- conversion from raw `route.params` to normalized tab state
- helpers to build named-route locations for each context
- helpers for canonical replacement on the current tab

This phase matters because most of the complexity in the current plan comes from spreading param logic across several views and panels.

**Expected files**

- create `frontend/src/utils/granularRoutes.js` or equivalent

**Exit criteria**

- all path encoding/decoding rules live in one place
- parent views do not hand-roll route params ad hoc

### Phase 2 — Router definitions

Update `frontend/src/router.js` so every Files, Git, and Terminal route accepts granular params:

- `terminal/:termIndex?`
- `files/:rootKey?/:filePath?`
- `git/:rootKey?/:commitRef?/:filePath?`

Keep route names unchanged.

**Exit criteria**

- every existing context exposes the granular shape
- no route name churn elsewhere in the app

### Phase 3 — Parent views become route controllers

Refactor `SessionView.vue` and `ProjectDetailPanel.vue` so they:

- derive the active tab from the route only
- parse granular params through the shared codec
- pass route-derived props down to panels
- expose a small set of navigation helpers for tab entry routes and fully specified granular routes

Important simplification:

- remove tool-tab restoration that depends on KeepAlive-only memory
- do not read child refs to reconstruct last route state
- switching to Files/Git/Terminal from the header goes to the tab entry route

**Expected files**

- `frontend/src/views/SessionView.vue`
- `frontend/src/components/ProjectDetailPanel.vue`

**Exit criteria**

- no hidden tool-tab sub-state restoration remains in parent views
- workspace query preservation still works
- route changes flow through a small number of helpers

### Phase 4 — Panels become route-driven

Refactor the three panels around the same contract:

- props carry the requested route state
- local state resolves that request once data is available
- user interactions emit navigation intents upward
- invalid routes are canonicalized with `replace`

#### Terminal

- accept a target terminal index
- canonicalize bare terminal entry route to terminal `0`
- defer canonicalization until backend terminal indices are known
- replace to the nearest valid terminal when the addressed terminal disappears

#### Files

- accept target `rootKey` and `filePath`
- apply them after roots and tree data are available
- keep root-only routes stable
- replace invalid file URLs with the same root and no file

#### Git

- accept target `rootKey`, `commitRef`, and `filePath`
- resolve commit after git log loads
- resolve file after commit data loads
- keep commit-only routes stable
- replace invalid root/commit/file combinations with the closest canonical state

**Expected files**

- `frontend/src/components/TerminalPanel.vue`
- `frontend/src/components/FilesPanel.vue`
- `frontend/src/components/GitPanel.vue`

**Exit criteria**

- visible panel state matches the URL once data is available
- panels do not own router logic directly
- no panel needs hidden sync state from another tab to resolve its route

### Phase 5 — Remove routing dependence on cross-tab sync

Today the session view carries `syncedGitDirPath` to keep Files and Git loosely aligned. That should not remain part of the routing design.

Refactor cross-tab interactions so they navigate explicitly instead:

- “view in Files” computes the target Files URL and navigates there directly
- command-palette tab navigation goes to tab entry routes unless it has an explicit granular target
- any caller that previously relied on stale `route.params` being carried forward is updated to choose either entry-route navigation or fully specified navigation

If a temporary compatibility layer is needed, it must be clearly secondary to route resolution and must not mutate inactive-tab navigation state.

**Expected files**

- `frontend/src/views/SessionView.vue`
- `frontend/src/commands/staticCommands.js`
- any explicit Git -> Files navigation callers

**Exit criteria**

- hidden background sync is no longer required for correct routing
- one user action produces one navigation entry

### Phase 6 — Verification

Run a targeted manual verification matrix.

#### Session-level scenarios

1. Open a granular Files URL, reload, and confirm same root/file is restored.
2. Open a granular Git URL, reload, and confirm same root/commit/file is restored or canonically replaced.
3. Open `/terminal/3` before terminal indices load and confirm the route is not prematurely clobbered.
4. Kill the addressed terminal and confirm the URL is replaced to a valid fallback.
5. Switch tabs with header buttons and confirm they go to entry routes, not hidden remembered sub-states.
6. Confirm that loading a valid Git commit route does not auto-open the first file.
7. Confirm that loading a valid Files root route does not auto-open the first file.

#### Project-level scenarios

1. Verify Files and Terminal granular routes in single-project mode.
2. Verify all-projects mode preserves `workspace` query behavior.
3. Verify workspace mode files roots still resolve to the expected keys.

#### History semantics

1. Intentional selections use `push`.
2. Invalid route repair uses `replace`.
3. One explicit cross-tab action creates one history entry.

## Proposed File List

| Action | File | Reason |
|---|---|---|
| Create | `frontend/src/utils/granularRoutes.js` | Centralize parsing, encoding, and route building |
| Modify | `frontend/src/router.js` | Add granular params to tab routes |
| Modify | `frontend/src/views/SessionView.vue` | Session-level route controller |
| Modify | `frontend/src/components/ProjectDetailPanel.vue` | Project-level route controller |
| Modify | `frontend/src/components/TerminalPanel.vue` | Route-driven terminal selection |
| Modify | `frontend/src/components/FilesPanel.vue` | Route-driven root/file selection |
| Modify | `frontend/src/components/GitPanel.vue` | Route-driven root/commit/file selection |
| Modify | `frontend/src/commands/staticCommands.js` | Make tab navigation explicit |

## Risks To Watch

- Premature `replace` before async data is loaded will destroy valid deep links.
- Positional optional params can silently misparse if helpers allow “later param without earlier param”.
- Keeping old cross-tab sync code around after the refactor may reintroduce hidden state coupling.
- Session KeepAlive behavior can still override the route if old restoration logic is left in place.

## Definition of Done

- A granular URL is enough to reopen the same visible screen.
- The current visible screen never depends on a shadow navigation cache.
- Parent views, not panels, own URL construction.
- Invalid URLs are repaired deterministically with `replace`.
- Header tab switching is predictable and does not rely on hidden remembered sub-state.
