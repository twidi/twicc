# Decouple SessionView from the URL projectId

**Date:** 2026-04-22
**Status:** DRAFT

## Context

The URL of a session today is `/project/<projectId>/session/<sessionId>` (single-project mode) or `/projects/<projectId>/session/<sessionId>` (all-projects mode). `SessionView.vue` captures `route.params.projectId` at creation time as a frozen `ref` and uses that single value for two unrelated roles:

1. **Session ownership**: as "the project this session belongs to" — used for REST calls (`/api/projects/<projectId>/sessions/<sessionId>/...`), code-comments lookups, the `project_id` field of the `send_message` WebSocket payload, and the captured `pid` used by auto-rename.
2. **Sidebar filter preservation**: as "the project currently selected in the sidebar" — used by the `router.push` calls that rebuild the current URL when switching tabs (main / subagent / files / git / terminal).

Today the two roles collapse onto the same value because every session reachable from the sidebar lives in the current project filter. That coincidence is about to break. Upcoming work (a richer pin system, and potentially other cross-filter navigation cases like "all active sessions") needs the sidebar to keep showing the current filter while the session view shows a session that lives in a different project.

This design covers only the decoupling refactor. It does not introduce the pin enum or any new filter; those live in follow-up designs.

## Goal

Separate the two roles so that, inside `SessionView` and its subtree:

- Every piece of code that identifies the session's project uses the session's real project (`session.project_id`, data-driven).
- Every piece of code that reconstructs a URL to preserve the current sidebar filter uses the URL's projectId.

After this change, a URL `/project/A/session/sessionX` where `sessionX.project_id === B` must render correctly: sidebar stays on project A, the session view operates on project B.

## Non-Goals

- **No backend changes.** Models, endpoints, serializers, and WebSocket message shapes are untouched.
- **No router changes.** Route names, paths, and required params stay as they are. The `:projectId` segment keeps its name; only its semantic changes inside `SessionView`.
- **No new filter or pin semantics.** Pin remains a boolean in this iteration.
- **No new visual indicator** for cross-filter sessions in the sidebar. The pin enum iteration will provide the proper visual signal.
- **No change to the WebSocket reconciliation priority hint.** `useWebSocket.js` keeps passing `route.params.projectId` to `useReconciliation`. If a future cross-filter session requires prioritizing its project too, that will be a follow-up.

## Design

### Two refs in `SessionView.vue`

```js
// Session ownership — drives API calls, code-comments lookups, WS payloads
const projectId = computed(() => session.value?.project_id)

// Sidebar filter — drives router.push calls that rebuild the current URL
const filterProjectId = ref(route.params.projectId)
```

`sessionId` remains a frozen `ref` captured at creation (unchanged). `session` remains a computed that reads `store.getSession(sessionId.value)` (unchanged).

The `projectId` computed is naturally stable per KeepAlive instance because `sessionId` is frozen and `session.project_id` is immutable for a given session.

The `filterProjectId` is a frozen `ref` captured at creation time, same pattern as the current `projectId` ref. It reflects the URL at the moment the view was first created and does not follow subsequent route changes (which belong to other KeepAlive'd instances).

The two refs typically carry the same value (classic case: user clicked a session listed in the current filter). They diverge only when the user reaches a session through a URL that does not match the session's project (direct bookmark, future pin cross-filter click).

### Prop propagation to children

| Component | `project-id` before | `project-id` after |
|---|---|---|
| `SessionItemsList` (main tab) | frozen URL ref | `session?.project_id` |
| `SessionContent` (subagent tabs) | frozen URL ref | `session?.project_id` |
| `FilesPanel`, `GitPanel`, `TerminalPanel` | already `session?.project_id` | unchanged |

Downstream chains are unaffected (`SessionItemsList → SessionItem → Message → ContentList → ToolUseContent`, and `SessionItemsList → MessageInput → FilePickerPopup / SlashCommandPickerPopup / MessageHistoryPickerPopup / MessageSnippetsDialog`). Each child continues to forward its received `projectId` prop; only the value at the root of the tree changes.

### API calls, code-comments, WS payloads

Every site currently reading `projectId.value` inside `SessionView` is re-targeted to the session's project:

- `filesCommentsCount`, `gitCommentsCount`, `chatCommentsCount`, `agentCommentsCount` → `projectId.value` (now data-driven)
- `handleNeedsTitle` — `const pid = projectId.value` — captured value remains data-driven
- Command palette actions `session.archive`, `session.unarchive`, `session.pin`, `session.unpin` — use `projectId.value`

The `delete-draft` command palette action is an exception that belongs to the *navigation* role, not the ownership role: after deleting the draft, it pushes `{ name: 'project', params: { projectId: filterProjectId.value } }` to return to the sidebar's current project. The split is consistent: anything that operates on the session's data uses `projectId` (data), anything that rebuilds a URL uses `filterProjectId` (URL).

`MessageInput.vue` receives `projectId` via prop (from `SessionItemsList`, which now forwards `session?.project_id`). The `send_message` WS payload thus carries the session's real project, including for drafts (a draft has its project set at creation via `createDraftSession(projectId)`, so `session.project_id` is populated from the outset).

### Navigation that preserves the sidebar filter

Every `router.push` / `router.replace` inside `SessionView` that rebuilds the URL for the current session uses `filterProjectId.value`:

- Git-tab redirect watcher
- `navigateInTab()` helper (all tab switches)
- `switchToTab()` (to `main`, to a subagent)
- `closeTab()` (to the previous subagent, to `main`)
- The `delete-draft` command palette action also uses `filterProjectId.value` for the fallback to `{ name: 'project', params }` (see rationale above)

Rationale: these navigations must not change the sidebar filter. If the user is at `/project/A/session/sessionB-cross` and switches to the git tab, the URL becomes `/project/A/session/sessionB-cross/git/...`, never `/project/B/...`.

### `SessionList.vue`: insert cross-filter session at the right sort position

The existing fallback at lines 86-91 keeps the current `sessionId` visible in the list even when it falls outside the pagination window, by appending it to the filtered array. Post-refactor it must also handle sessions that don't match the sidebar filter (cross-filter bookmarks; future pin-cross-filter clicks). Appending at the end breaks the list ordering (active process → pinned → mtime).

Changes:

1. Export `sessionSortComparator` from `frontend/src/stores/data.js`. It is currently a module-level **factory**: `sessionSortComparator(processStates)` returns the actual `(a, b) => ...` comparator, closing over the `processStates` map so it can sort active-process sessions to the top. The export must expose the factory; callers are responsible for supplying `processStates`.

2. Move the fallback from `allSessions` to `baseSessions`, and re-apply the comparator. `SessionList.vue` obtains `processStates` from the data store (`store.processStates`):

   ```js
   const baseSessions = computed(() => {
       const sessions = /* workspace / all-projects / single-project branch, unchanged */
       if (props.sessionId && !sessions.some(s => s.id === props.sessionId)) {
           const s = store.sessions[props.sessionId]
           if (s && !s.parent_session_id) {
               return [...sessions, s].sort(sessionSortComparator(store.processStates))
           }
       }
       return sessions
   })
   ```

3. The `allSessions` computed keeps its `s.id === props.sessionId` exception in the archived filter (so an archived selected session does not vanish when "show archived" is off). The previous append-at-the-end branch at lines 86-91 is removed.

Side effect: sessions older than the pagination window (the original use case of the fallback) also land at the correct sorted position instead of being appended.

### Routes

Unchanged. The `:projectId` path segment keeps its name in all route definitions. The semantic shift is internal to `SessionView`:

- `/project/:projectId/...`: `:projectId` is the sidebar filter (consumed by `ProjectView.effectiveProjectId`). It may or may not match the session's project.
- `/projects/:projectId/...` (all-projects mode): `:projectId` remains the session's project for URL canonicity; the sidebar ignores it (`effectiveProjectId === ALL_PROJECTS_ID`).

The `beforeEach` guard that propagates `?workspace=` (router.js lines 126-147) is unchanged.

### WebSocket reconciliation priority hint

`useWebSocket.js` continues to pass `route.params.projectId` to `useReconciliation.onReconnected()`. This hint drives two behaviors on reconnect:

- Reload the current project's sessions first (to avoid a stale/empty sidebar)
- Spare the current project/session from being unloaded if items fail to sync after retries

Both are sidebar-visibility concerns, so the URL projectId is the right value here. If the visible session belongs to a different project (cross-filter case), that project is currently not prioritized. This is acceptable in this iteration because the cross-filter case only arises from direct bookmarks — a marginal workflow. The pin iteration will revisit this: when a pinned session lives in a different project, prioritizing both projects becomes warranted.

## Edge cases and risks

### Session not yet loaded at mount

`session` is `undefined` for the brief window between `SessionView` mount and the first sync of the session into the store (initial sync at startup, or a late WS message). During that window, `projectId.value` is `undefined` and API calls from children would target `/api/projects/undefined/...`.

Observed today, this window is extremely short because initial sync runs at startup before any view is mounted. The existing codebase already uses `session?.project_id` with optional chaining in `FilesPanel`, `GitPanel`, `TerminalPanel` without explicit guards, and tolerates the transient `undefined`. The code-comments getters called from `SessionView` (`countBySource`, `countBySession`, `getCommentsBySession`) already tolerate `undefined` as well — they either compare by strict equality (which returns no matches) or produce a lookup key that misses and yields `0`.

Mitigation: preserve the current tolerance. If a regression is observed during implementation, wrap the `<wa-tab-group>` with `v-if="session"` in the template so children never mount before the session is resolved. Do not pre-emptively add the guard; the overhead and added complexity are not justified without evidence of breakage.

### Direct URL where filter project and session project differ

Navigating directly to `/project/A/session/sessionX` where `sessionX.project_id === B`:

- Before this refactor: `SessionView` mounts with `projectId = "A"` and calls `/api/projects/A/sessions/sessionX/...` which returns 404. The session does not load. Broken.
- After this refactor: `SessionView` mounts, `session` resolves to the real session (present in `store.sessions` from initial sync), `projectId` becomes `"B"`, API calls hit `/api/projects/B/sessions/sessionX/...` and succeed. The sidebar stays on `A` and the `SessionList` fallback inserts `sessionX` at the right sort position.

This turns a broken URL into a working one — de facto bug fix.

### `MessageInput` sending a message during the undefined window

If the user somehow types and sends a message before `session` resolves, `props.projectId` is `undefined` and the WS payload carries `project_id: undefined`. The backend would reject it.

In practice this window closes before the user can type. The current design does not need an explicit guard, but implementation may add a defensive check (disable the send button while `projectId === undefined`) if testing uncovers timing issues.

## Implementation checklist

The implementation should touch:

- `frontend/src/stores/data.js` — export `sessionSortComparator`
- `frontend/src/views/SessionView.vue` — introduce `projectId` (computed) and `filterProjectId` (frozen ref); rewire router.push calls to `filterProjectId` (including the `delete-draft` command palette action, which is the one case within `SessionView` that uses the filter-role value despite being inside a command that acts on session data); update the template to pass `session?.project_id` to `SessionItemsList` and `SessionContent`
- `frontend/src/components/SessionList.vue` — move the fallback into `baseSessions`, use the exported comparator, remove the old append branch from `allSessions`

No other file modifications are expected. Children components and store actions retain their current signatures.

## Out of scope follow-ups

- Introduce `pin` as an enum (scope / workspace / always) and the UI for setting it.
- Extend `useReconciliation.onReconnected()` to optionally receive the session's project when it differs from the filter, and prioritize both.
- Explore additional cross-filter sidebar views (e.g. "all currently active sessions across projects").
