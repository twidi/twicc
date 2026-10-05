// frontend/src/utils/scopeMemory.js
//
// Per-scope last-location memory. Each "scope" — a session, a project/worktree page,
// a workspace page, or the All Projects page — remembers the last location the user
// was at inside it. When the user RE-ENTERS a scope by navigating to its bare
// home/entry route, the guard redirects to that remembered location instead, so a
// scope reopens where it was left (like switching back to a window).
//
// Strictly secondary to the URL (spec: 2026-06-21-scope-route-memory-design.md): the
// memory is read at exactly one moment — a fresh (non Back/Forward) navigation that
// ENTERS a scope home from a DIFFERENT scope. It never overrides a displayed route,
// never reconciles, never background-syncs. Volatile: lost on a full page reload.
//
// No per-button wiring: a single global guard upgrades every control that pushes a
// bare scope-home route (sidebar rows, the project button, the workspace / All
// Projects selectors, the deselect toggle).

import { isRailScopeRootNavigation } from './railScopeNavigation.js'
import { buildTabRouteName, buildSubagentRouteName, pickDefined } from './granularRoutes.js'

// --- Route-name families (authoritative; mirrors router.js) ---

const SESSION_NAMES = new Set([
    'session', 'session-files', 'session-git', 'session-terminal',
    'session-artifacts', 'session-orchestration', 'session-plan', 'session-tasks', 'session-workflows', 'session-browser', 'session-subagent',
    'projects-session', 'projects-session-files', 'projects-session-git',
    'projects-session-terminal', 'projects-session-artifacts',
    'projects-session-orchestration', 'projects-session-plan', 'projects-session-tasks', 'projects-session-workflows', 'projects-session-browser', 'projects-session-subagent',
])
const PROJECT_NAMES = new Set([
    'project', 'project-files', 'project-git', 'project-terminal', 'project-artifacts',
])
const ALL_PROJECTS_NAMES = new Set([
    'projects-all', 'projects-files', 'projects-git', 'projects-terminal', 'projects-artifacts',
])
// Bare entry routes (homes) per scope family.
const HOME_NAMES = new Set(['session', 'projects-session', 'project', 'projects-all'])

// The Artifacts browser is a separate sidebar MODE (it has its own last-location
// memory in sidebarViewMemory.js), not a screen within a scope — so it must never
// be recorded as a scope's last location. Kept in the name sets above only so
// scopeKey() still resolves them, which keeps the restore guard's intra-scope
// detection correct (a return artifacts→home stays "intra-scope", no restore).
const ARTIFACTS_BROWSER_NAMES = new Set(['project-artifacts', 'projects-artifacts'])

// --- Pure helpers ---
//
// These only ever receive normalized vue-router RouteLocations (from guards), so
// `route.params` and `route.query` are always objects — hence the unguarded reads.

// Stable per-scope key, or null for non-scope routes (home '/', login, …).
export function scopeKey(route) {
    const name = route?.name
    if (!name) return null
    if (SESSION_NAMES.has(name)) return `session:${route.params.sessionId}`
    if (PROJECT_NAMES.has(name)) return `project:${route.params.projectId}`
    if (ALL_PROJECTS_NAMES.has(name)) {
        const ws = route.query?.workspace
        return ws ? `workspace:${ws}` : 'all-projects'
    }
    return null
}

// True only for a scope's bare entry route (Chat / project stats / All Projects list).
export function isScopeHome(route) {
    return HOME_NAMES.has(route?.name)
}

// The sub-screen id within a scope: '' for the base, else the tool tab / 'subagent'.
// Assumes tab ids are single-segment (no hyphen): files/git/terminal/artifacts/
// orchestration/subagent. A future hyphenated tab name would need a different split.
export function tabOf(name) {
    if (HOME_NAMES.has(name)) return ''
    return name.split('-').pop()
}

// The framing-independent part of a route: which tab + its granular params, dropping
// the context params (projectId/sessionId) that are re-supplied on rebuild.
export function scopeRelative(route) {
    const { projectId, sessionId, ...rest } = route.params || {}
    return { tab: tabOf(route.name), params: rest }
}

// "Nothing better than the home is remembered" — absent, or the base screen itself.
export function isBase(saved) {
    return !saved || saved.tab === ''
}

// Rebuild a concrete route location from a stored {tab, params}, in the framing of the
// navigation being intercepted (`to`): its route-name family, its projectId, and its
// live query (e.g. the current ?workspace=). The stored params are already URL-encoded
// (captured verbatim from route.params), so no re-encoding is needed.
export function rebuild(saved, to) {
    const isAllProjectsMode = to.name.startsWith('projects')
    const isSessionRoute = SESSION_NAMES.has(to.name)
    const name = saved.tab === 'subagent'
        ? buildSubagentRouteName(isAllProjectsMode)
        : buildTabRouteName({ isAllProjectsMode, isSessionRoute, tab: saved.tab })
    const params = {
        ...saved.params,
        ...pickDefined({ projectId: to.params.projectId, sessionId: to.params.sessionId }),
    }
    return { name, params, query: to.query }
}

// --- The memory + guard installer ---

const memory = new Map() // scopeKey -> { tab, params }

export function registerScopeMemory(router) {
    // Back/Forward detection via Vue Router's monotonic history.state.position:
    // on a pop the browser swaps history.state to the destination BEFORE beforeEach
    // runs (position differs from our tracked value); on a fresh push it has not yet.
    let currentPosition = window.history.state?.position ?? 0
    const isHistoryNav = () => {
        const pos = window.history.state?.position
        return pos != null && pos !== currentPosition
    }

    // Active restore: rewrite an enter-scope navigation to the scope's last location.
    router.beforeEach((to, from) => {
        if (isRailScopeRootNavigation(router, to)) return true // explicit rail Sessions root
        if (isHistoryNav()) return true                       // Back/Forward: never touch
        if (!isScopeHome(to)) return true                     // only intercept entry routes
        const toKey = scopeKey(to)
        if (!toKey || toKey === scopeKey(from)) return true   // intra-scope move: leave alone
        const saved = memory.get(toKey)
        if (isBase(saved)) return true                        // nothing better than the home
        return rebuild(saved, to)
    })

    // Passive recording: remember where the user is in each scope. Skip aborted /
    // redirected intermediates so we only record settled screens.
    router.afterEach((to, from, failure) => {
        if (failure) return
        currentPosition = window.history.state?.position ?? currentPosition
        if (ARTIFACTS_BROWSER_NAMES.has(to.name)) return   // separate mode — never a scope's last location
        const key = scopeKey(to)
        if (key) memory.set(key, scopeRelative(to))
    })
}
