# Context-Preserving Session Links — Design

Status: implemented (2026-06-28).

Several UI affordances that "take you to a session" rewrite the URL strictly —
they replace the path's project with the *target session's* project, and they do
not carry the active workspace. This yanks the user out of their current visual
frame (sidebar project filter, `project` vs `projects` mode, workspace). This
design unifies all such "go to a session" navigations onto a single
context-preserving route builder so that **only the session id (and its suffix:
subagent / tab) changes** while the current frame (prefix mode + current
`projectId` + `workspace`) is preserved.

## 1. The problem

The common defect is **not** the `?workspace=` query (the router's `beforeEach`
workspace guard re-propagates it when the target project belongs to the active
workspace — `router.js:137-160`). The defect is the **`projectId` / prefix**:
the "strict" links write the target session's own project into the path, which:

- in single-project mode, **switches the sidebar project filter** to another
  project (`SessionToastContent.vue:130-131` even documents this as intentional);
- for the orchestration link, hardcodes single-project mode, **breaking the
  `projects-*` prefix** when in all-projects mode (`OrchestrationNode.vue:61`).

Key consequence used throughout this design: **if we keep the current
`projectId`**, the target project is the one the user is already on (hence a
member of the active workspace), so the workspace guard re-adds the workspace
automatically. And if we additionally carry `route.query.workspace`
**explicitly**, the workspace is preserved **unconditionally** — even toward a
project outside the workspace (the guard's `to.query.workspace !== undefined`
branch returns early). So "keep the current `projectId` + carry workspace
explicitly" solves both the prefix/filter problem and the workspace problem.

## 2. Desired behavior (the principle)

For any "go to this session" affordance, derive the frame from the **current
route** and replace only the session id (plus the suffix):

- Current mode `projects-*` → stay in `projects-…`; the path's `projectId`
  carries the session's real project (the "All projects / workspace" sidebar
  shows it). 
- Current single-project mode → keep `route.params.projectId` (the current
  filter); the session renders cross-filter if it lives elsewhere
  (`SessionView` loads by session id; the path `projectId` is the sidebar
  filter, not the session's real project — see the `ensureSessionResolved`
  comment at `SessionView.vue:315-317`, vs the data-driven `projectId` computed
  at `SessionView.vue:352`).
- `workspace` carried explicitly from `route.query.workspace` when present.

### 2.1 Condition A — trigger is the *destination* being a session route

The URL adaptation applies whenever the **destination is a session route**
(`session` / `projects-session` and their sub-routes: `subagent`, `files`,
`git`, `terminal`, `orchestration`, `plan`, `tasks`). The **current** route may
be anything: coming from a project root (`/project/A`) or a Files tab and
navigating to a session must still keep project A. In practice the helper reads
only `route.name` (mode), `route.params.projectId` (current project, may be
absent), and `route.query.workspace`, so this holds automatically for any
current route. We simply route every session-targeting link through the helper.

### 2.2 Condition B — no URL rewriting on history navigation

All context-preservation logic lives **in the click handlers** (computing the
`router.push` target at click time). We add **no `beforeEach` redirect**.
Browser Back/Forward (popstate) must replay the stored URL verbatim; a guard
that rewrote destinations would corrupt history navigation. The existing
workspace guard is left untouched.

## 3. The unified helper

Generalize the existing single source of truth `sessionRouteLocation`
(`utils/sessionRoute.js`), already used by the sidebar link
(`SessionListItem.vue:306`) and the session switcher
(`useSessionSwitcher.js:187`):

```js
// utils/sessionRoute.js
// target: { id, project_id }
// options: { subagentId } (extensible later; only subagent is needed now)
export function sessionRouteLocation(target, route, options = {}) {
    const isAllProjects = route.name?.startsWith('projects-')
    const projectId = isAllProjects
        ? target.project_id
        : (route.params.projectId || target.project_id) // fallback for non-project routes (e.g. home)
    const name = options.subagentId
        ? buildSubagentRouteName(isAllProjects)
        : buildSessionBaseRouteName(isAllProjects)
    const params = { projectId, sessionId: target.id }
    if (options.subagentId) params.subagentId = options.subagentId
    const location = { name, params }
    if (route.query.workspace) location.query = { workspace: route.query.workspace }
    return location
}
```

- Route names come from `granularRoutes.js` (`buildSessionBaseRouteName`,
  `buildSubagentRouteName`) — no hardcoded route-name strings.
- Backward compatible: the existing `(session, route)` calls pass a
  `{ id, project_id }` object, identical to the new `target` shape.
- The `route.params.projectId || target.project_id` fallback covers the edge of
  a link fired from a route with no project (e.g. home).

## 4. Call-site audit and migration

| Site | File | Action | Behavior change |
|---|---|---|---|
| Sidebar link (`href`) | `SessionListItem.vue:306` | keep helper (new arg shape) | none |
| Session switcher commit | `useSessionSwitcher.js:187` | keep helper (MRU exact path preserved) | none |
| Sidebar select (canonical) | `ProjectView.vue handleSessionSelect` (956-982, non-deselect branches) | → helper | none — `projectId`/`activeWorkspaceId`/`isAllProjectsMode` are defined as `route.params.projectId` / `route.query.workspace` / `route.name` (lines 400/414/404), so equivalent; removes divergence risk vs the `href` |
| Command palette jump | `staticCommands.js navigate()` (252-263) | → helper | none (de-dupes inline copy) |
| **Toast "Go to session"** | `SessionToastContent.vue goToSession` (119-140) | → helper | single mode keeps the current project (no sidebar switch); workspace carried |
| **"View Agent"** | `ToolUseContent.vue navigateToSubagent` (837-847) | → helper `{ subagentId }` | keeps current frame; **fixes workspace drop** (see §5) |
| **Orchestration node** | `OrchestrationNode.vue:60-63` (`router-link :to`) | → helper | respects all-projects mode; keeps current frame + workspace |
| Search — no filter | `SearchOverlay.vue navigateToResult` (385-399) | → helper | none (already this behavior) |
| Search — workspace/project filter | `SearchOverlay.vue navigateToResult` (374-384) | **keep as-is** | none — the in-overlay filter is explicit user intent and wins (decision §7) |

**Left unchanged (rationale):**

- Intra-session tab navigation — `SessionView.vue navigateInTab`/`switchToTab`
  (489-503, 629-657): same session, already context-preserving by construction
  (uses `filterProjectId` + `route.query`).
- `focusChat.js gotoChatFooterPanel` (34-35): same session; the workspace guard
  re-adds workspace for the same (member) project. No change needed.
- Session creation — `ProjectView.vue handleNewSession`, command-palette
  "New Session" / "New Session in…": creation is a different concern than "go to
  an existing session"; already carries `route.query`.
- Deselection / mode-switch handlers (`handleSessionSelect` deselect branch,
  project/workspace selector): not session jumps.

## 5. Reported bug fixed as a consequence

Repro (user-confirmed): in workspace W with *show active across projects* on, an
active session of a project **P outside W** appears cross-filter; the user opens
it (`/projects/P/session/S?workspace=W`) and clicks **View Agent**.
`navigateToSubagent` pushes without a `query`; the guard sees
`to.query.workspace === undefined`, tests `workspaceContainsProject(W, P)` →
false → **workspace dropped**, landing in bare all-projects mode.

Fix: routing View Agent through the helper carries `route.query.workspace`
**explicitly** (W), so the guard's `to.query.workspace !== undefined` branch
returns early and **W is preserved** even though P is outside W — matching how
`handleSessionSelect` already keeps a cross-filter session on its workspace.

## 6. Edge cases

- Link fired from a route with no project (home) → `route.params.projectId`
  absent → fallback to `target.project_id`.
- Cross-project / cross-workspace target → renders fine; the path `projectId`
  only drives the sidebar filter; explicit workspace keeps the sidebar frame.
- `route.query.workspace === ''` (explicit-clear sentinel) → falsy → treated as
  "no workspace" (consistent with the existing `if (route.query.workspace)`
  contract and the `afterEach` cleanup in `router.js:165-172`).

## 7. Decisions (resolved 2026-06-28)

1. **Scope** — full audit + unification on one helper (not just the two named
   links).
2. **Search** — keep the in-overlay filter behavior: a workspace/project filter
   set in the search overlay is explicit user intent and wins; only the
   *no-filter* case routes through the helper (which is already its behavior).
3. **Condition A** — the trigger is the *destination* being a session route; the
   current frame is preserved even when navigating from a non-session route.
4. **Condition B** — logic stays in click handlers; no `beforeEach` redirect;
   history navigation replays stored URLs verbatim.

## 8. Alternatives considered and rejected

- **Aggressive unification** (force search's filter cases through the helper):
  rejected — it would ignore the workspace/project filter the user explicitly
  set in the search overlay (a regression).
- **Per-call-site inline fixes, no shared helper**: rejected — perpetuates the
  ~5 already-diverging inline copies of the mode/projectId/workspace logic; new
  sites would re-break.
- **A `beforeEach` redirect implementing context preservation globally**:
  rejected — violates Condition B (would rewrite URLs on Back/Forward).

## 9. File map

- `frontend/src/utils/sessionRoute.js` — generalize the helper (the SSoT).
- `frontend/src/utils/granularRoutes.js` — reuse `buildSessionBaseRouteName`,
  `buildSubagentRouteName` (no new code expected).
- `frontend/src/components/session/SessionToastContent.vue` — `goToSession`.
- `frontend/src/components/session/detail/items/ToolUseContent.vue` —
  `navigateToSubagent`.
- `frontend/src/components/orchestration/OrchestrationNode.vue` — `router-link`.
- `frontend/src/views/ProjectView.vue` — `handleSessionSelect`.
- `frontend/src/commands/staticCommands.js` — `navigate()`.
- `frontend/src/components/app/SearchOverlay.vue` — `navigateToResult` (no-filter
  branch only).
- Unchanged but verified: `components/session/list/SessionListItem.vue`,
  `useSessionSwitcher.js`, `SessionView.vue`, `focusChat.js`, `router.js`.

## 11. Implementation notes (from spec review)

- `OrchestrationNode.vue` does **not** currently import/use `useRoute` — the
  helper needs the current route, so migrating its `:to` requires adding
  `useRoute()`. (`SessionToastContent.vue` already has `route` at line 49;
  `ToolUseContent.vue` at lines 20-21.)
- `staticCommands.js navigate()` (252-263) reads local destructured vars
  (`allProjects`, `currentProjectId`, `activeWorkspaceId`) rather than a `route`
  object. `buildSessionNavItems` already receives `route`, so the helper call is
  feasible by passing `route` and re-deriving from it instead of threading the
  locals.

## 10. Testing notes (manual)

- Single-project A, toast for a session in B → stays on `/project/A/...`, B
  renders cross-filter (sidebar stays on A).
- All-projects + workspace W, cross-filter session in P (outside W), View Agent →
  workspace W preserved (the §5 repro).
- Orchestration node clicked in all-projects mode → stays in `projects-…`.
- Middle-click vs left-click on a sidebar row → identical URL (href = handler).
- Search with workspace/project filter → unchanged (filter wins).
- Browser Back/Forward across the above → URLs replay verbatim (Condition B).
