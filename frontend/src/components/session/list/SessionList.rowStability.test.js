// SessionList.vue on the shipped sidebar derivations (design:
// docs/plans/2026-10-28-sidebar-recompute-cost-design.md, 3.4, 5.2, 5.4, 5.6, 7.1 #8).
//
// The SFC is mounted with the shipped modules (block stages, scope lists,
// retain helpers, project scopes, real `ws:` workspace ids) over the store
// facades of `tests/helpers/sidebarFixture.js`: the data facade has the real
// `getProjectScopeIds` (it iterates `state.projects` for `worktree_of`), the
// activity-sorted baseline `getProjects` / `getWorktreesOf` and the shipped
// getters; the workspace facade delegates to `createWorkspaceMembership`.
// A `SessionListItem` stub counts its renders per session id. Like the real
// row, it reads its session's title, so a title change re-renders that row.
import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { compileComponent, makeRenderer, virtualScroller, row, flush } from '../../../../tests/helpers/scrollerComponentHarness.js'
import { buildSidebarState, tick, FIXTURE_IDS, createShippedData } from '../../../../tests/helpers/sidebarFixture.js'
import { ALL_PROJECTS_ID } from '../../../constants.js'
import * as sidebarSessions from '../../../utils/sidebarSessions.js'
import * as sessionLists from '../../../utils/sessionLists.js'
import * as retain from '../../../utils/retain.js'
import * as projectScopes from '../../../utils/projectScopes.js'
import * as workspaceIds from '../../../utils/workspaceIds.js'
import * as sessionTree from '../../../utils/sessionTree.js'
import * as sessions from '../../../utils/sessions.js'
import * as textFilter from '../../../utils/textFilter.js'
import * as sessionTitle from '../../../utils/sessionTitle.js'
import * as scrollerLoadWindow from '../../../utils/scrollerLoadWindow.js'

const ID = FIXTURE_IDS
const ROW_PROPS = ['session', 'active', 'highlighted', 'compactView', 'showProviderIcon', 'showProjectName',
    'scopeProjectIds', 'filterProjectId', 'treeRow', 'canDetachFromTree']

/** A fixture with pagination cutoffs (`hasMoreSessions: true` on every cursor). */
const fixture = () => buildSidebarState({ roots: 60, cutoffRoots: 40 })

/** Project ids of a fixture scope (the visible members of `w` and their worktrees, or a project and its worktrees). */
function scopeProjectSet(state, projectId) {
    if (projectId === 'ws:w') return new Set(['p0', 'p1'])
    return new Set([projectId, ...Object.values(state.projects).filter(p => p.worktree_of === projectId).map(p => p.id)])
}

/** The root records outside the scope `projectId`. */
function outOfScopeRoots(state, projectId) {
    const inScope = scopeProjectSet(state, projectId)
    return Object.values(state.sessions).filter(s => !s.parent_session_id && !inScope.has(s.project_id))
}

/** Replace `field` of the raw record by a counting accessor. */
function countReads(record, field) {
    const raw = Vue.toRaw(record)
    let value = raw[field]
    const counter = { reads: 0 }
    Object.defineProperty(raw, field, {
        configurable: true, enumerable: true,
        get() { counter.reads++; return value },
        set(next) { value = next },
    })
    return counter
}

const total = (counters) => counters.reduce((sum, counter) => sum + counter.reads, 0)

/** Mount SessionList on `state` in the scope `projectId`. */
function mountList(t, state, projectId) {
    const { data, workspaces } = createShippedData(state)
    // The data facade plus the list's own store needs. A load never settles: these tests do not paginate.
    const store = Object.defineProperties({
        hasMoreSessions: id => state.localState.projects[id]?.hasMoreSessions ?? true,
        areSessionsLoading: id => state.localState.projects[id]?.sessionsLoading ?? false,
        loadSessions: () => new Promise(() => {}),
        setDisplayedSessionIds() {},
        $onAction() {},
    }, Object.getOwnPropertyDescriptors(data))

    const renders = new Map()
    const rowProps = []
    const SessionListItem = {
        props: ROW_PROPS,
        emits: ['toggle-tree', 'select', 'drop-data', 'selection-click'],
        setup(props) {
            rowProps.push(props)
            return () => {
                renders.set(props.session.id, (renders.get(props.session.id) ?? 0) + 1)
                return Vue.h('div', props.session.title)
            }
        },
    }

    const savedRAF = [globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame]
    globalThis.requestAnimationFrame = () => 0
    globalThis.cancelAnimationFrame = () => {}

    const noMotion = { itemClass() {}, itemStyle() {}, noteLive() {}, dropLive() {}, holdTarget() {}, isArriving: () => false }
    const component = compileComponent(new URL('./SessionList.vue', import.meta.url), {
        'vue-router': { useRoute: () => ({ query: {} }) },
        '../../../stores/data': { useDataStore: () => store, ALL_PROJECTS_ID },
        '../../../stores/workspaces': { useWorkspacesStore: () => workspaces },
        '../../../stores/sessionSelection': { useSessionSelectionStore: () => ({ active: false }) },
        '../../../stores/sessionTreeExpansion': { useSessionTreeExpansionStore: () => ({ isExpanded: () => true, reconcile() {}, toggle() {}, reveal: () => [] }) },
        '../../../utils/workspaceIds': workspaceIds,
        '../../../utils/sidebarSessions': sidebarSessions,
        '../../../utils/sessionTree.js': sessionTree,
        '../../../utils/sessions': sessions,
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
    const props = Vue.reactive({ projectId, showArchivedProjects: false, showActiveAcrossFilters: true })
    const app = renderer.createApp({ setup: () => () => Vue.h(component, props) })
    app.mount(root)
    t.after(() => {
        app.unmount()
        for (const [index, name] of ['requestAnimationFrame', 'cancelAnimationFrame'].entries()) {
            if (savedRAF[index] === undefined) delete globalThis[name]
            else globalThis[name] = savedRAF[index]
        }
    })
    return {
        data, renders, rowProps,
        renderCount: () => [...renders.values()].reduce((sum, count) => sum + count, 0),
        resetRenders: () => renders.clear(),
    }
}

/**
 * Positive control of the render counter: at least one counted render at
 * mount, then a title change on one visible row's session re-renders that row
 * once (and no other row). Leaves the counter reset.
 */
async function assertRowsCanRender(state, view, label) {
    assert.ok(view.renderCount() >= 1, `${label}: at least one row renders at mount`)
    const id = [...view.renders.keys()][0]
    view.resetRenders()
    state.sessions[id].title = `${state.sessions[id].title} (renamed)`
    await flush()
    assert.equal(view.renders.get(id), 1, `${label}: the renamed row re-renders once`)
    assert.equal(view.renderCount(), 1, `${label}: no other row re-renders on a title change`)
    view.resetRenders()
}

test('an out-of-scope reorder of a session without a process reads no root field in workspace and main-repository scopes', async t => {
    const reads = {}
    for (const projectId of ['ws:w', 'p2']) {
        const state = fixture()
        mountList(t, state, projectId)
        await flush()
        // The ticked session is already unread (fixture), has no process and is out of the scope:
        // the tick moves it to the top of the store order without an unread flip.
        const ticked = state.sessions[ID.unread1]
        assert.ok(!scopeProjectSet(state, projectId).has(ticked.project_id), `${projectId}: ticked session out of scope`)
        // The baseline refilter of the all-projects list reads `project_id` first.
        const counters = outOfScopeRoots(state, projectId).map(root => countReads(root, 'project_id'))
        tick(state, ID.unread1, { top: true })
        await flush()
        reads[projectId] = total(counters)
    }
    assert.deepEqual(reads, { 'ws:w': 0, p2: 0 }, 'no out-of-scope root project_id read, per scope')
})

test('adding an unrelated project keeps the scope project ids and re-renders no row in project scope', async t => {
    const state = fixture()
    const view = mountList(t, state, 'p0')
    await flush()
    await assertRowsCanRender(state, view, 'p0')
    const before = view.rowProps.map(props => props.scopeProjectIds)
    assert.deepEqual(before[0], ['p0'])
    state.projects.p99 = { id: 'p99', name: 'Project p99', directory: '/fixture/p99', mtime: 1, archived: false, worktree_of: null }
    await flush()
    assert.equal(view.renderCount(), 0, 'p0: no row re-renders')
    assert.deepEqual(view.rowProps.map(props => props.scopeProjectIds), before, 'p0: same scope project ids')
    view.rowProps.forEach((props, i) => assert.equal(props.scopeProjectIds, before[i], 'p0: scope project ids keep their identity'))
})

test('an in-scope reorder in the mounted list reads no out-of-scope root field', async t => {
    const reads = {}
    for (const projectId of ['p0', 'ws:w']) {
        const state = fixture()
        // Own setup, before mount: the ticked in-scope session (no process) is unread, so the tick does not flip it.
        const target = state.sessions[ID.p0Second]
        target.last_viewed_at = new Date(Date.parse(target.last_new_content_at) - 5000).toISOString()
        mountList(t, state, projectId)
        await flush()
        // The baseline cross-filter scans read `pinned` first on every root.
        const counters = outOfScopeRoots(state, projectId).map(root => countReads(root, 'pinned'))
        tick(state, ID.p0Second, { top: true })
        await flush()
        reads[projectId] = total(counters)
    }
    assert.deepEqual(reads, { p0: 0, 'ws:w': 0 }, 'no out-of-scope root pinned read, per scope')
})

// Guard (already passes after Tasks 5 and 6): design 7.1 #8.
test('a project activity change re-renders no row in project, workspace and all-projects scopes', async t => {
    for (const projectId of ['p0', 'ws:w', ALL_PROJECTS_ID]) {
        const state = fixture()
        const view = mountList(t, state, projectId)
        await flush()
        await assertRowsCanRender(state, view, projectId)
        const before = view.rowProps.map(props => props.scopeProjectIds)
        const order = view.data.getProjects.map(p => p.id).join(',')
        // A real activity change: p1 takes the top of the activity order, the p2 worktrees swap.
        const top = Math.max(...Object.values(state.projects).map(p => p.mtime))
        state.projects.p1.mtime = top + 100
        state.projects['p2-wt1'].mtime = top + 50
        await flush()
        assert.notEqual(view.data.getProjects.map(p => p.id).join(','), order, `${projectId}: the activity order changed`)
        view.rowProps.forEach((props, i) => assert.equal(props.scopeProjectIds, before[i], `${projectId}: scope project ids keep their identity`))
        assert.equal(view.renderCount(), 0, `${projectId}: no row re-renders`)
    }
})
