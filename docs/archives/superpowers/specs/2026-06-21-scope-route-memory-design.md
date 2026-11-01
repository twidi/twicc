# Per-Scope Last-Location Memory

**Date:** 2026-06-21
**Status:** DRAFT

## Problem

Navigating away from a screen and coming back to it no longer returns the user to
where they were. Concretely: open session A, switch to its Files tab on a specific
file, click session B in the sidebar, then click session A again — A reopens on the
Chat tab, not on the file the user left. The same happens for project home pages,
workspace pages, and the All Projects page: re-entering them always lands on their
base/entry screen.

This is the deliberate consequence of the
[Granular URL Routing](./2026-04-19-granular-url-routing.md) design: "the URL is the
only source of truth", and **bare routes are entry routes, not remembered state**.
Re-entering a screen is done by navigating to its bare route, which resolves to that
scope's canonical default (Chat for a session, stats for a project, the list for All
Projects). Browser Back/Forward still restores the exact previous screen because the
full URL is replayed — but clicking a sidebar row or a header button pushes the bare
route, so the place is lost.

That same Granular URL Routing spec anticipated this feature explicitly (§7, "Simple
user model"):

> If later we want "remember last file per tab", that must store a full route
> location and remain strictly secondary to the current URL.

This is that "later".

## Goal

When the user **enters a scope** (a session, a project page, a worktree page, a
workspace page, or the All Projects page) by an in-app action whose nominal target is
that scope's home/entry route, restore the **last location they were at within that
scope** instead of the bare entry route — while leaving browser history, the URL as
source of truth, and KeepAlive entirely untouched.

Mental model: each scope behaves like a separate window that remembers its own state.
Re-focusing a "window" returns you to where you left it; navigating inside it is free
and unconstrained.

## Relationship to Granular URL Routing (consistency)

This feature is the §7-sanctioned addition and must honor the prior design's
non-functional requirements rather than violate them:

- **NFR #1 — URL is the only source of truth for the *current* screen.** Unchanged.
  The memory never overrides what the URL says while a screen is displayed. It is
  consulted **only at navigation-initiation time**, to rewrite an *enter-scope*
  navigation's destination before it commits. Once any screen is shown, the route
  alone determines it.
- **NFR #2 — No mirrored navigation cache.** The memory is not a parallel "live"
  model competing with the URL: it is never read to reconcile, repair, or mutate a
  displayed route, and inactive scopes are never background-synced. It stores a
  resolved location and is read at exactly one moment (entering a scope). This is the
  "full route location, strictly secondary to the URL" exception §7 allows.
- **NFR #3 — KeepAlive is a performance tool, not a routing tool.** Unchanged.
  KeepAlive still just renders the current route faster. The memory mechanism lives in
  the router layer, not in KeepAlive lifecycle.

The litmus test for "strictly secondary": if the memory map were wiped at any instant,
every currently-displayed screen would be unaffected, and only the *next* enter-scope
click would differ. That holds here.

## Concept: Scope

A **scope** is one navigational context the user thinks of as a place with its own
state. There are five kinds:

| Kind | What it is | Route family (representative names) |
|---|---|---|
| Session | One session's screen (Chat + tool tabs + subagents) | `session`, `session-files`, `session-git`, `session-terminal`, `session-artifacts`, `session-orchestration`, `session-subagent`, and the `projects-session*` mirror |
| Project | One project's home (stats + tool tabs) | `project`, `project-files`, `project-git`, `project-terminal`, `project-artifacts` |
| Worktree | A worktree's project home | same as Project (a worktree *is* a project with its own id) |
| Workspace | The All Projects view filtered by a workspace | `projects-all`, `projects-files`, … **with** `?workspace=<id>` |
| All Projects | The All Projects view, no workspace filter | `projects-all`, `projects-files`, … **without** `?workspace` |

The global site home (`/`, `HomeView`) is **not** a scope and is never recorded or
intercepted.

The authoritative mapping from `route.name` to scope kind lives in one new module
(below); the names above are representative and have been verified to exist verbatim
in `router.js`.

### Scope key

Each scope has a stable string key, derived purely from the route:

| Scope | Key | Source |
|---|---|---|
| Session | `session:<sessionId>` | `route.params.sessionId` |
| Project / Worktree | `project:<projectId>` | `route.params.projectId` |
| Workspace | `workspace:<workspaceId>` | `route.query.workspace` |
| All Projects | `all-projects` | constant |

Notes:
- A session has **one** key regardless of whether it is being viewed in
  single-project framing (`session*`) or all-projects framing (`projects-session*`).
  Framing is rebuilt at restore time (see below), not stored.
- Worktree vs main project fall out for free: a worktree carries its own `projectId`,
  so `project:<id>` already distinguishes them — no special handling.
- Workspace and All Projects share the `projects-*` route family and are
  differentiated solely by the `?workspace` query, which the key captures.

### Scope home (entry route)

A route is its scope's **home** when it is the bare base of the family — the entry
route Granular URL Routing canonicalizes to:

- Session home: `session` / `projects-session` (Chat, no tool tab, no subagent)
- Project home: `project` (stats)
- Workspace / All Projects home: `projects-all` (the list)

`isScopeHome(route)` is true only for these base names. Tool-tab and subagent routes
are not homes.

## What is stored

For each scope key, the memory holds a **scope-relative location**: the part of the
last route that is *internal* to the scope, framing-independent:

```
{ tab, params }
```

- `tab` — which sub-screen within the scope: the tool tab id (`files`, `git`,
  `terminal`, `artifacts`, `orchestration`), `subagent`, or the base/Chat itself.
- `params` — that sub-screen's granular params (`rootKey`, `filePath`, `commitRef`,
  `termIndex`, `subagentId`, `bookmarkId`), exactly as Granular URL Routing already
  encodes them.

The valid `tab` values differ per scope kind (e.g. `orchestration` and `subagent`
exist only for sessions; project/workspace/all-projects scopes have no orchestration
tab). `scopeRelative` must enumerate exactly the tabs each scope can actually show,
matched against what each view renders.

It deliberately does **not** store: the framing (single vs all-projects), the
`projectId`, or the `?workspace` query. Those belong to the *context the user is
re-entering from* and are supplied fresh at restore time. Storing the scope-relative
part is what makes "key by session, rebuild in current framing" clean, and it dodges
both the framing-flip and stale-workspace-query hazards.

The map is **in-memory and volatile**: it lives for the lifetime of the page and is
lost on a full reload. That is intended — after a reload the user still has normal
browser history. No persistence, no invalidation logic.

## Mechanism

Two router hooks plus one small module.

### Passive recording — `router.afterEach`

After every settled navigation, record the current scope-relative location:

```
afterEach((to) => {
  const key = scopeKey(to)            // null for non-scope routes (e.g. '/')
  if (key) memory.set(key, scopeRelative(to))
})
```

This runs unconditionally — including during Back/Forward. That keeps the memory
truthful: after going Back into a scope, its remembered location becomes wherever
Back landed. `afterEach` fires on the *final* route after any canonicalizing
`replace`, so we record resolved screens, not transient ones.

### Active restore — `router.beforeEach`

Rewrite an *enter-scope* navigation's destination to the remembered location:

```
beforeEach((to, from) => {
  if (isHistoryNav())                    return true   // Back/Forward: never touch
  if (!isScopeHome(to))                  return true   // only intercept entry routes
  if (scopeKey(to) === scopeKey(from))   return true   // intra-scope move: leave alone
  const saved = memory.get(scopeKey(to))
  if (!saved || isBase(saved))           return true   // nothing better than the home
  return rebuild(saved, to)                             // redirect to the remembered place
})
```

Both filters are necessary:

1. **History-nav skip** — guarantees Back/Forward replays exact history entries. The
   user's invariant: from `[A1, A2, X]`, "back ×2" must land on `A1`, never be
   bounced to A's last location. Without this, popping to a scope-home entry that
   differs from the scope's current last location would be hijacked.
2. **Intra-scope skip** — `scopeKey(to) === scopeKey(from)` means the user is moving
   *within* a scope and nothing is forced (this is the original spec: "as long as I
   stay in the same scope, just keep the last URL"). The load-bearing case: clicking
   the **Chat** tab inside a session navigates to the bare session route, which *is*
   the session's home; without this filter the guard would bounce Chat back to the
   last tool tab and Chat would become unreachable. With it, Chat stays Chat (and
   `afterEach` then records Chat as the scope's new last location).

Redirect semantics: returning a location from `beforeEach` makes Vue Router redirect,
so the bare home is never committed to history — the triggering push of the home
becomes a push of the remembered location instead. The redirect's target is a
granular (non-home) route, so `isScopeHome` is false on the re-run and there is no
loop; if `saved` happens to *be* the base, `isBase(saved)` short-circuits and we never
redirect.

`isBase(saved)` and the intra-scope skip cover **distinct entry paths** and neither is
redundant: the intra-scope skip keeps Chat reachable *within* a session (a same-scope
move to the home), while `isBase` handles a *cross-scope* entry whose remembered
location is itself the base (e.g. the user last left that scope on its Chat/stats
screen).

Because the restore lives in a single global guard, **no per-button wiring is
needed**: every existing and future control that navigates to a scope home (sidebar
rows, the header "go to project" button, the workspace/all-projects selectors, the
deselect-toggle that returns to the project) is upgraded automatically.

### Rebuild in the current framing

`rebuild(saved, to)` produces the concrete target by combining the **stored
scope-relative `{ tab, params }`** with the **current context taken from `to`** (its
route-name family → framing, its `projectId`, its `?workspace` query):

- If `to.name === 'projects-session'` and `saved.tab === 'files'`, target
  `projects-session-files` with `{ projectId: to.params.projectId, sessionId, …files
  params }` and `query: to.query`.
- If `to.name === 'session'` (single-project framing), target `session-files`
  similarly.

Thus the same stored `{ tab, params }` reframes correctly whether the session is
re-entered in single-project or all-projects mode, and always carries the *current*
workspace query rather than a stale one. Project, Workspace and All Projects scopes
rebuild the same way against their own route families.

## Detecting Back/Forward

This is the only non-trivial piece of machinery. We must classify each navigation as
a fresh push vs a history replay (Back or Forward), and skip the latter in the restore
guard.

**Chosen approach — track Vue Router's history position.** `createWebHistory`
maintains a monotonic integer `position` in `history.state` (the same value that
drives `scrollBehavior`'s `savedPosition`). We keep a module variable
`currentPosition`:

- In `beforeEach`, read `window.history.state?.position`. On Back/Forward the browser
  has already swapped `history.state` to the destination entry before the guard runs,
  so it differs from our tracked `currentPosition` → history nav. On a programmatic
  push the state is not yet updated when the guard runs, so it equals
  `currentPosition` → fresh nav.
- In `afterEach`, set `currentPosition = window.history.state?.position`.

This detects both Back and Forward (both land on a different position) and both are
skipped, which is correct — any history replay must be honored verbatim.

**Fallback** if position-tracking proves unreliable in practice: a `popstate`
listener that sets a one-shot flag consumed in `beforeEach`. The position approach is
preferred because it reads Vue Router's own signal and is already correct at guard
time for pops.

Because the entire history-skip filter rests on this timing assumption (that on
Back/Forward `history.state` is already swapped before `beforeEach`, but on a fresh
push it is not yet), **validating it empirically is the first implementation task** —
observe `history.state.position` at guard time for a fresh push vs a Back vs a
Forward. If it does not hold, switch to the `popstate` fallback before building
anything on top, since a wrong assumption here silently misclassifies every
navigation.

## Worked scenarios

1. **Sidebar A→B→A.** On A/Files/foo. Click B (push bare `session` B): scope change,
   home, fresh, memory[B] empty → B home; record B. Click A (push bare `session` A):
   scope change, home, fresh, memory[A] = `{files, foo}` → redirect to A/Files/foo. ✔
2. **Back ×2.** History `[A1, A2, X]` (A1, A2 same scope A; X scope B). "Back ×2" pops
   to A1: history nav → guard skips → lands exactly on A1. ✔
3. **Chat tab inside a session.** On A/Files. Click Chat (push bare `session` A):
   `scopeKey(to) === scopeKey(from)` → skip → goes to Chat; `afterEach` records
   `{base}` as A's last location. ✔
4. **Header "go to project".** From a session, click the project button (push bare
   `project` P): scope change, home, fresh, memory[P] = `{git, commitX}` → reopen the
   project on Git at commitX. Intended ("like switching windows"). ✔
5. **Deselect toggle.** Click the active session row → returns to project P (push bare
   `project`): scope change → restores P's last location, consistent with the model. ✔
6. **Workspace switch.** Click workspace W (push `projects-all?workspace=W`): scope
   `workspace:W`, home, fresh → restore W's last location (or its list if none). ✔

## Interaction with existing systems

- **`rememberedToolTabRoutes`** (`SessionView.vue`) — the per-session, in-instance
  memory of each tool tab's last granular sub-route, used for tab-switching *inside* a
  session. Complementary and kept: scope memory chooses the **entry point** when
  re-entering a scope (one location per scope); `rememberedToolTabRoutes` handles the
  **per-tab** sub-state once inside. Different granularities, no conflict.
- **Absent-tab canonicalization** (`SessionView.vue` `absentActiveToolTab` →
  `router.replace` to base) — if a remembered location points to a tab that is no
  longer present (e.g. Git on a session whose repo is gone), the restore lands there
  and the existing guard replaces to the base. That replace is **intra-scope**, so the
  restore guard skips it: graceful self-heal to Chat, no loop.
- **Panel-level canonicalization** (missing file/commit/terminal) — same story: the
  remembered granular URL degrades via the panels' existing `replace` fallbacks, all
  intra-scope, no loop.
- **KeepAlive** — untouched; it renders whatever route the restore produced.

## Non-Goals

- **No persistence across reload.** The map is volatile by design.
- **No change to browser history semantics.** Back/Forward are never intercepted; the
  restore only rewrites fresh enter-scope pushes.
- **No new URL state.** Scroll position, tree expansion, branch filters, etc. remain
  out of the URL (as in Granular URL Routing) and out of this memory.
- **No "force home" affordance.** Entering a scope intentionally restores its last
  place; reaching the bare home is not a goal of this iteration (the site home `/`
  remains a separate, non-scope page).
- **No per-control wiring.** The mechanism is global; we do not special-case
  individual buttons.

## Components affected

### New: scope-memory module (`frontend/src/utils/scopeMemory.js`)

Placed beside the existing `granularRoutes.js` and `sidebarViewMemory.js`. The single
home for this feature's logic, as pure/centralized functions plus the map:

- `scopeKey(route)` → string | null
- `isScopeHome(route)` → boolean
- `scopeRelative(route)` → `{ tab, params }`
- `isBase(saved)` → boolean
- `rebuild(saved, to)` → route location (reframed against `to`)
- the in-memory `Map` plus `record`/`lookup`
- the history-position tracking helper (`isHistoryNav`)
- a `registerScopeMemory(router)` that installs the `beforeEach` + `afterEach` hooks

Centralizing the route-name knowledge here (which names belong to which scope family,
which are homes) keeps `router.js` and the views unchanged in spirit.

### `frontend/src/router.js`

- Call `registerScopeMemory(router)` after route definitions. No route changes.

### Views / navigation callers

- **No changes required.** Sidebar rows, header buttons, selectors, and the
  deselect-toggle already push bare scope-home routes; the guard upgrades them.
- If any enter-scope action currently uses `router.replace` (rather than `push`),
  audit during planning so the redirect inherits the right history operation. (Known
  enter-scope actions — `handleSessionSelect`, `handleProjectSelect`, the workspace
  selector, the sidebar link — use `push`.)

## Edge cases

- **First entry to a scope** — no memory → bare home renders → `afterEach` records it.
- **Remembered tab now absent / file or commit deleted** — graceful self-heal via the
  existing intra-scope `replace` guards; no loop (restore guard skips intra-scope).
- **Reload** — memory empty; the current URL renders unaffected; recording resumes.
- **Native open-in-new-tab / deep link / shared URL** — a fresh page with empty
  memory; the URL is honored verbatim.
- **Draft session id rebind** (draft temp id → canonical id) — keying is by the route's
  `sessionId`; a rebind that changes the URL simply starts a new key. Stale draft-keyed
  entries are harmless (never navigated to).
- **Duplicate history entries** — restoring via a redirected push can place the same
  location twice in history across visits; harmless.
- **Map growth** — one small entry per visited scope, volatile; no eviction needed.

## Testing

- **Unit (pure functions):** `scopeKey`, `isScopeHome`, `scopeRelative`, `isBase`,
  and `rebuild` (especially reframing single ↔ all-projects and workspace-query
  substitution), plus the filter decision in `beforeEach` for the matrix
  {history-nav, intra-scope, home/not-home, memory present/absent}.
- **Integration / manual:** the six worked scenarios above, plus the self-heal path
  (remembered Git tab on a now-non-git session) and a Back/Forward replay across
  scope boundaries to confirm the guard stays inert.
