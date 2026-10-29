// Shared store fixture of the sidebar tests
// (design: docs/plans/2026-10-28-sidebar-recompute-cost-design.md, section 7).
//
// `buildSidebarState` builds a deterministic reactive store state.
// `createFixtureStores` gives the `{ data, workspaces }` store facades that read
// it the way the real Pinia stores do: every field that is a Pinia getter in the
// real store is a `computed` read through a property getter, so it caches like
// Pinia does.
//
// Fixture layout (rank 0 = most recent root, every root a distinct mtime):
//   - top block, above every cutoff: the launched ephemeral root and the draft
//     (project p3), three p0 sessions with a process (assistant_turn,
//     user_turn with a background shell, user_turn with an active cron), two
//     unread roots without a process (p4, p5), two `pinned: 'all'` roots (p6,
//     p7), the p0 `child` of the tree ancestor, and the read roots p0-recent,
//     p0-second, p1-recent and p3-recent (step targets);
//   - regular read roots `r<n>` above the `__all__` cutoff, projects in rotation;
//   - cut-p2 (p2, between the p2 and the `__all__` cutoffs), cut-p0 (p0,
//     between the p0 and the `__all__` cutoffs);
//   - regular read roots below every cutoff;
//   - old-p2-wt1 (p2-wt1) and the p0 tree `ancestor`, below every cutoff.
// Every root except the unread, pinned and process ones above is read, has no
// process and is not pinned. Each root except the draft and the ephemeral one
// has `subagentsPerRoot` subagents (`<root>-sa<k>`), inserted right after it.

import { computed, reactive, toRaw } from 'vue'
import { ALL_PROJECTS_ID } from '../../src/constants.js'
import { buildProjectActivityIndex, createProjectActivityComparator } from '../../src/utils/projectActivity.js'
import { getScopeSessionList, getStableSessionList } from '../../src/utils/sessionLists.js'
import { getRootSessions } from '../../src/utils/rootSessions.js'
import { getUnreadMemos } from '../../src/utils/unreadMemos.js'
import { getProjectActivityIndex } from '../../src/utils/projectProcessActivity.js'
import { createWorkspaceMembership, getArchivedProjectIds, getWorktreeIdsByParent } from '../../src/utils/projectScopes.js'
import { isWorkspaceProjectId } from '../../src/utils/workspaceIds.js'
import { hasActiveStartupPhase } from '../../src/utils/startupPhase.js'

const T0 = 1_760_000_000  // seconds, like session and project mtimes
const RANK_STEP = 10      // mtime gap between two consecutive roots

/** Ids of the roots the fixture places on purpose. */
export const FIXTURE_IDS = Object.freeze({
    ephemeral: 'ephemeral',
    draft: 'draft',
    processAssistant: 'proc-assistant',
    processShell: 'proc-shell',
    processCron: 'proc-cron',
    unread1: 'unread-1',
    unread2: 'unread-2',
    pinned1: 'pinned-1',
    pinned2: 'pinned-2',
    child: 'child',
    ancestor: 'ancestor',
    p0Recent: 'p0-recent',
    p0Second: 'p0-second',
    p1Recent: 'p1-recent',
    p3Recent: 'p3-recent',
    cutP2: 'cut-p2',
    cutP0: 'cut-p0',
    oldWorktree: 'old-p2-wt1',
})

const ID = FIXTURE_IDS
const TOP_ROOTS = [
    { id: ID.ephemeral, project_id: 'p3', kind: 'ephemeral' },
    { id: ID.draft, project_id: 'p3', kind: 'draft' },
    { id: ID.processAssistant, project_id: 'p0', process: { state: 'assistant_turn', started_at: 300 } },
    { id: ID.processShell, project_id: 'p0',
        process: { state: 'user_turn', started_at: 200, background_work_in_progress: { shells: 1 } } },
    { id: ID.processCron, project_id: 'p0',
        process: { state: 'user_turn', started_at: 100, active_crons: [{ id: 'cron-1', schedule: '*/5 * * * *' }] } },
    { id: ID.unread1, project_id: 'p4', unread: true },
    { id: ID.unread2, project_id: 'p5', unread: true },
    { id: ID.pinned1, project_id: 'p6', pinned: 'all' },
    { id: ID.pinned2, project_id: 'p7', pinned: 'all' },
    { id: ID.child, project_id: 'p0', spawned_by: ID.ancestor },
    { id: ID.p0Recent, project_id: 'p0' },
    { id: ID.p0Second, project_id: 'p0' },
    { id: ID.p1Recent, project_id: 'p1' },
    { id: ID.p3Recent, project_id: 'p3' },
]
const CUTOFF_ROOTS = [{ id: ID.cutP2, project_id: 'p2' }, { id: ID.cutP0, project_id: 'p0' }]
const BOTTOM_ROOTS = [{ id: ID.oldWorktree, project_id: 'p2-wt1' }, { id: ID.ancestor, project_id: 'p0' }]
const PLACED_BELOW = CUTOFF_ROOTS.length + BOTTOM_ROOTS.length

/**
 * The initial workspaces of the fixture: `w` = {p0, p1}, `w1` = {p0} (a one-project
 * workspace). Each state holds its own reactive copy (`state.workspaces`), which the
 * steps change the way `stores/workspaces.js` does.
 */
export const FIXTURE_WORKSPACES = Object.freeze([
    Object.freeze({ id: 'w', name: 'Workspace w', archived: false, projectIds: Object.freeze(['p0', 'p1']) }),
    Object.freeze({ id: 'w1', name: 'Workspace w1', archived: false, projectIds: Object.freeze(['p0']) }),
])

/**
 * Scope ids of design 7.1 #9: a single project, a main repository with
 * worktrees, a workspace, a one-project workspace, all projects, no project id.
 */
export const FIXTURE_SCOPES = Object.freeze(['p0', 'p2', 'ws:w', 'ws:w1', ALL_PROJECTS_ID, null])

// Per-state fixture clocks, outside the reactive state.
const metaByState = new WeakMap()

function metaOf(state) {
    const meta = metaByState.get(toRaw(state))
    if (!meta) throw new Error('not a buildSidebarState() state')
    return meta
}

const iso = (seconds) => new Date(seconds * 1000).toISOString()

/** A content time above every timestamp written so far through the fixture. */
function nextContentTime(state) {
    const meta = metaOf(state)
    meta.contentSeconds += 1
    return iso(meta.contentSeconds)
}

/** An mtime above every mtime written so far through the fixture. */
function nextTopMtime(state) {
    const meta = metaOf(state)
    meta.topMtime += RANK_STEP
    return meta.topMtime
}

function rootRecord({ id, project_id, kind, unread = false, pinned = null, spawned_by = null }, mtime) {
    const content = iso(mtime)
    const record = {
        id, project_id, parent_session_id: null, provider: 'claude', title: `Session ${id}`,
        mtime, last_line: 10, last_new_content_at: content, last_viewed_at: unread ? iso(mtime - 5) : content,
        hidden: false, archived: false, pinned, mute_on_user_turn: false, spawned_by, tree_detached: false,
    }
    if (kind === 'draft') Object.assign(record, { title: null, last_line: 0, last_new_content_at: null,
        last_viewed_at: null, draft: true })
    if (kind === 'ephemeral') Object.assign(record, { last_new_content_at: null, last_viewed_at: null,
        draft: false, ephemeral: true, ephemeralPhase: 'running', ephemeralStartedAt: iso(mtime) })
    return record
}

function subagentRecord(root, id) {
    const content = iso(root.mtime)
    return {
        id, project_id: root.project_id, parent_session_id: root.id, provider: 'claude', title: `Agent ${id}`,
        mtime: root.mtime, last_line: 5, last_new_content_at: content, last_viewed_at: content,
        hidden: false, archived: false, pinned: null, mute_on_user_turn: false, spawned_by: null, tree_detached: false,
    }
}

function processRecord(session, { state, started_at, ...rest }) {
    return { session_id: session.id, project_id: session.project_id, state, started_at, pending_requests: [], ...rest }
}

function projectRecord(id, mtime, worktreeOf = null) {
    return { id, name: `Project ${id}`, directory: `/fixture/${id}`, mtime, archived: false, worktree_of: worktreeOf }
}

/**
 * Build the deterministic store state of the sidebar tests.
 *
 * @param {Object} options
 * @param {number} options.roots - total number of root sessions (at least 18)
 * @param {number} [options.subagentsPerRoot=2]
 * @param {number} [options.projects=10] - plain projects `p0`…`p<n-1>` (at least 8), plus the worktrees `p2-wt1`, `p2-wt2`
 * @param {?number} [options.cutoffRoots=null] - number of roots above the `__all__` cutoff; null: every
 *     scope is fully loaded (`hasMoreSessions: false`), with the same cursor values
 * @returns {Object} reactive `{ sessions, projects, processStates, startupProgress, settings, localState, workspaces }`
 */
export function buildSidebarState({ roots, subagentsPerRoot = 2, projects = 10, cutoffRoots = null }) {
    if (projects < 8) throw new Error('the fixture needs at least 8 projects')
    if (roots < TOP_ROOTS.length + PLACED_BELOW) throw new Error(`the fixture needs at least ${TOP_ROOTS.length + PLACED_BELOW} roots`)
    const above = cutoffRoots ?? roots - PLACED_BELOW
    if (above < TOP_ROOTS.length || above > roots - PLACED_BELOW) {
        throw new Error(`cutoffRoots must be between ${TOP_ROOTS.length} and ${roots - PLACED_BELOW}`)
    }

    // Plain projects get a permuted activity; the worktrees of p2 are not in activity order by id.
    const projectsById = {}
    for (let i = 0; i < projects; i++) projectsById[`p${i}`] = projectRecord(`p${i}`, T0 + ((i * 7) % projects) * 100)
    projectsById['p2-wt1'] = projectRecord('p2-wt1', T0 + projects * 100 + 10, 'p2')
    projectsById['p2-wt2'] = projectRecord('p2-wt2', T0 + projects * 100 + 20, 'p2')
    const rotation = Object.keys(projectsById)

    const regularCount = roots - TOP_ROOTS.length - PLACED_BELOW
    const regular = (from, to) => Array.from({ length: to - from }, (_, i) =>
        ({ id: `r${from + i}`, project_id: rotation[(from + i) % rotation.length] }))
    const regularAbove = above - TOP_ROOTS.length
    const ranked = [...TOP_ROOTS, ...regular(0, regularAbove), ...CUTOFF_ROOTS,
        ...regular(regularAbove, regularCount), ...BOTTOM_ROOTS]
    const mtimeOfRank = (rank) => T0 + (roots - rank) * RANK_STEP

    // cut-p2 sits at rank `above`: 5 under the `__all__` cutoff; cut-p0 at 15 under it.
    const allCutoff = mtimeOfRank(above) + 5
    const cutoffs = { [ALL_PROJECTS_ID]: allCutoff, p2: allCutoff - 8, p0: allCutoff - 18,
        'ws:w': allCutoff - 30, 'ws:w1': allCutoff - 40 }

    const sessions = {}
    const processStates = {}
    ranked.forEach((descriptor, rank) => {
        const root = rootRecord(descriptor, mtimeOfRank(rank))
        sessions[root.id] = root
        if (descriptor.process) processStates[root.id] = processRecord(root, descriptor.process)
        if (descriptor.kind) return
        for (let k = 0; k < subagentsPerRoot; k++) {
            const id = `${root.id}-sa${k}`
            sessions[id] = subagentRecord(root, id)
        }
    })

    const localProjects = {}
    for (const [scopeId, oldestSessionMtime] of Object.entries(cutoffs)) {
        localProjects[scopeId] = { hasMoreSessions: cutoffRoots != null, oldestSessionMtime }
    }

    const state = reactive({
        sessions,
        projects: projectsById,
        processStates,
        startupProgress: {},
        settings: { isShowArchivedProjects: false },
        localState: { projects: localProjects },
        workspaces: FIXTURE_WORKSPACES.map(ws => ({ ...ws, projectIds: [...ws.projectIds] })),
    })
    const topMtime = mtimeOfRank(0)
    metaByState.set(toRaw(state), { topMtime, contentSeconds: topMtime + 1000 })
    return state
}

/**
 * One real batch of a session: `mtime`, `last_line` and `last_new_content_at`
 * advance; for the open session `last_viewed_at` follows. A tick therefore
 * flips a read session to unread, except an open tick. `top` gives the session
 * an mtime above every mtime written through the fixture; `mtime` gives it that
 * exact mtime; otherwise the mtime advances by 1, less than the gap between two
 * fixture roots. Reads the session's own `mtime` and `last_line` once.
 */
export function tick(state, sessionId, { open = false, top = false, mtime: at = null } = {}) {
    const session = state.sessions[sessionId]
    if (!session) throw new Error(`unknown session ${sessionId}`)
    const meta = metaOf(state)
    const mtime = top ? nextTopMtime(state) : at ?? session.mtime + 1
    meta.topMtime = Math.max(meta.topMtime, mtime)
    const contentAt = nextContentTime(state)
    session.mtime = mtime
    session.last_line = session.last_line + 1
    session.last_new_content_at = contentAt
    if (open) session.last_viewed_at = contentAt
}

/**
 * Set a startup phase of the real shape: active (`completed: false`) or ended.
 * Replaces the object, like `setStartupProgress`.
 */
export function startupPhase(state, active) {
    state.startupProgress = { background_compute: { claude: { current: 1, total: 2, completed: !active, detail: null } } }
}

/** One startup progress message: a new `startupProgress` object, the way `setStartupProgress` writes it. */
export function progressMessage(state) {
    const phase = state.startupProgress.background_compute || {}
    const entry = phase.claude || { current: 0, total: 2, completed: false }
    const current = entry.current + 1
    state.startupProgress = {
        ...state.startupProgress,
        background_compute: { ...phase,
            claude: { current, total: Math.max(entry.total, current), completed: entry.completed, detail: null } },
    }
}

function addRoot(state, { id, project_id }) {
    const root = rootRecord({ id, project_id }, nextTopMtime(state))
    root.last_new_content_at = root.last_viewed_at = nextContentTime(state)
    state.sessions[id] = root
}

function addWorktree(state, parentId, id) {
    addRoot(state, { id: `${id}-root`, project_id: id })
    state.projects[id] = projectRecord(id, nextTopMtime(state), parentId)
}

const step = (name, apply, startup = null) => Object.freeze({ name, startup, apply })

/**
 * The shared update sequence of the behaviour and coverage tests, applied in
 * order on one state with `applyStep`. `startup` marks the startup steps:
 * `'write'` (a phase starts and one untracked write happens: no comparison
 * until the next message), `'progress'` (a progress message) and `'end'`.
 */
export const FIXTURE_UPDATE_SEQUENCE = Object.freeze([
    step('in-scope tick without order change', state => tick(state, ID.processAssistant, { open: true })),
    step('in-scope tick with order change', state => tick(state, ID.p0Recent, { top: true })),
    step('out-of-scope tick without order change', state => tick(state, ID.unread1)),
    step('out-of-scope tick with order change', state => tick(state, ID.unread2, { top: true })),
    step('unread flip to unread on a user_turn session with background work', state => {
        state.sessions[ID.processShell].last_new_content_at = nextContentTime(state)
    }),
    step('unread flip to read', state => { state.sessions[ID.unread1].last_viewed_at = nextContentTime(state) }),
    step('pin change', state => { state.sessions[ID.p3Recent].pinned = 'all' }),
    step('archive', state => { state.sessions[ID.p0Second].archived = true }),
    step('hide', state => { state.sessions[ID.p1Recent].hidden = true }),
    step('cutoff crossing', state => tick(state, ID.oldWorktree,
        { mtime: state.localState.projects[ALL_PROJECTS_ID].oldestSessionMtime + 1 })),
    step('root add', state => addRoot(state, { id: 'added-root', project_id: 'p0' })),
    step('root removal', state => { delete state.sessions[ID.p0Recent] }),
    step('subagent add', state => {
        state.sessions['child-sa-added'] = subagentRecord(toRaw(state.sessions[ID.child]), 'child-sa-added')
    }),
    step('subagent removal', state => { delete state.sessions['child-sa-added'] }),
    step('workspace member archived', state => { state.projects.p1.archived = true }),
    step('worktree added to p2', state => addWorktree(state, 'p2', 'p2-wt3')),
    step('worktree added to p0', state => addWorktree(state, 'p0', 'p0-wt1')),
    // Like `updateWorkspace`: a new `projectIds` array. p2 brings its worktrees along.
    step('workspace gains a project', state => {
        const ws = state.workspaces.find(w => w.id === 'w')
        ws.projectIds = [...ws.projectIds, 'p2']
    }),
    step('workspace loses a project', state => {
        const ws = state.workspaces.find(w => w.id === 'w')
        ws.projectIds = ws.projectIds.filter(id => id !== 'p2')
    }),
    step('process start', state => {
        state.processStates[ID.unread1] = processRecord(toRaw(state.sessions[ID.unread1]),
            { state: 'assistant_turn', started_at: 400 })
    }),
    step('process stop', state => { delete state.processStates[ID.processCron] }),
    step('startup phase with one untracked write', state => {
        startupPhase(state, true)
        state.sessions[ID.cutP0].hidden = true
    }, 'write'),
    step('progress message', progressMessage, 'progress'),
    step('end of startup', state => startupPhase(state, false), 'end'),
])

/** Apply one step of `FIXTURE_UPDATE_SEQUENCE`. */
export function applyStep(state, step) {
    step.apply(state)
}

/**
 * A fresh copy of the state, outside startup: what a page reload would load
 * from the same data. Its stores share no cache with the live state, so blocks
 * computed on it are what the sidebar must show after the live updates.
 */
export function reloadedState(state) {
    return reactive(structuredClone({ ...toRaw(state), startupProgress: {} }))
}

/**
 * `{ data, workspaces }` store facades over the state, wired like `stores/data.js`
 * and `stores/workspaces.js`: the project getters (`getProjects`, `getWorktreesOf`)
 * are the activity-sorted ones, the workspace membership reads the activity-free
 * inputs (`archivedProjectIds`, `worktreeIdsByParent`).
 */
export function createFixtureStores(state) {
    // Like the store: derived activity, the comparator, and the activity-sorted project lists.
    const projectActivityById = computed(() => buildProjectActivityIndex(Object.values(state.projects)))
    const projectActivityComparator = computed(() => createProjectActivityComparator(projectActivityById.value))
    const getProjects = computed(() => Object.values(state.projects).sort(projectActivityComparator.value))
    const getWorktreesOf = computed(() => {
        const comparator = projectActivityComparator.value
        return (projectId) => Object.values(state.projects)
            .filter(project => project.worktree_of === projectId)
            .sort(comparator)
    })
    const getProject = computed(() => (id) => state.projects[id])
    const getMainRepoProjectId = computed(() => (projectId) =>
        (projectId && state.projects[projectId]?.worktree_of) || projectId)
    const getProjectScopeIds = computed(() => (projectId) => {
        if (!projectId || projectId === ALL_PROJECTS_ID || isWorkspaceProjectId(projectId)) {
            return projectId ? [projectId] : []
        }
        const ids = [projectId]
        for (const p of Object.values(state.projects)) {
            if (p.worktree_of === projectId) ids.push(p.id)
        }
        return ids
    })
    const getProjectSessions = computed(() => (projectId) => getStableSessionList(state, projectId, ALL_PROJECTS_ID))
    const getAllSessions = computed(() => getStableSessionList(state, ALL_PROJECTS_ID, ALL_PROJECTS_ID))
    const isStartupInProgress = computed(() => hasActiveStartupPhase(state.startupProgress))
    // Like the store getter: a function, so each call reads `startupProgress`.
    const inStartupPhase = computed(() => () => hasActiveStartupPhase(state.startupProgress))
    const rootSessions = computed(() => getRootSessions(state))
    const scopeSessionList = computed(() => (scope) => getScopeSessionList(state, scope))
    const hasUnreadContentMemo = computed(() => (session) => getUnreadMemos(state).unreadContent(session))
    const archivedProjectIds = computed(() => getArchivedProjectIds(state))
    const worktreeIdsByParent = computed(() => getWorktreeIdsByParent(state))
    const projectActivitySummary = computed(() => {
        const index = getProjectActivityIndex(data)
        return (projectIds, previous) => index.summary(projectIds, previous)
    })

    const data = {
        get sessions() { return state.sessions },
        get projects() { return state.projects },
        get processStates() { return state.processStates },
        get startupProgress() { return state.startupProgress },
        get localState() { return state.localState },
        get projectActivityById() { return projectActivityById.value },
        get getProjects() { return getProjects.value },
        get getWorktreesOf() { return getWorktreesOf.value },
        get getProject() { return getProject.value },
        get getMainRepoProjectId() { return getMainRepoProjectId.value },
        get getProjectScopeIds() { return getProjectScopeIds.value },
        get getProjectSessions() { return getProjectSessions.value },
        get getAllSessions() { return getAllSessions.value },
        get isStartupInProgress() { return isStartupInProgress.value },
        get inStartupPhase() { return inStartupPhase.value },
        get rootSessions() { return rootSessions.value },
        get getScopeSessionList() { return scopeSessionList.value },
        get hasUnreadContentMemo() { return hasUnreadContentMemo.value },
        get archivedProjectIds() { return archivedProjectIds.value },
        get worktreeIdsByParent() { return worktreeIdsByParent.value },
        // Like the real getter: the project badges read the unread index through it.
        get getProjectActivitySummary() { return projectActivitySummary.value },
    }

    const getWorkspaceById = computed(() => (id) => state.workspaces.find(w => w.id === id) || null)
    const workspaceContainsProject = computed(() => (workspaceId, projectId) => {
        const ws = getWorkspaceById.value(workspaceId)
        if (!ws || !projectId) return false
        if (ws.projectIds.includes(projectId)) return true
        const mainRepoId = data.getMainRepoProjectId(projectId)
        return mainRepoId !== projectId && ws.projectIds.includes(mainRepoId)
    })
    // Like `stores/workspaces.js`: one membership per store, each parameter read on each evaluation.
    const membership = createWorkspaceMembership({
        getWorkspaceById: (id) => getWorkspaceById.value(id),
        getProject: (id) => getProject.value(id),
        getWorktreeIdsByParent: () => worktreeIdsByParent.value,
        isShowArchived: () => state.settings.isShowArchivedProjects,
    })
    const getVisibleProjectIds = computed(() => (workspaceId) => membership.visible(workspaceId))
    const getAllProjectIds = computed(() => (workspaceId) => membership.all(workspaceId))
    const workspaces = {
        get getAllWorkspaces() { return state.workspaces },
        get getWorkspaceById() { return getWorkspaceById.value },
        get getVisibleProjectIds() { return getVisibleProjectIds.value },
        get getAllProjectIds() { return getAllProjectIds.value },
        get workspaceContainsProject() { return workspaceContainsProject.value },
    }
    return { data, workspaces }
}
