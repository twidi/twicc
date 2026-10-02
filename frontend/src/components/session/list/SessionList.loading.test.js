import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { readFileSync } from 'node:fs'
import { compileComponent, makeRenderer, virtualScroller, row, flush, deferred, descendants } from '../../../../tests/helpers/scrollerComponentHarness.js'
import * as helpers from '../../../utils/scrollerLoadWindow.js'
// Execute the production request registration, merge, loading flag, and finally ordering.
const storeSource = readFileSync(new URL('../../../stores/data.js', import.meta.url), 'utf8')
const storeStart = storeSource.indexOf('        async loadSessions(')
const storeEnd = storeSource.indexOf('        /**\n         * Load all "sticky"', storeStart)
const createStoreActions = new Function('sessionsLoadInFlight', 'syncBaseline', 'isWorkspaceProjectId',
    `return { ${storeSource.slice(storeStart, storeEnd)} }`)
function mount(t, { initialLoading = false, height = 140, more = true, initialSessions = {} } = {}) {
    const { renderer, root } = makeRenderer(height)
    const props = Vue.reactive({ projectId: 'p', searchQuery: 'match' })
    const active = Vue.ref(true), calls = [], requests = []
    const state = Vue.reactive({ sessionsLoading: false, sessionsFetched: true, hasMoreSessions: more, oldestSessionMtime: 100 })
    const sessions = Vue.reactive(initialSessions)
    const localState = Vue.reactive({ projects: { p: state, q: { sessionsLoading: false, sessionsFetched: true, hasMoreSessions: true, oldestSessionMtime: 50 } }, sessions: {} })
    const store = {
        sessions, processStates: {}, localState,
        get getAllSessions() { return Object.values(sessions) }, getProjectSessions: id => Object.values(sessions).filter(s => s.project_id === id), getProjectScopeIds: id => [id],
        hasMoreSessions: id => localState.projects[id]?.hasMoreSessions ?? true, areSessionsLoading: id => localState.projects[id]?.sessionsLoading ?? false,
        setDisplayedSessionIds() {}, $onAction() {},
        _ensureProjectLocalState: id => localState.projects[id],
        _fetchSessionsPage(id) {
            calls.push(id)
            const request = deferred(); requests.push(request)
            return request.promise
        },
        ...createStoreActions(new Map(), { sessionMtime: (_, local) => local }, id => id.startsWith('workspace:')),

    }
    const noMotion = { itemClass() {}, itemStyle() {}, noteLive() {}, dropLive() {}, hold() {}, release() {}, clear() {} }
    const component = compileComponent(new URL('./SessionList.vue', import.meta.url), {
        'vue-router': { useRoute: () => ({ query: {} }) },
        '../../../stores/data': { useDataStore: () => store, ALL_PROJECTS_ID: '__all__' },
        '../../../stores/workspaces': { useWorkspacesStore: () => ({ getVisibleProjectIds: () => ['p'] }) },
        '../../../stores/sessionSelection': { useSessionSelectionStore: () => ({}) },
        '../../../utils/workspaceIds': { isWorkspaceProjectId: id => id.startsWith('workspace:'), extractWorkspaceId: id => id.slice(10) },
        '../../../utils/sidebarSessions': { computeSidebarSessionBlocks: () => ({ extra: null, crossFilterPinned: [], crossFilterActive: [], natural: Object.values(sessions) }) },
        '../../../utils/textFilter': { matchQuery: (query, value) => value?.includes(query) },
        '../../../utils/datePresets': { dateBucketSeparator: () => ({ key: 'old', entry: { label: 'Older' } }) },
        '../../../composables/useListCascade': { useListCascade: () => noMotion },
        '../../../composables/useListExit': { useListExit: ({ items }) => ({ displayItems: items, exitClass() {}, exitStyle() {} }) },
        '../../../composables/useGlideInk': { useGlideInk() {} },
        '../../../utils/sidebarRows': { activeRowBase() {}, entranceOffset() {}, revealBands() {}, revealMargins() {} },
        '../../../utils/scrollerLoadWindow.js': helpers,
        '../../virtual-scroller/VirtualScroller.vue': virtualScroller(),
        './SessionListItem.vue': row, '../../sidebar/SidebarListSeparator.vue': row,
    })
    if (initialLoading) store.loadSessions('p')
    const app = renderer.createApp({ setup: () => () => Vue.h(Vue.KeepAlive, () => active.value ? Vue.h(component, props) : null) })
    app.mount(root); t.after(() => app.unmount())
    async function finish({ cursor = state.oldestSessionMtime, id, title = 'other', more = true, fail = false } = {}) {
        const request = requests.at(-1)
        if (fail) request.reject(new Error('network'))
        else request.resolve({ sessions: id ? [{ id, title, project_id: 'p', mtime: cursor }] : [], has_more: more })
        await flush()
    }
    return { props, active, calls, requests, root, state, store, sessions, app, finish,
        scrollers: () => descendants(root, n => n.props.class?.split(' ').includes('virtual-scroller')),
        retry: () => descendants(root, n => n.type === 'wa-button').find(n => n.props.onClick)?.props.onClick(),
    }
}
test('empty filtered pages keep the actual viewport and chain through cursor progress', async t => {
    const v = mount(t); await flush()
    assert.equal(v.scrollers().length, 1); assert.equal(v.calls.length, 1)
    await v.finish({ cursor: 90, id: 'a' }); assert.equal(v.scrollers().length, 1); assert.equal(v.calls.length, 2)
    await v.finish({ cursor: 80, id: 'b', title: 'match', more: false })
    assert.equal(v.calls.length, 2); assert.equal(v.scrollers().length, 1)
})
test('empty no-progress response stops and exposes retry', async t => {
    const v = mount(t); await flush(); assert.equal(v.calls.length, 1)
    await v.finish(); assert.equal(v.calls.length, 1)
    assert.ok(descendants(v.root, n => n.type === 'wa-button').length)
    v.retry(); await flush(); assert.equal(v.calls.length, 2)
    await v.finish({ more: false }); assert.equal(v.scrollers().length, 0)
})
test('empty failure exposes retry without looping', async t => {
    t.mock.method(console, 'error', () => {})
    const v = mount(t); await flush(); assert.equal(v.calls.length, 1); await v.finish({ fail: true })
    assert.equal(v.calls.length, 1); assert.ok(descendants(v.root, n => n.type === 'wa-button').length)
})
test('hidden settlement cannot chain; activation can retry current scope', async t => {
    const v = mount(t); await flush(); assert.equal(v.calls.length, 1)
    v.active.value = false; await flush(); await v.finish({ cursor: 90, id: 'a' })
    assert.equal(v.calls.length, 1)
    v.active.value = true; await flush(); assert.equal(v.calls.length, 2)
    await v.finish({ more: false })
})
test('unmounted settlement cannot chain', async t => {
    const v = mount(t); await flush(); assert.equal(v.calls.length, 1)
    v.app.unmount(); await v.finish({ cursor: 90, id: 'a' }); assert.equal(v.calls.length, 1)
})
test('unmeasured viewport never starts pagination', async t => {
    const v = mount(t, { height: 0 }); await flush(); assert.equal(v.calls.length, 0)
})

test('external initial request keeps its start snapshot through false-before-settlement', async t => {
    const v = mount(t, { initialLoading: true }); await flush()
    assert.equal(v.calls.length, 1)
    await v.finish({ cursor: 90, id: 'a' })
    assert.equal(v.calls.length, 2, 'joining the initial request must retain cursor progress')
    await v.finish({ more: false })
})
test('filter change during await revokes old chaining and permits a current evaluation', async t => {
    const v = mount(t); await flush(); assert.equal(v.calls.length, 1)
    v.props.searchQuery = 'another'; await flush()
    await v.finish({ cursor: 90, id: 'a' })
    assert.equal(v.calls.length, 2)
    await v.finish({ more: false })
})

test('external load start cannot reenter the store before in-flight registration', async t => {
    const v = mount(t, { more: false, initialSessions: { a: { id: 'a', title: 'match', project_id: 'p', mtime: 100 } } })
    await flush(); assert.equal(v.calls.length, 0)
    const external = v.store.loadSessions('p', { force: true })
    assert.equal(v.calls.length, 1, 'the loading=true watcher must not start a second transport request')
    await flush(); await v.finish({ cursor: 90, id: 'b' }); await external
    assert.equal(v.calls.length, 2, 'after real settlement the current scope can fetch the next page')
    await v.finish({ more: false })
})

test('DOM hides before ResizeObserver delivery: settlement cannot chain', async t => {
    const v = mount(t); await flush(); assert.equal(v.calls.length, 1)
    v.scrollers()[0].clientHeight = 0
    await v.finish({ cursor: 90, id: 'a' }); assert.equal(v.calls.length, 1)
})

test('an unmeasured external baseline cannot become another project baseline', async t => {
    const v = mount(t, { initialLoading: true, height: 0 }); await flush()
    v.props.projectId = 'q'; await flush()
    const nextViewport = v.scrollers()[0]
    v.active.value = false; await flush()
    nextViewport.clientHeight = 140
    v.active.value = true; await flush()
    assert.deepEqual(v.calls, ['p', 'q'])
    await v.finish()
    assert.deepEqual(v.calls, ['p', 'q'], 'an empty q page has no progress against q cursor 50')
    v.requests[0].resolve({ sessions: [], has_more: false }); await flush()
})
