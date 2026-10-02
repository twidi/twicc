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
    const savedRAF = [globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame]
    const pendingRAF = new Map(), scrollerInputs = [], scrollerAPIs = [], updates = []
    let nextRAF = 0
    globalThis.requestAnimationFrame = callback => { pendingRAF.set(++nextRAF, callback); return nextRAF }
    globalThis.cancelAnimationFrame = handle => pendingRAF.delete(handle)
    const actualScroller = virtualScroller()
    const setupScroller = actualScroller.setup
    actualScroller.setup = (props, context) => {
        scrollerInputs.push(props)
        return setupScroller(props, { ...context,
            expose(value) { scrollerAPIs.push(value); context.expose(value) },
            emit(name, value) { if (name === 'update') updates.push(value); context.emit(name, value) },
        })
    }
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
        '../../../utils/sidebarSessions': { computeSidebarSessionBlocks: ({ showArchived }) => ({ extra: null, crossFilterPinned: [], crossFilterActive: [], natural: Object.values(sessions).filter(session => showArchived || !session.archived) }) },
        '../../../utils/textFilter': { matchQuery: (query, value) => value?.includes(query) },
        '../../../utils/datePresets': { dateBucketSeparator: () => ({ key: 'old', entry: { label: 'Older' } }) },
        '../../../composables/useListCascade': { useListCascade: () => noMotion },
        '../../../composables/useListExit': { useListExit: ({ items }) => ({ displayItems: items, exitClass() {}, exitStyle() {} }) },
        '../../../composables/useGlideInk': { useGlideInk() {} },
        '../../../utils/sidebarRows': { activeRowBase() {}, entranceOffset() {}, revealBands() {}, revealMargins() {} },
        '../../../utils/scrollerLoadWindow.js': helpers,
        '../../virtual-scroller/VirtualScroller.vue': actualScroller,
        './SessionListItem.vue': row, '../../sidebar/SidebarListSeparator.vue': row,
    })
    if (initialLoading) store.loadSessions('p')
    const app = renderer.createApp({ setup: () => () => Vue.h(Vue.KeepAlive, () => active.value ? Vue.h(component, props) : null) })
    app.mount(root); t.after(() => {
        app.unmount()
        for (const [index, name] of ['requestAnimationFrame', 'cancelAnimationFrame'].entries()) {
            if (savedRAF[index] === undefined) delete globalThis[name]
            else globalThis[name] = savedRAF[index]
        }
    })
    async function finish({ cursor = state.oldestSessionMtime, id, title = 'other', more = true, fail = false } = {}) {
        const request = requests.at(-1)
        if (fail) request.reject(new Error('network'))
        else request.resolve({ sessions: id ? [{ id, title, project_id: 'p', mtime: cursor }] : [], has_more: more })
        await flush()
    }
    return { props, active, calls, requests, root, state, store, sessions, app, finish, updates,
        range: () => scrollerAPIs.at(-1).getVisibleRange(),
        displayedIds: () => scrollerInputs.at(-1).items.map(session => session.id),
        async scroll(top) {
            const viewport = descendants(root, n => n.props.class?.split(' ').includes('virtual-scroller'))[0]
            viewport.scrollTop = top
            viewport.props.onScrollPassive({ type: 'scroll', target: viewport })
            const batch = [...pendingRAF]; pendingRAF.clear()
            for (const [, callback] of batch) callback(0)
            await flush()
        },
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


function matchingSessions(count) {
    return Object.fromEntries(Array.from({ length: count }, (_, index) => {
        const id = `session-${index}`
        return [id, { id, title: `match ${index}`, project_id: 'p', mtime: 100 }]
    }))
}
for (const opportunity of ['range', 'membership', 'equal-index scroll']) {
    test(`successful no-progress waits for an independent ${opportunity} opportunity`, async t => {
        const v = mount(t, { initialSessions: matchingSessions(6) }); await flush()
        assert.equal(v.calls.length, 1)
        await v.finish(); await flush()
        assert.equal(v.calls.length, 1, 'successful no-progress cannot self-loop')
        assert.equal(descendants(v.root, n => n.type === 'wa-callout' && n.props.variant === 'danger').length, 0,
            'successful no-progress is not a fetch failure')
        const beforeRange = { ...v.range() }, beforeUpdates = v.updates.length
        if (opportunity === 'range') {
            await v.scroll(70)
            assert.notDeepEqual(v.range(), beforeRange)
        } else if (opportunity === 'membership') {
            v.sessions.new = { id: 'new', title: 'filtered-out', project_id: 'p', mtime: 100 }
            await flush()
            assert.deepEqual(v.range(), beforeRange, 'canonical membership can change without displayed range movement')
        } else {
            await v.scroll(1)
            assert.deepEqual(v.range(), beforeRange)
            assert.equal(v.updates.length, beforeUpdates, 'native scroll uses the equal-range event path')
        }
        assert.equal(v.calls.length, 2, 'one independent opportunity starts one next request')
        await v.finish(); await flush()
        assert.equal(v.calls.length, 2, 'second no-progress settlement cannot self-loop')
        await v.scroll(v.scrollers()[0].scrollTop)
        assert.equal(v.calls.length, 2, 'an unchanged scroll position is not a new scroll opportunity')
    })
}
test('real page rejection remains latched across external opportunities until explicit retry', async t => {
    t.mock.method(console, 'error', () => {})
    const v = mount(t, { initialSessions: matchingSessions(6) }); await flush()
    await v.finish({ fail: true })
    assert.equal(v.calls.length, 1)
    await v.scroll(70)
    v.sessions.new = { id: 'new', title: 'match new', project_id: 'p', mtime: 100 }
    await flush(); await v.scroll(71)
    assert.equal(v.calls.length, 1)
    assert.equal(descendants(v.root, n => n.type === 'wa-callout' && n.props.variant === 'danger').length, 1)
    v.retry(); await flush(); assert.equal(v.calls.length, 2)
    await v.finish({ more: false })
})
test('30 matching sessions shrinking to 12 requests one page without query or range changes', async t => {
    const v = mount(t, { initialSessions: matchingSessions(30), height: 140 }); await flush()
    assert.equal(v.calls.length, 0)
    const beforeRange = { ...v.range() }, beforeUpdates = v.updates.length
    const beforeIds = Object.keys(v.sessions), beforeCursor = v.state.oldestSessionMtime
    assert.equal(beforeRange.end, 3)
    assert.equal(v.displayedIds().length, 30)
    for (let index = 0; index < 18; index++) v.sessions[`session-${index}`].title = `excluded ${index}`
    await flush()
    assert.equal(v.displayedIds().length, 12)
    assert.equal(12 - beforeRange.end, 9)
    assert.deepEqual(v.range(), beforeRange)
    assert.equal(v.updates.length, beforeUpdates, 'stable geometry emits no replacement event')
    assert.equal(v.props.searchQuery, 'match')
    assert.deepEqual(Object.keys(v.sessions), beforeIds)
    assert.equal(v.state.oldestSessionMtime, beforeCursor)
    assert.equal(v.calls.length, 1, 'changed filtered membership supplies the missing opportunity')
    await v.finish(); await flush()
    assert.equal(v.calls.length, 1, 'no-progress completion does not retry itself')
    v.sessions['session-18'].title = 'match changed title'
    v.sessions['session-19'].annotations = { edited: true }
    await flush()
    assert.equal(v.calls.length, 1, 'same ordered displayed membership cannot unlock no-progress')
    v.sessions['session-18'].title = 'excluded later'
    await flush(); assert.equal(v.calls.length, 2)
    await v.finish({ more: false })
})


test('a successful same-ID title replacement cannot turn filtered membership into a self-loop', async t => {
    const v = mount(t, { initialSessions: matchingSessions(6) }); await flush()
    assert.equal(v.calls.length, 1)
    await v.finish({ cursor: 100, id: 'session-0', title: 'excluded' })
    await flush()
    assert.equal(v.displayedIds().length, 5)
    assert.equal(v.calls.length, 1, 'a response without canonical progress stops after its own membership change')
    v.sessions['session-1'].title = 'excluded independently'
    await flush(); assert.equal(v.calls.length, 2)
    await v.finish({ more: false })
})
test('archive-driven filtered membership supplies the same independent threshold opportunity', async t => {
    const v = mount(t, { initialSessions: matchingSessions(30) }); await flush()
    assert.equal(v.calls.length, 0)
    const beforeRange = { ...v.range() }, beforeIds = Object.keys(v.sessions)
    for (let index = 0; index < 18; index++) v.sessions[`session-${index}`].archived = true
    await flush()
    assert.equal(v.displayedIds().length, 12)
    assert.deepEqual(v.range(), beforeRange)
    assert.deepEqual(Object.keys(v.sessions), beforeIds)
    assert.equal(v.calls.length, 1)
    await v.finish(); assert.equal(v.calls.length, 1)
    v.sessions['session-18'].annotations = { archivedReason: 'unrelated' }
    await flush(); assert.equal(v.calls.length, 1)
})


test('actual delayed ResizeObserver measurement enables pagination after an initial zero-height mount', async t => {
    const observers = [], previous = globalThis.ResizeObserver
    globalThis.ResizeObserver = class {
        constructor(callback) { this.callback = callback; observers.push(this) }
        observe(target) { this.target = target }
        disconnect() {}
    }
    t.after(() => {
        if (previous === undefined) delete globalThis.ResizeObserver
        else globalThis.ResizeObserver = previous
    })
    const v = mount(t, { height: 0 }); await flush()
    assert.equal(v.calls.length, 0)
    const viewport = observers.find(observer => observer.target?.props.class?.includes('virtual-scroller'))
    assert.ok(viewport, 'the actual VirtualScroller observes its container')
    viewport.target.clientHeight = 421
    viewport.callback([{ contentRect: { height: 421 } }])
    await flush()
    assert.equal(v.calls.length, 1, 'the first delayed real measurement permits one current-scope page')
    await v.finish(); await flush(); assert.equal(v.calls.length, 1)
})
