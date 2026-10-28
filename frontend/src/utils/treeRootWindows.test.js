// The order of tree roots among active entries (design:
// docs/plans/2026-10-28-tree-root-active-order-design.md, section 6).
//
// Each test builds its own reactive `{ sessions, processStates }` state and its
// tracker (`getTreeRootWindows`), before any step: one tracker per state. The
// tracker updates once per reactive flush, so every step is followed by
// `await flush()`. Root orders come from `buildSessionTree` with the tracker's
// `rootKey`, the way the sidebar and the rail build their trees.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { computed, effect, nextTick, reactive, toRaw } from 'vue'
import { buildSessionTree } from './sessionTree.js'
import { sessionSortComparator } from './sessionSort.js'
import { getTreeRootWindows, nextRootWindows, collectProcessInputs, processAncestry, NO_ROOT_WINDOWS } from './treeRootWindows.js'
import { observeStageRuns } from './stageRuns.js'
import { buildSessionTree as baselineBuildSessionTree } from '../../tests/helpers/sidebarBaseline/sessionTree.js'
import { computeSidebarSessionBlocks } from './sidebarSessions.js'
import { isSessionUnread } from './sessions.js'
import { buildSidebarState, createShippedData, applyStep, FIXTURE_IDS, FIXTURE_SCOPES,
    FIXTURE_UPDATE_SEQUENCE } from '../../tests/helpers/sidebarFixture.js'

const flush = () => nextTick()

// Real agent start times are epoch seconds; T(n) keeps the numbers readable.
const T = n => 1_760_000_000 + n

/**
 * A small world: `session(id, fields)` adds a loaded row, `start(id, t)` a live
 * process (assistant_turn), `stop(id)` removes it. `tree(ids)` builds the tree of
 * the candidates `ids` with the tracker's `rootKey`; `today(ids)` without it.
 */
function world() {
    const state = reactive({ sessions: {}, processStates: {} })
    const tracker = getTreeRootWindows(state)
    const lookup = id => state.sessions[id] ?? null
    const session = (id, fields = {}) => {
        state.sessions[id] = { id, project_id: 'p', mtime: 100, spawned_by: null, tree_detached: false,
            hidden: false, parent_session_id: null, pinned: null, ...fields }
        return state.sessions[id]
    }
    const start = (id, startedAt, fields = {}) => {
        state.processStates[id] = { state: 'assistant_turn', project_id: 'p', started_at: startedAt,
            spawn_ancestors: [], ...fields }
    }
    const stop = id => { delete state.processStates[id] }
    const build = (ids, rootKey) => buildSessionTree(ids.map(id => state.sessions[id]), {
        lookup, processStates: state.processStates, rootKey,
    })
    const tree = ids => build(ids, tracker.rootKey)
    const today = ids => build(ids, null)
    const activeRoots = t => t.rootIds.filter(id => t.nodes.get(id).effective.active)
    return { state, tracker, session, start, stop, tree, today, activeRoots, key: id => tracker.rootKey(id) }
}

/** Leader `L` (no own process) with workers below it, and an independent active root `R`. */
function leaderWorld({ workers = ['A', 'B'], rStartedAt = T(150) } = {}) {
    const w = world()
    w.session('L')
    for (const id of workers) w.session(id, { spawned_by: 'L' })
    w.session('R')
    w.start('R', rStartedAt)
    return { ...w, ids: ['L', ...workers, 'R'] }
}

// --- 1. A worker start or restart does not move the leader ---

test('1. leader root inactive, worker A active: starting B and restarting A keep the root position', async () => {
    const w = leaderWorld()
    w.start('A', T(100))
    await flush()
    assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['R', 'L'], 'initial: R (150) above L (100)')
    assert.equal(w.key('L'), T(100))

    w.start('B', T(200))
    await flush()
    // Positive control: today's order moves L to the top (max start below it).
    assert.deepEqual(w.activeRoots(w.today(w.ids)), ['L', 'R'], 'today the root jumps')
    assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['R', 'L'], 'B starts: no move')

    // Restart A: a stop, then a start with a new started_at, in separate flushes.
    w.stop('A')
    await flush()
    w.start('A', T(300))
    await flush()
    assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['R', 'L'], 'A restarts: no move')
    // Restart in place (reconnect path): the entry stays, its started_at changes.
    w.state.processStates.A.started_at = T(400)
    await flush()
    assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['R', 'L'], 'A restarts in place: no move')
    assert.equal(w.key('L'), T(100))
})

// --- 2. The root's own process starts while a worker runs ---

test('2. the root own process starts while a worker runs: no move', async () => {
    const w = leaderWorld()
    w.start('A', T(100))
    await flush()
    w.start('L', T(300))
    await flush()
    assert.deepEqual(w.activeRoots(w.today(w.ids)), ['L', 'R'], 'today the root jumps')
    assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['R', 'L'])
    assert.equal(w.key('L'), T(100))
})

// --- 3. Window chain ---

test('3. window chain: A starts, B starts, A stops: the key stays the start of A', async () => {
    const w = leaderWorld()
    w.start('A', T(100))
    await flush()
    w.start('B', T(200))
    await flush()
    w.stop('A')
    await flush()
    assert.equal(w.key('L'), T(100))
    // The smallest live start is now B's: the key is history, not the snapshot.
    assert.equal(Math.min(...[...w.tracker.current().windows.get('L').held.values()]), T(200))
    assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['R', 'L'])
})

// --- 4. A new window after a gap ---

test('4. nothing lives below the root, then a process starts: a new window with the new key', async () => {
    const w = leaderWorld()
    w.start('A', T(100))
    await flush()
    w.stop('A')
    await flush()
    assert.equal(w.key('L'), undefined, 'no window')
    assert.equal(w.tree(w.ids).nodes.get('L').effective.active, false)
    w.start('B', T(400))
    await flush()
    assert.equal(w.key('L'), T(400))
    assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['L', 'R'], 'the root moves with its new window')
})

// --- 5. A single session has the key of flat mode ---

test('5. a session holding only its own identity has the flat-mode key, fresh after each restart', async () => {
    const w = world()
    w.session('S')
    w.session('R')
    w.start('R', T(300))
    w.start('S', T(100))
    await flush()
    const ids = ['S', 'R']
    const flat = () => ids.map(id => w.state.sessions[id]).sort(sessionSortComparator(w.state.processStates)).map(s => s.id)
    assert.equal(w.key('S'), T(100))
    assert.deepEqual(w.tree(ids).rootIds, flat())
    assert.deepEqual(w.tree(ids).rootIds, ['R', 'S'])

    w.stop('S')
    await flush()
    w.start('S', T(500))
    await flush()
    assert.equal(w.key('S'), T(500), 'stop and start: fresh key')
    assert.deepEqual(w.tree(ids).rootIds, flat())
    assert.deepEqual(w.tree(ids).rootIds, ['S', 'R'])

    w.state.processStates.S.started_at = T(200)
    await flush()
    assert.equal(w.key('S'), T(200), 'restart in place: fresh key')
    assert.deepEqual(w.tree(ids).rootIds, flat())
    assert.deepEqual(w.tree(ids).rootIds, ['R', 'S'])
})

// --- 7. Ancestry ---

test('7a. a worker whose own row is not loaded joins its leader through spawn_ancestors at once', async () => {
    const w = leaderWorld({ workers: ['A'] })
    w.start('A', T(100))
    await flush()
    // W starts before its row is loaded: its first link is the first entry of spawn_ancestors.
    w.start('W', T(200), { spawn_ancestors: ['L', 'G'] })
    await flush()
    assert.deepEqual([...w.tracker.current().windows.get('L').held.keys()].sort(), ['A', 'W'], 'L holds W at once')
    // A stops while the row of W is still loading: W carries the window.
    w.stop('A')
    await flush()
    assert.equal(w.key('L'), T(100), 'no window break before the row loads')
    w.session('W', { spawned_by: 'L' })
    await flush()
    assert.equal(w.key('L'), T(100), 'no window break when the row loads')
    assert.equal(w.tree(['L', 'W', 'R']).nodes.get('W').parentId, 'L')
    assert.deepEqual(w.activeRoots(w.tree(['L', 'W', 'R'])), ['R', 'L'])
})

test('7b. a hidden intermediate parent, never loaded or loaded hidden, ends the chain', async () => {
    const w = leaderWorld({ workers: [] })
    // H is a hidden parent of W below L: it never loads (the client never lists hidden rows).
    w.session('W', { spawned_by: 'H' })
    w.start('W', T(100), { spawn_ancestors: ['H', 'L'] })
    // V's own row is not loaded: its first link is H, which is not loaded either.
    w.start('V', T(110), { spawn_ancestors: ['H', 'L'] })
    await flush()
    assert.equal(w.key('L'), undefined, 'L holds neither W nor V')
    assert.deepEqual(processAncestry('W', w.state.processStates.W, id => w.state.sessions[id] ?? null), [])
    assert.deepEqual(processAncestry('V', w.state.processStates.V, id => w.state.sessions[id] ?? null), [])
    // Positive control: a loaded, visible H links W (and V, through its first link) to L.
    w.session('H', { spawned_by: 'L' })
    await flush()
    assert.equal(w.key('L'), T(100))
    // A loaded hidden H ends the chain, as in the tree.
    w.state.sessions.H.hidden = true
    await flush()
    assert.equal(w.key('L'), undefined)
    const t = w.tree(['L', 'W', 'R'])
    assert.equal(t.nodes.get('W').parentId, null, 'the tree does not link W to L either')
})

test('7c. an unloaded intermediate link ends the chain; once loaded the tracker follows the tree, detached included', async () => {
    const w = leaderWorld({ workers: [] })
    w.session('W', { spawned_by: 'M' })
    w.start('W', T(100), { spawn_ancestors: ['M', 'L'] })
    await flush()
    assert.equal(w.key('L'), undefined, 'M not loaded: L does not hold W')
    assert.equal(w.tree(['L', 'W', 'R']).nodes.get('W').parentId, null)

    w.session('M', { spawned_by: 'L' })
    await flush()
    assert.equal(w.key('L'), T(100), 'M loaded: L holds W')
    assert.equal(w.key('M'), T(100), 'M holds W too')
    assert.equal(w.tree(['L', 'W', 'R']).nodes.get('W').parentId, 'L', 'the tree links W to L through M')

    w.state.sessions.M.tree_detached = true
    await flush()
    assert.equal(w.key('L'), undefined, 'a detached M ends the chain')
    assert.equal(w.key('M'), T(100), 'M still holds W')
    assert.equal(w.tree(['L', 'W', 'R']).nodes.get('W').parentId, null)
})

// --- 8. Override frame ---

test('8. a STARTING override frame with a monotonic started_at never becomes the leader key', async () => {
    const OVERRIDE = 5123.25 // event-loop clock seconds, not an epoch time
    for (const deadFirst of [false, true]) {
        const label = deadFirst ? 'after a dead frame' : 'over the live state'
        const w = leaderWorld({ workers: ['W1', 'W2'] })
        const keys = []
        const step = async (apply) => { apply(); await flush(); keys.push(w.key('L')) }
        await step(() => w.start('W1', T(100)))
        if (deadFirst) await step(() => w.stop('W1'))
        await step(() => w.start('W1', OVERRIDE, { state: 'starting' }))
        await step(() => w.start('W2', T(200)))
        await step(() => w.start('W1', T(300)))
        assert.ok(!keys.includes(OVERRIDE), `${label}: never the override (${keys})`)
        assert.equal(w.key('L'), deadFirst ? T(200) : T(100), label)
        assert.equal(w.key('W1'), T(300), `${label}: W1's own key is its real start`)
    }
})

// --- 9. Real reparent / detach ---

test('9. a detach or a reparent that moves the live processes out from below the root opens a new window', async () => {
    for (const move of ['detach', 'reparent']) {
        const w = leaderWorld({ workers: ['W1', 'W2'] })
        w.session('M')
        w.start('W1', T(100))
        await flush()
        assert.equal(w.key('L'), T(100), move)
        // W1 leaves L and W2 starts below L, in one flush: none of the held identities is still held.
        if (move === 'detach') w.state.sessions.W1.tree_detached = true
        else w.state.sessions.W1.spawned_by = 'M'
        w.start('W2', T(200))
        await flush()
        assert.equal(w.key('L'), T(200), `${move}: new window`)
        if (move === 'reparent') assert.equal(w.key('M'), T(100), 'M opens a window with W1')
        const ids = ['L', 'W1', 'W2', 'R', 'M']
        assert.deepEqual(w.activeRoots(w.tree(ids)).slice(0, 2), ['L', 'R'], `${move}: L moves above R (150)`)
        // In separate flushes: the root without live processes below it has no window.
        w.stop('W2')
        await flush()
        assert.equal(w.key('L'), undefined, `${move}: no window`)
    }
})

// --- 10. Live retry chain ---

test('10. live retry chain: the remaining process goes STARTING (null) and back: the key does not change', async () => {
    const w = leaderWorld()
    w.start('A', T(100))
    await flush()
    w.start('B', T(200))
    await flush()
    w.stop('A')
    await flush()
    assert.equal(w.key('L'), T(100), 'the key comes from A, which stopped')
    // ApiError retry: setProcessState(STARTING) with no extra (started_at null) over the live B.
    w.start('B', null, { state: 'starting' })
    await flush()
    assert.equal(w.key('L'), T(100), 'during the retry')
    assert.equal(w.key('B'), T(200), 'B keeps its own key during the retry')
    w.start('B', T(200))
    await flush()
    assert.equal(w.key('L'), T(100), 'after the retry')
    assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['R', 'L'])
})

// --- 11. Restart in place ---

test('11. restart in place and a stop plus a start in one flush are gaps', async () => {
    const w = leaderWorld({ workers: ['A', 'B'] })
    w.start('L', T(100))
    await flush()
    w.state.processStates.L.started_at = T(500)
    await flush()
    assert.equal(w.key('L'), T(500), 'own restart in place: new key')
    w.stop('L')
    w.start('A', T(100))
    await flush()
    assert.equal(w.key('L'), T(100), 'a stop and a start of different processes in one flush: new window')
    w.state.processStates.A.started_at = T(600)
    await flush()
    assert.equal(w.key('L'), T(600), 'the only live descendant restarts in place: new key')
    w.stop('A')
    w.start('B', T(50))
    await flush()
    assert.equal(w.key('L'), T(50), 'stop and start in one flush, below the root')
    assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['R', 'L'])
})

// --- 12. Optimistic start ---

test('12. a fresh optimistic STARTING (null) then the real started_at orders the root by the real value', async () => {
    for (const shape of ['single', 'leader']) {
        const w = leaderWorld({ workers: ['W'] })
        const target = shape === 'single' ? 'L' : 'W'
        w.start(target, null, { state: 'starting' })
        await flush()
        assert.equal(w.key('L'), undefined, `${shape}: no window before the real start`)
        assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['R', 'L'], `${shape}: today's key (0) meanwhile`)
        w.start(target, T(400))
        await flush()
        assert.equal(w.key('L'), T(400), shape)
        assert.deepEqual(w.activeRoots(w.tree(w.ids)), ['L', 'R'], `${shape}: ordered by the real start`)
    }
})

// --- 13. No rerun of a tree build without a window change of its own roots ---

test('13. the tracker reruns no tree build without a window change of its active roots', async () => {
    const w = leaderWorld({ workers: ['A', 'X'] })
    // Own setup: A runs below L; R runs; X (below L) is not a candidate; Q is outside the tree.
    w.state.sessions.X.project_id = 'q'
    w.session('Q', { project_id: 'q' })
    w.state.projects = { p: { id: 'p', mtime: 1 } }
    w.start('A', T(100))
    await flush()
    const candidates = ['L', 'A', 'R']
    let builds = 0
    const tree = computed(() => { builds++; return w.tree(candidates) })
    let dirty = false
    const runner = effect(() => tree.value, { scheduler() { dirty = true } })
    const stages = []
    const stopObserving = observeStageRuns(name => stages.push(name))
    const measure = async (apply) => {
        const before = builds
        stages.length = 0
        apply()
        await flush()
        if (dirty) { dirty = false; runner() }
        return { builds: builds - before, updates: stages.filter(n => n === 'treeRoots:update').length,
            inputs: stages.filter(n => n === 'treeRoots:inputs').length }
    }

    const activeTick = await measure(() => { w.state.sessions.A.mtime += 1; w.state.processStates.A.memory = 5 })
    assert.equal(activeTick.builds, 0, 'active-session tick')
    const userTurn = await measure(() => {
        w.state.processStates.A.state = 'user_turn'
        w.state.processStates.A.state_changed_at = T(120)
    })
    assert.equal(userTurn.builds, 0, 'user_turn transition')
    assert.ok(userTurn.inputs >= 1, 'the tracker inputs reran on the state change (probe can fail)')
    const userTurnTick = await measure(() => { w.state.sessions.A.mtime += 1 })
    assert.equal(userTurnTick.builds, 0, 'user_turn tick')
    const activity = await measure(() => { w.state.projects.p.mtime += 100 })
    assert.equal(activity.builds, 0, 'project activity change')
    const outside = await measure(() => w.start('Q', T(300)))
    assert.equal(outside.builds, 0, 'a window opens outside the tree')
    assert.equal(outside.updates, 1, 'the tracker updated once for it')
    assert.equal(w.key('Q'), T(300))
    const outsideClose = await measure(() => w.stop('Q'))
    assert.equal(outsideClose.builds, 0, 'a window closes outside the tree')

    // Positive control: a window of an active root of this tree opens through a non-candidate
    // descendant only (X is not read by the build), so only the key reruns the build.
    const own = await measure(() => {
        w.stop('A')
        w.start('L', null, { state: 'starting' })
    })
    assert.ok(own.builds >= 1, 'A stops: the build reruns (process presence)')
    assert.equal(w.key('L'), undefined)
    const keyOnly = await measure(() => w.start('X', T(250)))
    assert.equal(keyOnly.builds, 1, 'L opens a window: the build reruns once')
    assert.equal(w.key('L'), T(250))
    assert.deepEqual(w.activeRoots(tree.value), ['L', 'R'])
    stopObserving()
    runner.effect.stop()
})

test('13. a window change of an inactive root of the tree reruns no tree build', async () => {
    const w = world()
    // Own setup: X (below L) is not a candidate, so L is an inactive root of the tree; R runs.
    w.session('L')
    w.session('X', { spawned_by: 'L', project_id: 'q' })
    w.session('R')
    w.start('R', T(150))
    await flush()
    let builds = 0
    const tree = computed(() => { builds++; return w.tree(['L', 'R']) })
    let dirty = false
    const runner = effect(() => tree.value, { scheduler() { dirty = true } })
    const measure = async (apply) => {
        const before = builds
        apply()
        await flush()
        if (dirty) { dirty = false; runner() }
        return builds - before
    }
    assert.equal(await measure(() => w.start('X', T(200))), 0, 'the window of L opens')
    assert.equal(w.key('L'), T(200), 'the window is open (probe)')
    assert.equal(tree.value.nodes.get('L').effective.active, false)
    assert.equal(await measure(() => w.stop('X')), 0, 'the window of L closes')
    // Positive control: R, an active root of the tree, restarts in place.
    assert.ok(await measure(() => { w.state.processStates.R.started_at = T(300) }) >= 1, 'R restarts: the build reruns')
    runner.effect.stop()
})

// --- 15. Everything else equals today's output ---

/** The comparable shape of a tree: every field the sidebar reads, `dateKey` of inactive nodes only. */
function treeShape(tree, { withRootOrder = true } = {}) {
    const nodes = {}
    for (const [id, node] of tree.nodes) {
        const { pinned, active, activeKey, dateKey } = node.effective
        nodes[id] = {
            parentId: node.parentId, childIds: node.childIds, hintParent: node.hintParent?.id ?? null,
            pinned, active, activeKey, dateKey: active ? 'active' : dateKey,
            descendantIds: node.descendantIds, triggerIds: node.triggerIds,
        }
    }
    return withRootOrder ? { rootIds: tree.rootIds, nodes } : { nodes }
}

const blockOptions = (data, workspaces, effectiveProjectId) => ({
    data, workspaces, effectiveProjectId, activeWorkspaceId: null, sessionId: null,
    showArchived: false, showArchivedProjects: false, showActiveAcrossFilters: true, showAsTree: true,
})

// The fixture scopes that show the p0 sessions with a process.
const P0_SCOPES = ['p0', 'ws:w', 'ws:w1', FIXTURE_SCOPES[4]]

const processRecord = (state, id, fields) => ({
    session_id: id, project_id: toRaw(state.sessions[id]).project_id, pending_requests: [], ...fields,
})

// Extra steps of section 6 #15: a stop and restart, and a fresh optimistic STARTING (not a live retry).
const OWN_IDENTITY_STEPS = [
    { name: 'stop before restart', apply: state => { delete state.processStates[FIXTURE_IDS.processShell] } },
    { name: 'restart', apply: state => {
        state.processStates[FIXTURE_IDS.processShell] = processRecord(state, FIXTURE_IDS.processShell,
            { state: 'user_turn', started_at: 500 })
    } },
    { name: 'fresh optimistic STARTING', apply: state => {
        state.processStates[FIXTURE_IDS.p3Recent] = processRecord(state, FIXTURE_IDS.p3Recent,
            { state: 'starting', started_at: null })
    } },
    { name: 'real start after the optimistic STARTING', apply: state => {
        state.processStates[FIXTURE_IDS.p3Recent] = processRecord(state, FIXTURE_IDS.p3Recent,
            { state: 'assistant_turn', started_at: 50 })
    } },
]

test('15. own identities only: every tree equals the baseline tree, root order included, along the sequence', async () => {
    for (const scope of FIXTURE_SCOPES) {
        const state = buildSidebarState({ roots: 60, cutoffRoots: 40 })
        const { data, workspaces } = createShippedData(state)
        const tracker = getTreeRootWindows(state)
        let compared = 0
        const check = (label) => {
            const { natural } = computeSidebarSessionBlocks(blockOptions(data, workspaces, scope))
            const lookup = id => state.sessions[id] ?? null
            const opts = { lookup, processStates: state.processStates, viewedId: FIXTURE_IDS.p0Second, isUnread: isSessionUnread }
            const expected = baselineBuildSessionTree(natural, opts)
            const actual = buildSessionTree(natural, { ...opts, rootKey: tracker.rootKey })
            assert.deepEqual(treeShape(actual), treeShape(expected), `${String(scope)}: ${label}`)
            compared += actual.rootIds.filter(id => actual.nodes.get(id).effective.active).length
        }
        await flush()
        check('initial')
        for (const step of [...FIXTURE_UPDATE_SEQUENCE, ...OWN_IDENTITY_STEPS]) {
            applyStep(state, step)
            await flush()
            check(step.name)
        }
        // Probe that can fail: active roots were ordered by their window key along the way.
        if (P0_SCOPES.includes(scope)) assert.ok(compared > 0, `${String(scope)}: active roots compared`)
        assert.equal(tracker.rootKey(FIXTURE_IDS.processShell), 500, `${String(scope)}: the tracker was live`)
    }
})

test('15. with trees: effective fields, child order and triggerIds equal the baseline in every scope', async () => {
    for (const scope of FIXTURE_SCOPES) {
        const state = buildSidebarState({ roots: 60, cutoffRoots: 40 })
        const { data, workspaces } = createShippedData(state)
        const ID = FIXTURE_IDS
        // Own setup: chains with processes below idle ancestors, and a worker start below a leader.
        state.sessions[ID.processAssistant].spawned_by = ID.p0Recent
        state.sessions[ID.processShell].spawned_by = ID.p0Recent
        state.sessions[ID.processCron].spawned_by = ID.p0Second
        // `child` (below `ancestor`) runs since 250: between the two starts below p0-recent (200, 300),
        // so the window key (200) and today's key (300) order p0-recent on either side of it.
        state.processStates[ID.child] = processRecord(state, ID.child, { state: 'user_turn', started_at: 250 })
        const tracker = getTreeRootWindows(state)
        let reordered = false
        const check = (label) => {
            const { natural } = computeSidebarSessionBlocks(blockOptions(data, workspaces, scope))
            const opts = { lookup: id => state.sessions[id] ?? null, processStates: state.processStates, isUnread: isSessionUnread }
            const expected = baselineBuildSessionTree(natural, opts)
            const actual = buildSessionTree(natural, { ...opts, rootKey: tracker.rootKey })
            assert.deepEqual(treeShape(actual, { withRootOrder: false }), treeShape(expected, { withRootOrder: false }),
                `${String(scope)}: ${label}`)
            assert.deepEqual([...actual.rootIds].sort(), [...expected.rootIds].sort(), `${String(scope)}: ${label}: same roots`)
            reordered ||= actual.rootIds.join() !== expected.rootIds.join()
        }
        await flush()
        check('initial')
        const steps = [
            ...FIXTURE_UPDATE_SEQUENCE,
            // The assistant worker restarts later than everything: today p0-recent jumps.
            { name: 'worker restart', apply: s => { s.processStates[ID.processAssistant].started_at = 900 } },
            { name: 'second worker start', apply: s => {
                s.processStates[ID.unread2] = processRecord(s, ID.unread2, { state: 'assistant_turn', started_at: 950 })
                s.sessions[ID.unread2].spawned_by = ID.p0Second
            } },
        ]
        for (const step of steps) {
            applyStep(state, step)
            await flush()
            check(step.name)
        }
        if (P0_SCOPES.includes(scope)) assert.ok(reordered, `${String(scope)}: the root order differs at least once (probe)`)
    }
})

// --- Pure update ---

test('nextRootWindows: the key of a window that opens over several identities', () => {
    const inputs = [
        { id: 'a', starting: false, startedAt: 30, chain: ['x'] },
        { id: 'b', starting: false, startedAt: 10, chain: ['x'] },
        { id: 'x', starting: false, startedAt: 20, chain: [] },
        { id: 'c', starting: true, startedAt: null, chain: ['y'] },
    ]
    const next = nextRootWindows(NO_ROOT_WINDOWS, inputs)
    assert.equal(next.windows.get('x').key, 20, 'X holds its own identity: its own start')
    assert.equal(next.windows.has('y'), false, 'a fresh STARTING holds nothing')
    const withoutOwn = nextRootWindows(NO_ROOT_WINDOWS, inputs.filter(entry => entry.id !== 'x'))
    assert.equal(withoutOwn.windows.get('x').key, 10, 'otherwise the smallest held start')
})

test('collectProcessInputs skips synthetic states and drops the STARTING started_at', () => {
    const sessions = { a: { id: 'a', spawned_by: 'p' }, p: { id: 'p' } }
    const inputs = collectProcessInputs({
        a: { state: 'starting', started_at: 99 },
        s: { state: 'assistant_turn', started_at: 5, synthetic: true },
        p: { state: 'user_turn', started_at: 7 },
    }, id => sessions[id] ?? null)
    assert.deepEqual(inputs, [
        { id: 'a', starting: true, startedAt: null, chain: ['p'] },
        { id: 'p', starting: false, startedAt: 7, chain: [] },
    ])
})
