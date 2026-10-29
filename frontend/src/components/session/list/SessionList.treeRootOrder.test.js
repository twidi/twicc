// Tree root order among active entries, through the sidebar list and the rail
// (design: docs/plans/2026-10-28-tree-root-active-order-design.md, section 6 #6).
//
// SessionList.vue is mounted on the shipped sidebar modules over the store
// facades of `tests/helpers/sidebarFixture.js`. A wrapper of `buildSessionTree`
// keeps the last tree the list built; a wrapper of `getTreeRootWindows` counts
// the `rootKey` reads per id. The rail runs `useRailActiveSessions` on the same
// state, so both read the one tracker of that state.
import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { compileComponent, makeRenderer, virtualScroller, row, flush } from '../../../../tests/helpers/scrollerComponentHarness.js'
import { buildSidebarState, FIXTURE_IDS, createFixtureStores } from '../../../../tests/helpers/sidebarFixture.js'
import { ALL_PROJECTS_ID } from '../../../constants.js'
import * as sidebarSessions from '../../../utils/sidebarSessions.js'
import * as sessionLists from '../../../utils/sessionLists.js'
import * as retain from '../../../utils/retain.js'
import * as projectScopes from '../../../utils/projectScopes.js'
import * as workspaceIds from '../../../utils/workspaceIds.js'
import * as sessionTree from '../../../utils/sessionTree.js'
import * as unreadMemos from '../../../utils/unreadMemos.js'
import * as treeRootWindows from '../../../utils/treeRootWindows.js'
import * as textFilter from '../../../utils/textFilter.js'
import * as sessionTitle from '../../../utils/sessionTitle.js'
import * as scrollerLoadWindow from '../../../utils/scrollerLoadWindow.js'
import { useRailActiveSessions } from '../../../composables/useRailActiveSessions.js'

const ID = FIXTURE_IDS
const LEADER = ID.p0Recent       // p0, no own process
const W1 = ID.p1Recent           // p1, below the leader
const W2 = 'worker-2'            // p1, below the leader
const GRAND = 'grand-leader'     // p2 (outside w): the spawner of the leader in variant (d) child
const W1_START = 150             // between proc-shell (200) and proc-cron (100)
const W2_START = 250             // between proc-assistant (300) and proc-shell (200)
// The active roots of the workspace tree when the user never leaves it: the leader keeps W1's start.
const EXPECTED = [ID.processAssistant, ID.processShell, LEADER, ID.processCron]

const processRecord = (state, id, startedAt) => ({
    session_id: id, project_id: Vue.toRaw(state.sessions[id]).project_id, state: 'assistant_turn',
    started_at: startedAt, pending_requests: [], spawn_ancestors: [LEADER],
})

/**
 * The fixture with the leader and its two workers in another project of the workspace `w`
 * (own setup, before the tracker and the mounts): W1 runs since 150, W2 has no process yet.
 */
function handOffFixture({ grandLeader = false } = {}) {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 40 })
    const w1 = state.sessions[W1]
    w1.spawned_by = LEADER
    state.sessions[W2] = { ...Vue.toRaw(w1), id: W2, title: 'Session worker-2', mtime: w1.mtime - 1 }
    if (grandLeader) {
        state.sessions[GRAND] = { ...Vue.toRaw(state.sessions[LEADER]), id: GRAND, project_id: 'p2',
            title: 'Session grand-leader', mtime: state.sessions[LEADER].mtime - 2, spawned_by: null }
        state.sessions[LEADER].spawned_by = GRAND
    }
    state.processStates[W1] = processRecord(state, W1, W1_START)
    return state
}

function stubAnimationFrames(t) {
    const saved = ['requestAnimationFrame', 'cancelAnimationFrame'].map(name => [name, name in globalThis, globalThis[name]])
    globalThis.requestAnimationFrame = () => 0
    globalThis.cancelAnimationFrame = () => {}
    t.after(() => {
        for (const [name, present, value] of saved) {
            if (present) globalThis[name] = value
            else delete globalThis[name]
        }
    })
}

/** Mount SessionList on `state` with reactive `props`; returns the last tree and the rootKey reads. */
function mountList(t, state, props) {
    const { data, workspaces } = createFixtureStores(state)
    const store = Object.defineProperties({
        hasMoreSessions: id => state.localState.projects[id]?.hasMoreSessions ?? true,
        areSessionsLoading: id => state.localState.projects[id]?.sessionsLoading ?? false,
        loadSessions: () => new Promise(() => {}),
        setDisplayedSessionIds() {},
        $onAction() {},
    }, Object.getOwnPropertyDescriptors(data))

    const view = { tree: null, reads: new Map() }
    const trees = { ...sessionTree, buildSessionTree: (...args) => (view.tree = sessionTree.buildSessionTree(...args)) }
    const windows = {
        ...treeRootWindows,
        getTreeRootWindows(owner) {
            const tracker = treeRootWindows.getTreeRootWindows(owner)
            return { ...tracker, rootKey: id => { view.reads.set(id, (view.reads.get(id) ?? 0) + 1); return tracker.rootKey(id) } }
        },
    }
    const SessionListItem = {
        props: ['session', 'active', 'highlighted', 'compactView', 'showProviderIcon', 'showProjectName',
            'scopeProjectIds', 'filterProjectId', 'treeRow', 'canDetachFromTree'],
        emits: ['toggle-tree', 'select', 'drop-data', 'selection-click'],
        setup: p => () => Vue.h('div', p.session.title),
    }
    const noMotion = { itemClass() {}, itemStyle() {}, noteLive() {}, dropLive() {}, holdTarget() {}, isArriving: () => false }
    const component = compileComponent(new URL('./SessionList.vue', import.meta.url), {
        'vue-router': { useRoute: () => ({ query: {} }) },
        '../../../stores/data': { useDataStore: () => store, ALL_PROJECTS_ID },
        '../../../stores/workspaces': { useWorkspacesStore: () => workspaces },
        '../../../stores/sessionSelection': { useSessionSelectionStore: () => ({ active: false }) },
        '../../../stores/sessionTreeExpansion': { useSessionTreeExpansionStore: () => ({ isExpanded: () => true, reconcile() {}, toggle() {}, reveal: () => [] }) },
        '../../../utils/workspaceIds': workspaceIds,
        '../../../utils/sidebarSessions': sidebarSessions,
        '../../../utils/sessionTree.js': trees,
        '../../../utils/treeRootWindows.js': windows,
        '../../../utils/unreadMemos.js': unreadMemos,
        '../../../utils/sessionLists.js': sessionLists,
        '../../../utils/retain.js': retain,
        '../../../utils/projectScopes.js': projectScopes,
        '../../../utils/textFilter': textFilter,
        '../../../utils/datePresets': { dateBucketSeparator: () => ({ key: 'old', entry: { label: 'Older' } }) },
        '../../../composables/useSessionTreeAncestors': { useSessionTreeAncestors() {} },
        '../../../composables/useListCascade': { useListCascade: () => noMotion },
        '../../../composables/useListExit': { useListExit: ({ items }) => ({ displayItems: items, exitClass() {}, exitStyle() {} }) },
        '../../../composables/useGlideInk': { useGlideInk() {} },
        '../../../composables/useSidebarScrollbar': { useSidebarScrollbar() {} },
        '../../../utils/sidebarRows': { activeRowBase() {}, entranceOffset() {}, revealBands() {}, revealMargins() {} },
        '../../../utils/scrollerLoadWindow.js': scrollerLoadWindow,
        '../../../utils/sessionTitle': sessionTitle,
        '../../virtual-scroller/VirtualScroller.vue': virtualScroller(),
        './SessionListItem.vue': SessionListItem,
        '../../sidebar/SidebarListSeparator.vue': row,
    })
    const { renderer, root } = makeRenderer(140)
    const app = renderer.createApp({ setup: () => () => Vue.h(component, props) })
    app.mount(root)
    t.after(() => app.unmount())
    return view
}

/** The rail on the same state: tree mode follows `flags.tree`. */
function mountRail(t, state, flags) {
    const { data } = createFixtureStores(state)
    const store = Object.defineProperties({ loadSessionById: () => Promise.resolve(null) }, Object.getOwnPropertyDescriptors(data))
    const scope = Vue.effectScope()
    const api = scope.run(() => useRailActiveSessions(store, () => null, { treeEnabled: () => flags.tree }))
    t.after(() => scope.stop())
    // The root of each rail group, in rail order (a group's rows are consecutive).
    return { groups: () => [...new Set(api.rows.value.map(r => r.groupId))] }
}

const activeRoots = tree => tree.rootIds.filter(id => tree.nodes.get(id).effective.active)

// What the user leaves the workspace tree for, and comes back from.
const VARIANTS = [
    { name: '(a) a narrower scope that does not show the leader', leave: p => { p.projectId = 'p2' }, noReads: true },
    { name: '(b) a search query', leave: p => { p.searchQuery = 'zzz' }, noReads: true },
    { name: '(c) tree mode off', leave: p => { p.showAsTree = false }, noReads: true },
    { name: '(d) the leader is an inactive entry of the shown tree', leave: p => { p.projectId = 'p0' },
        leaderShown: 'inactive' },
    { name: '(d) the leader is a child entry of the shown tree', grandLeader: true,
        leave: p => { p.projectId = ALL_PROJECTS_ID }, leaderShown: 'child' },
]

const back = p => { p.projectId = 'ws:w'; p.searchQuery = ''; p.showAsTree = true }

test('6. hidden hand-off: back in the workspace tree, the leader keeps the start of W1, in the sidebar and the rail', async t => {
    stubAnimationFrames(t)
    for (const variant of [{ name: 'control: the user never leaves', leave: () => {} }, ...VARIANTS]) {
        const state = handOffFixture({ grandLeader: variant.grandLeader })
        // Own setup: the tracker exists before the first process change, like `initDataStore`.
        const tracker = treeRootWindows.getTreeRootWindows(state)
        const props = Vue.reactive({ projectId: 'ws:w', searchQuery: '', showAsTree: true,
            showArchivedProjects: false, showActiveAcrossFilters: true })
        const view = mountList(t, state, props)
        const railFlags = Vue.reactive({ tree: true })
        const rail = mountRail(t, state, railFlags)
        await flush()
        assert.deepEqual(activeRoots(view.tree), EXPECTED, `${variant.name}: initial order`)
        assert.ok(view.reads.get(LEADER) > 0, `${variant.name}: the workspace tree reads the leader key (probe)`)

        variant.leave(props)
        railFlags.tree = variant.name.startsWith('control')
        await flush()
        if (variant.leaderShown === 'inactive') {
            assert.equal(view.tree.nodes.get(LEADER)?.effective.active, false, `${variant.name}: leader shown inactive`)
        }
        if (variant.leaderShown === 'child') {
            assert.equal(view.tree.nodes.get(LEADER)?.parentId, GRAND, `${variant.name}: leader shown as a child`)
        }
        view.reads.clear()
        // The hand-off, in separate flushes: W2 starts, then W1 stops.
        state.processStates[W2] = processRecord(state, W2, W2_START)
        await flush()
        delete state.processStates[W1]
        await flush()
        if (!variant.name.startsWith('control')) {
            assert.equal(view.reads.get(LEADER) ?? 0, 0, `${variant.name}: the leader key is not read during the hand-off`)
            if (variant.noReads) assert.equal(view.reads.size, 0, `${variant.name}: the tracker is not read at all`)
        }

        back(props)
        railFlags.tree = true
        await flush()
        assert.equal(tracker.rootKey(LEADER), W1_START, `${variant.name}: leader key`)
        assert.deepEqual(activeRoots(view.tree), EXPECTED, `${variant.name}: sidebar order`)
        // The rail tree agrees with the sidebar. The rail is not scoped: its root of the leader's
        // tree is the grand leader when there is one (it holds the same windows). proc-cron (cron
        // only) has no rail row.
        const railRoot = variant.grandLeader ? GRAND : LEADER
        assert.equal(tracker.rootKey(railRoot), W1_START, `${variant.name}: rail root key`)
        const railGroups = rail.groups()
        assert.deepEqual(railGroups, [ID.processAssistant, ID.processShell, railRoot], `${variant.name}: rail order`)
    }
})
