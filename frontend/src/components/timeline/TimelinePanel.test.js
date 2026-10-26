import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import test from 'node:test'
import { createPinia } from 'pinia'
import * as Vue from 'vue'

import { useTimelineFiltersStore } from '../../stores/timelineFilters.js'
import { compileComponent, deferred, descendants, flush, makeRenderer, virtualScroller } from '../../../tests/helpers/scrollerComponentHarness.js'
import { createFeedState, createTimelineFeed } from '../../utils/timeline/feed.js'
import * as liveEventsModule from '../../utils/timeline/liveEvents.js'
import * as providerLabels from '../../utils/timeline/providerLabels.js'
import * as eventTimeModule from '../../utils/timeline/eventTime.js'
import * as filtersModule from '../../utils/timeline/filters.js'
import * as rowsModule from '../../utils/timeline/rows.js'
import * as scopeModule from '../../utils/timeline/scope.js'
import * as scrollKeeperModule from '../../utils/timeline/scrollKeeper.js'

// The panel binds window events when it mounts: count the listeners to see them removed.
const windowListeners = new Map()
globalThis.window = new EventTarget()
for (const [method, step] of [['addEventListener', 1], ['removeEventListener', -1]]) {
    const original = window[method].bind(window)
    window[method] = (type, listener, options) => {
        windowListeners.set(type, (windowListeners.get(type) ?? 0) + step)
        original(type, listener, options)
    }
}
const TIMELINE_EVENT_TYPES = ['twicc:timeline-changed', 'twicc:ws-reconnected', 'twicc:ws-first-open']
const listenerCounts = () => Object.fromEntries(TIMELINE_EVENT_TYPES.map((type) => [type, windowListeners.get(type) ?? 0]))

const RouterLink = { props: ['to'], setup: (props, { slots }) => () => Vue.h('a', { 'data-to': JSON.stringify(props.to) }, slots.default?.()) }
const stub = (tag, textProp) => ({ props: ['projectId', 'label', 'row', 'dayKey'], setup: (props) => () => Vue.h(tag, { 'data-id': props.row?.key ?? props.projectId }, textProp ? props[textProp] : undefined) })

// The two shared pieces of the Stats structure, real (only the navigation list itself is a stub).
const TabNavList = compileComponent(new URL('../project/ProjectTabNavList.vue', import.meta.url), { './ProjectDetailNavList.vue': stub('nav') })
const StickyBand = compileComponent(new URL('../ui/StickyBand.vue', import.meta.url))

/**
 * The filter band (TimelineFilterBand.vue has its own tests): its props, and the calls of its exposed focus
 * methods, in `log` (`[name, the store's view at the call]`).
 */
function createFilterBand(log, filtersStore) {
    return {
        props: ['pageId', 'active'],
        setup(props, { expose }) {
            expose({
                focusFiltersButton: () => log.push(['focusFiltersButton', filtersStore.filtersFor(props.pageId).view]),
                focusSessionPill: () => log.push(['focusSessionPill']),
            })
            return () => Vue.h('div', { class: 'filter-band', 'data-page-id': props.pageId, 'data-active': String(props.active) }, 'Timeline')
        },
    }
}

/**
 * The event row (TimelineEventRow.vue has its own tests): an `article` with the row key and the session filter it is
 * given; `onToggle(payload)` is its `toggle-session` emit.
 */
const EventRow = {
    props: ['row', 'context', 'sessionFilterId'],
    emits: ['toggle-session'],
    setup: (props, { emit }) => () => Vue.h('article', {
        'data-id': props.row.key,
        'data-session-filter': props.sessionFilterId,
        onToggle: (payload) => emit('toggle-session', payload),
    }),
}

const isoAt = (n) => new Date(Date.UTC(2026, 9, 14, 10, 0, n)).toISOString().replace('.000Z', '.000000Z')
const at = (n) => `2026-10-14T10:00:${String(n).padStart(2, '0')}.000000Z`
const event = (id, fields = {}) => ({ id, kind: 'turn.started', occurred_at: at(id), session_id: 's1', project_id: 'p1', provider: 'codex', actor: 'agent', precision: 'exact', data: {}, ...fields })

/** A scroller double: renders every item and records what the panel asks of it. */
function createFakeScroller() {
    // `measured`: keys the scroller already measured (it answers their height). `mounts`: how many scrollers
    // were created. `emit(range)`: the `update` event of the real scroller.
    const log = { scrollTop: 0, anchorIndex: 1, calls: [], measured: new Set(), mounts: 0, range: { start: 0, end: 0 }, emit: null, emitVisible: null }
    const component = {
        props: ['items', 'itemKey'],
        emits: ['update', 'became-visible'],
        setup(props, { slots, expose, emit }) {
            log.mounts += 1
            let rendered = []
            const cache = new Map()
            log.emit = (range) => {
                log.range = { start: range.visibleStartIndex, end: range.visibleEndIndex }
                emit('update', range)
            }
            log.emitVisible = () => emit('became-visible')
            expose({
                // The 4th element tells whether the row was already RENDERED when the panel seeded it (the panel seeds after the render).
                // Like the real scroller, a seeded height is in the cache and answered back (per scroller); a
                // measured key answers its measure (30).
                seedItemHeight: (key, height) => {
                    log.calls.push(['seed', key, height, rendered.includes(key)])
                    cache.set(key, height)
                },
                getItemHeight: (key) => (log.measured.has(key) ? 30 : cache.get(key)),
                getVisibleRange: () => log.range,
                getScrollState: () => ({ scrollTop: log.scrollTop }),
                // The first visible row is the one at `anchorIndex` of what is RENDERED NOW: asked before the DOM
                // moved it names the old row, asked after it names whatever sits there then.
                getScrollAnchor: () => (rendered[log.anchorIndex] === undefined
                    ? null : { key: rendered[log.anchorIndex], index: log.anchorIndex, offset: 7 }),
                // Every rendered row is 30px tall.
                get positions() { return rendered.map((key, index) => ({ key, top: index * 30, height: 30 })) },
                // A restore also records the rows rendered at that moment: before the render they are the old ones.
                scrollToAnchor: (anchor) => log.calls.push(['anchor', anchor, [...rendered]]),
                scrollToTop: () => log.calls.push(['top']),
            })
            return () => {
                rendered = props.items.map((item) => item.key)
                // Like the real scroller, the height of a key that left the list is dropped.
                for (const key of cache.keys()) if (!rendered.includes(key)) cache.delete(key)
                return Vue.h('div', { class: 'fake-scroller' }, [slots.before?.(), ...props.items.map((item) => slots.default({ item }))])
            }
        },
    }
    return { log, component }
}

/**
 * `pinia`: share the filters memory between two fixtures (two panels of the app). `filtersStore` is the real store
 * of that memory; `filterActions` records every action called on it (`[name, ...args]`).
 */
function fixture({ events = [event(3), event(2), event(1)], hasMore = false, projectsLoaded = true, compact = false, projects = { p1: { id: 'p1' } }, workspaces = {}, fakeScroller = false, pinia = createPinia() } = {}) {
    const calls = []
    const state = Vue.reactive(createFeedState())
    const server = {
        fail: false,
        respond: (params) => ({
            events: params.before ? [] : events, has_more: params.before ? false : hasMore, truncated: false,
            latest_id: events.length ? events[0].id : null, sessions: { s1: { title: 'My session', project_id: 'p1', provider: 'codex', hidden: false, archived: false } },
        }),
    }
    const feed = createTimelineFeed({
        state,
        fetchPage: async (params) => {
            calls.push({ ...params })
            if (server.fail) throw new Error('boom')
            return server.respond(params)
        },
    })
    const dataStore = Vue.reactive({
        projectsLoaded, projects,
        getProject: (id) => dataStore.projects[id] ?? null,
        getProjectScopeIds: (id) => [id],
        getSession: () => undefined,
    })
    const workspacesStore = Vue.reactive({
        workspaces,
        getWorkspaceById: (id) => workspacesStore.workspaces[id] ?? null,
        getAllProjectIds: (id) => workspacesStore.workspaces[id].projectIds,
    })
    const compactHeight = Vue.ref(compact)
    const scroller = fakeScroller ? createFakeScroller() : null
    const filtersStore = useTimelineFiltersStore(pinia)
    const filterActions = []
    filtersStore.$onAction(({ name, args }) => { if (name !== 'filtersFor' && name !== 'ensure') filterActions.push([name, ...args]) })
    const bandCalls = []
    const Panel = compileComponent(new URL('./TimelinePanel.vue', import.meta.url), {
        'vue-router': { RouterLink },
        '../../composables/useTimelineFeed': { useTimelineFeed: () => feed },
        '../../stores/data': { ALL_PROJECTS_ID: '__all__', useDataStore: () => dataStore },
        '../../stores/workspaces': { useWorkspacesStore: () => workspacesStore },
        '../../providers': { getProviderHelpers: () => null },
        '../../utils/compactHeight': { compactHeight },
        '../../stores/timelineFilters': { useTimelineFiltersStore: () => filtersStore },
        '../../utils/timeline/filters': filtersModule,
        '../../utils/timeline/rows': rowsModule,
        '../../utils/timeline/eventTime': eventTimeModule,
        '../../utils/timeline/liveEvents': liveEventsModule,
        '../../utils/timeline/scrollKeeper': scrollKeeperModule,
        '../../utils/timeline/providerLabels': providerLabels,
        '../../utils/timeline/scope': scopeModule,
        '../project/ProjectTabNavList.vue': TabNavList,
        '../ui/StickyBand.vue': StickyBand,
        '../session/detail/items/DaySeparator.vue': { props: ['label', 'dayKey'], setup: (props) => () => Vue.h('div', { class: 'day', 'data-day': props.dayKey }, props.label) },
        '../virtual-scroller/VirtualScroller.vue': scroller ? scroller.component : virtualScroller(),
        './TimelineEventRow.vue': EventRow,
        './TimelineFilterBand.vue': createFilterBand(bandCalls, filtersStore),
    })
    return { Panel, feed, state, calls, server, dataStore, workspacesStore, compactHeight, scroller, pinia, filtersStore, filterActions, bandCalls }
}

const settle = async () => {
    await flush()
    await new Promise((resolve) => setTimeout(resolve, 5))
    await flush()
}

async function mount(t, f, projectId = '__all__', { active = true } = {}) {
    const { renderer, root } = makeRenderer(600)
    const props = Vue.reactive({ projectId, active })
    const app = renderer.createApp({ render: () => Vue.h(f.Panel, props) })
    app.mount(root)
    let mounted = true
    t.after(() => { if (mounted) app.unmount() })
    await settle()
    root.setActive = async (value) => { props.active = value; await settle() }
    root.setProjectId = async (value) => { props.projectId = value; await settle() }
    root.unmount = () => { app.unmount(); mounted = false }
    return root
}

/**
 * `wa-callout` has a default slot and an `icon` slot only: a light-DOM child with any other `slot` is not
 * rendered by the browser, whatever this slot-less test renderer shows. Every callout child must be assignable.
 */
function assertRenderableCallouts(root) {
    const callouts = descendants(root, (n) => n.type === 'wa-callout')
    assert.ok(callouts.length >= 1)
    for (const callout of callouts) {
        for (const child of callout.children.filter((n) => n.type !== '#comment')) {
            assert.ok([undefined, 'icon'].includes(child.props.slot), `<${child.type} slot="${child.props.slot}"> has no slot to go to`)
        }
        const button = descendants(callout, (n) => n.type === 'wa-button')[0]
        assert.equal(button.props.slot, undefined, 'the Retry button is default content of the callout')
    }
}

const byClass = (root, name) => descendants(root, (node) => String(node.props.class ?? '').split(' ').includes(name))
const textOf = (node) => (node.type === '#comment' ? '' : `${node.text ?? ''}${node.children.map(textOf).join('')}`)
const rowIds = (root) => descendants(root, (n) => n.type === 'article').map((n) => n.props['data-id'])
/** The state block, found INSIDE the scroller (the state is an item of the list, under the band). */
const stateBlock = (root) => {
    const [scroller] = byClass(root, 'timeline-scroller')
    assert.ok(scroller, 'the scroller is always there')
    return byClass(scroller, 'timeline-state')[0]
}

test('the band is the sticky band of Stats (StickyBand) and holds the filter band of the page, and nothing else', async (t) => {
    const root = await mount(t, fixture(), 'p1', { active: false })
    const [band] = byClass(root, 'sticky-band')
    assert.match(String(band.props.class), /\bsticky-band\b/)
    assert.match(String(band.props.class), /\bglass-sticky\b/)
    const content = band.children.filter((n) => n.type !== '#comment' && n.type !== '#text')
    assert.equal(content.length, 1)
    assert.equal(content[0].props.class, 'filter-band')
    assert.equal(content[0].props['data-page-id'], 'p1')
    assert.equal(content[0].props['data-active'], 'false')
    await root.setActive(true)
    assert.equal(content[0].props['data-active'], 'true')
    const source = readFileSync(new URL('./TimelinePanel.vue', import.meta.url), 'utf8')
    assert.match(source, /<StickyBand ref="bandRef" class="timeline-band--over-list">\s*<TimelineFilterBand ref="filterBandRef" :page-id="projectId" :active="active" \/>\s*<\/StickyBand>/)
})

test('the global page loads the feed and renders a day separator then the events newest first', async (t) => {
    const f = fixture()
    const root = await mount(t, f)
    assert.deepEqual(f.calls, [{ projectIds: [], query: {}, before: null, afterId: null }])
    assert.equal(byClass(root, 'day').length, 1)
    assert.deepEqual(rowIds(root), ['event-3', 'event-2', 'event-1'])
})

test('while the projects are not loaded the page shows the loading state and sends nothing, then loads', async (t) => {
    const f = fixture({ projectsLoaded: false })
    const root = await mount(t, f, 'p1')
    assert.equal(f.calls.length, 0)
    assert.match(textOf(stateBlock(root)), /Loading/)
    f.dataStore.projectsLoaded = true
    await flush()
    await new Promise((resolve) => setTimeout(resolve, 5))
    await flush()
    assert.deepEqual(f.calls, [{ projectIds: ['p1'], query: {}, before: null, afterId: null }])
    assert.equal(rowIds(root).length, 3)
})

test('a workspace with no project shows the empty state and sends nothing; an unknown workspace is loading', async (t) => {
    const empty = fixture({ workspaces: { w: { projectIds: [] } } })
    const root = await mount(t, empty, 'ws:w')
    assert.equal(empty.calls.length, 0)
    assert.equal(textOf(stateBlock(root)), 'No events yet')
    const unknown = fixture()
    const root2 = await mount(t, unknown, 'ws:ghost')
    assert.equal(unknown.calls.length, 0)
    assert.match(textOf(stateBlock(root2)), /Loading/)
})

test('a workspace sends every project id, and a change of the set reloads the first page', async (t) => {
    const f = fixture({ workspaces: { w: { projectIds: ['p1', 'p2'] } } })
    const root = await mount(t, f, 'ws:w')
    assert.deepEqual(f.calls.at(-1).projectIds, ['p1', 'p2'])
    f.workspacesStore.workspaces.w = { projectIds: ['p1', 'p2', 'p3'] }
    await flush()
    await new Promise((resolve) => setTimeout(resolve, 5))
    assert.deepEqual(f.calls.map((c) => c.projectIds), [['p1', 'p2'], ['p1', 'p2', 'p3']])
    assert.ok(root)
})

test('an unknown project shows "Project not found" with a link to All Projects and sends nothing', async (t) => {
    const f = fixture()
    const root = await mount(t, f, 'ghost')
    assert.equal(f.calls.length, 0)
    const block = stateBlock(root)
    assert.match(textOf(block), /Project not found/)
    const [link] = descendants(block, (n) => n.type === 'a')
    assert.deepEqual(JSON.parse(link.props['data-to']), { name: 'projects-all' })
})

test('an empty feed shows the empty state and no end-of-list marker', async (t) => {
    const root = await mount(t, fixture({ events: [] }))
    assert.equal(textOf(stateBlock(root)), 'No events yet')
    assert.equal(byClass(root, 'timeline-footer').length, 0)
    assert.deepEqual(rowIds(root), [])
})

test('a failed first page shows an inline error whose Retry reloads', async (t) => {
    const f = fixture()
    f.server.fail = true
    const root = await mount(t, f)
    const callout = descendants(stateBlock(root), (n) => n.type === 'wa-callout')[0]
    assert.match(textOf(callout), /Failed to load the timeline/)
    assertRenderableCallouts(root)
    f.server.fail = false
    const retry = descendants(callout, (n) => n.type === 'wa-button')[0]
    retry.props.onClick()
    await flush()
    await new Promise((resolve) => setTimeout(resolve, 5))
    await flush()
    assert.equal(rowIds(root).length, 3)
})

test('a list shorter than the window asks for the next page by itself, and not when there is no more', async (t) => {
    const more = fixture({ hasMore: true })
    await mount(t, more)
    assert.equal(more.calls.filter((c) => c.before).length >= 1, true)
    const none = fixture({ hasMore: false })
    await mount(t, none)
    assert.equal(none.calls.filter((c) => c.before).length, 0)
})

// Sixty events on one day: 61 rows with the separator.
const sixty = () => Array.from({ length: 60 }, (_, i) => event(60 - i, { occurred_at: isoAt(60 - i) }))
const beforeRequests = (f) => f.calls.filter((c) => c.before).length

test('the next page is requested only when the end of the list is in view', async (t) => {
    const f = fixture({ events: sixty(), hasMore: true, fakeScroller: true })
    await mount(t, f)
    assert.equal(beforeRequests(f), 0, 'the first rows are in view, far from the end')
    f.scroller.log.emit({ startIndex: 0, endIndex: 20, visibleStartIndex: 0, visibleEndIndex: 5 })
    await settle()
    assert.equal(beforeRequests(f), 0)
    f.scroller.log.emit({ startIndex: 30, endIndex: 60, visibleStartIndex: 40, visibleEndIndex: 58 })
    await settle()
    assert.equal(beforeRequests(f), 1, 'the end is in view: exactly one request')
    f.scroller.log.emit({ startIndex: 30, endIndex: 60, visibleStartIndex: 40, visibleEndIndex: 58 })
    await settle()
    assert.equal(beforeRequests(f), 1, 'the server said there is no more')
})

test('a failed next page shows an inline error whose Retry sends one new request', async (t) => {
    const f = fixture({ events: sixty(), hasMore: true, fakeScroller: true })
    const root = await mount(t, f)
    f.server.fail = true
    f.scroller.log.emit({ startIndex: 30, endIndex: 60, visibleStartIndex: 40, visibleEndIndex: 58 })
    await settle()
    const footer = byClass(root, 'timeline-footer')[0]
    assert.match(textOf(footer), /Failed to load more events/)
    assertRenderableCallouts(footer)
    assert.equal(beforeRequests(f), 1)
    f.scroller.log.emit({ startIndex: 30, endIndex: 60, visibleStartIndex: 40, visibleEndIndex: 58 })
    await settle()
    assert.equal(beforeRequests(f), 1, 'no automatic retry while the error shows')
    f.server.fail = false
    descendants(footer, (n) => n.type === 'wa-button')[0].props.onClick()
    await settle()
    assert.equal(beforeRequests(f), 2, 'Retry sends one new request')
    assert.equal(f.state.loadError, null)
    assert.equal(byClass(root, 'timeline-footer').filter((n) => textOf(n).includes('Failed')).length, 0)
})

test('a page that lands with the end of the list still in view asks for the next one by itself', async (t) => {
    // No `update` event: the panel itself asks the scroller what is in view when a page has landed.
    const f = fixture({ events: sixty(), hasMore: true, fakeScroller: true })
    f.scroller.log.range = { start: 40, end: 58 }
    await mount(t, f)
    assert.equal(beforeRequests(f), 1)
})

test('a scroller that becomes visible again asks the next page if the end is in view', async (t) => {
    const f = fixture({ events: sixty(), hasMore: true, fakeScroller: true })
    await mount(t, f)
    assert.equal(beforeRequests(f), 0)
    f.scroller.log.range = { start: 40, end: 58 }
    f.scroller.log.emitVisible()
    await settle()
    assert.equal(beforeRequests(f), 1)
})

test('unmounting the page disposes its feed', async (t) => {
    const f = fixture({ fakeScroller: true })
    let disposed = 0
    const original = f.feed.dispose
    f.feed.dispose = () => { disposed += 1; original() }
    const root = await mount(t, f)
    assert.equal(disposed, 0)
    root.unmount()
    assert.equal(disposed, 1)
})

const seedsOf = (f) => f.scroller.log.calls.filter(([name]) => name === 'seed')

test('every row gets a height estimate once the scroller exists, the top room and the state item included', async (t) => {
    const f = fixture({ fakeScroller: true, compact: true })
    await mount(t, f)
    const seeds = Object.fromEntries(seedsOf(f).map(([, key, height]) => [key, height]))
    const { ROW_HEIGHT_ESTIMATES: H } = rowsModule
    assert.equal(seeds['timeline-top'], H.band + H.nav, 'compact height: the band and the navigation list')
    assert.equal(seeds['timeline-state'], H.state, 'the loading state of the first page')
    assert.equal(seeds['event-3'], H.head, 'the block head: top of the card, header line, event line')
    assert.equal(seeds['event-2'], H.event)
    assert.equal(seeds['event-1'], H.event + H.blockEnd, 'the block end: bottom of the card and the gap')
    assert.equal(seeds[Object.keys(seeds).find((key) => key.startsWith('daysep-'))], H.day)
    assert.equal(seedsOf(f).length, 6, 'top room, loading state, day separator and three events, once each')
})

test('a row the scroller already measured keeps its measure', async (t) => {
    const f = fixture({ fakeScroller: true })
    f.scroller.log.measured.add('event-2')
    await mount(t, f)
    assert.deepEqual(seedsOf(f).map(([, key]) => key).sort(), ['daysep-2026-10-14', 'event-1', 'event-3', 'timeline-state', 'timeline-top'])
})

test('a row whose head or end status flips is seeded again with the new estimate while it still holds the old seed', async (t) => {
    const f = fixture({ fakeScroller: true })
    await mount(t, f)
    const seedsFor = (key) => seedsOf(f).filter(([, k]) => k === key).map(([, , height]) => height)
    const { ROW_HEIGHT_ESTIMATES: H } = rowsModule
    assert.deepEqual(seedsFor('event-2'), [H.event])
    // The head (event-3) leaves the list: event-2 becomes the head of the block.
    f.state.events = f.state.events.filter((e) => e.id !== 3)
    await settle()
    assert.deepEqual(seedsFor('event-2'), [H.event, H.head])
    // Back to a following row: the seed follows again.
    f.state.events = [event(3), ...f.state.events]
    await settle()
    assert.deepEqual(seedsFor('event-2'), [H.event, H.head, H.event])
    // The end of the block flips too: event-1 leaves, event-2 closes the block.
    f.state.events = f.state.events.filter((e) => e.id !== 1)
    await settle()
    assert.deepEqual(seedsFor('event-2'), [H.event, H.head, H.event, H.event + H.blockEnd])
})

test('a row measured since its seed keeps its measure when its head status flips', async (t) => {
    const f = fixture({ fakeScroller: true })
    await mount(t, f)
    f.scroller.log.measured.add('event-2')
    f.state.events = f.state.events.filter((e) => e.id !== 3)
    await settle()
    assert.deepEqual(seedsOf(f).filter(([, k]) => k === 'event-2').map(([, , height]) => height), [34])
})

test('a row that left the list is seeded again when it returns (the scroller stays through every state)', async (t) => {
    const f = fixture({ fakeScroller: true })
    const root = await mount(t, f, 'p1')
    assert.equal(f.scroller.log.mounts, 1)
    assert.equal(seedsOf(f).filter(([, key]) => key === 'event-3').length, 1)
    // The rows leave for the loading state, then come back.
    f.dataStore.projectsLoaded = false
    await settle()
    assert.deepEqual(rowIds(root), [])
    f.dataStore.projectsLoaded = true
    await settle()
    assert.equal(f.scroller.log.mounts, 1, 'the same scroller')
    assert.equal(seedsOf(f).filter(([, key]) => key === 'event-3').length, 2)
    const all = f.state.events
    f.state.events = [all[0]]
    await settle()
    f.state.events = all
    await settle()
    assert.equal(seedsOf(f).filter(([, key]) => key === 'event-2').length, 3, 'trimmed with the list, seeded when it came back')
    assert.ok(seedsOf(f).every(([, , , wasRendered]) => wasRendered), 'every seed comes after the row is rendered')
})

/** The parts of the page in document order: navigation list, band, top room, day separators, event rows. */
const structure = (root) => descendants(root, (n) => n.type === 'nav' || ['sticky-band', 'timeline-top-room', 'day', 'timeline-state'].some((name) => String(n.props.class ?? '').split(' ').includes(name)) || n.type === 'article')
    .map((n) => (n.type === 'nav' || n.type === 'article' ? n.type : ['sticky-band', 'timeline-top-room', 'day', 'timeline-state'].find((name) => String(n.props.class).split(' ').includes(name))))

test('the Stats structure in the list: the navigation list (compact height), then the band, both inside the scroller, then the rows', async (t) => {
    const compact = await mount(t, fixture({ compact: true }))
    assert.deepEqual(structure(compact), ['nav', 'sticky-band', 'timeline-top-room', 'day', 'article', 'article', 'article'])
    const [scroller] = byClass(compact, 'timeline-scroller')
    assert.equal(byClass(scroller, 'sticky-band').length, 1, 'the band sticks inside the scroll container of the list')
    assert.equal(byClass(scroller, 'project-tab-nav').length, 1, 'the navigation list scrolls away with the list')
    assert.equal(byClass(compact, 'project-tab-nav-divider').length, 1)
    const normal = await mount(t, fixture({ compact: false }))
    assert.deepEqual(structure(normal), ['sticky-band', 'timeline-top-room', 'day', 'article', 'article', 'article'])
})

test('the top room is the measured height of the navigation list and the band together', async (t) => {
    // The test renderer gives every element the same height (600): band alone 600, with the navigation list 1200.
    const compact = await mount(t, fixture({ compact: true }))
    assert.equal(compact.children[0].props.style['--timeline-top-height'], '1200px')
    const normal = await mount(t, fixture({ compact: false }))
    assert.equal(normal.children[0].props.style['--timeline-top-height'], '600px')
})

test('the panel sets the time column sizer once, from the clock format of the rows', async (t) => {
    const root = await mount(t, fixture())
    assert.equal(root.children[0].props.style['--timeline-time-sizer'], eventTimeModule.timeColumnSizer())
})

test('compact-height mode: every state (loading, empty, not found, error) carries the navigation list and its divider above the band; normal mode none', async (t) => {
    const navigation = (root) => [byClass(root, 'project-tab-nav-list').length, byClass(root, 'project-tab-nav-divider').length]
    const loading = await mount(t, fixture({ compact: true, projectsLoaded: false }), 'p1')
    assert.deepEqual(navigation(loading), [1, 1])
    const empty = await mount(t, fixture({ compact: true, workspaces: { w: { projectIds: [] } } }), 'ws:w')
    assert.deepEqual(navigation(empty), [1, 1])
    const missing = await mount(t, fixture({ compact: true, projects: {} }), 'ghost')
    assert.deepEqual(navigation(missing), [1, 1])
    const broken = fixture({ compact: true })
    broken.server.fail = true
    assert.deepEqual(navigation(await mount(t, broken)), [1, 1])
    const normal = await mount(t, fixture({ compact: false, projectsLoaded: false }), 'p1')
    assert.deepEqual(navigation(normal), [0, 0])
    assert.deepEqual(structure(loading), ['nav', 'sticky-band', 'timeline-top-room', 'timeline-state'])
    assert.deepEqual(structure(normal), ['sticky-band', 'timeline-top-room', 'timeline-state'])
})

test('no timeline module imports the router statically (circular imports break the hot reload)', () => {
    const dirs = ['../../components/timeline', '../../utils/timeline', '../../composables']
    const files = dirs.flatMap((dir) => readdirSync(new URL(`${dir}/`, import.meta.url))
        .filter((name) => /^(?!.*\.test\.js$).*\.(js|vue)$/.test(name) && (dir !== '../../composables' || name === 'useTimelineFeed.js'))
        .map((name) => new URL(`${dir}/${name}`, import.meta.url)))
    assert.ok(files.length >= 12)
    for (const file of files) {
        assert.ok(!/from ['"](?:\.\.?\/)+router(?:\.js)?['"]/.test(readFileSync(file, 'utf8')), String(file))
    }
})

test('source pins: the band and the navigation list take no room over the list, the router is not imported, the bottom pin is off', () => {
    const source = readFileSync(new URL('./TimelinePanel.vue', import.meta.url), 'utf8')
    assert.match(source, /<template #before>\s*<ProjectTabNavList v-if="compactHeight" ref="topNavRef"[^>]*\/>\s*<StickyBand ref="bandRef" class="timeline-band--over-list">/)
    // The band is found by StickyBand's own class: no Timeline class without a rule of its own.
    assert.ok(!/class="[^"]*\btimeline-band(?![-\w])/.test(source), 'no timeline-band class')
    assert.match(source, /\.timeline-band--over-list \{\s*margin-block-end: calc\(-1 \* var\(--timeline-top-height\)\);/)
    assert.match(source, /\.timeline-top-room \{\s*height: var\(--timeline-top-height\);/)
    assert.match(source, /\.timeline-top-nav \{\s*position: relative;\s*z-index: 1;/)
    // No second sticky implementation: the band is the shared one.
    assert.ok(!/position: sticky/.test(source.slice(source.indexOf('<style'))))
    assert.ok(!/from '\.\.\/\.\.\/router/.test(source) && !/from 'vue-router'.*useRouter/.test(source))
    assert.match(source, /\.timeline-list \.timeline-scroller\.at-bottom \{\s*overflow-anchor: none;/)
    assert.match(source, /\.timeline-list \.timeline-scroller \{\s*flex: 1;\s*min-height: 0;/)
    assert.match(source, /<VirtualScroller\s+ref="scrollerRef"\s+class="timeline-scroller"/)
    // Stats' archived-blind list: the "show archived projects" setting does not apply to the Timeline.
    assert.match(source, /workspacesStore\.getAllProjectIds\(id\)/)
    assert.ok(!source.includes('getVisibleProjectIds'))
    assert.match(source, /prevent-auto-scroll-to-bottom/)
})

test('source pins: Stats uses the same two pieces, the navigation list above its sticky provider filter', () => {
    const detail = readFileSync(new URL('../project/ProjectDetailPanel.vue', import.meta.url), 'utf8')
    assert.match(detail, /<wa-tab-panel name="stats">\s*<ProjectTabNavList :project-id="projectId" \/>\s*<ContributionGraphs/)
    assert.ok(!detail.includes('stats-nav-list'))
    const nav = readFileSync(new URL('../project/ProjectTabNavList.vue', import.meta.url), 'utf8')
    assert.match(nav, /\.project-tab-nav \{[^}]*flex-shrink: 0;/, 'a flex parent must not squash the navigation list')
    const graphs = readFileSync(new URL('../activity/ContributionGraphs.vue', import.meta.url), 'utf8')
    assert.match(graphs, /<template>\s*(?:<!--[\s\S]*?-->\s*)?<StickyBand v-if="showProviderSelect" class="provider-filter">/)
    assert.ok(!/position: sticky/.test(graphs.slice(graphs.indexOf('<style'))))
})

// ── One band for every state, the filters ──────────────────────────────────────────────────────────────────

const page = (events) => ({ events, has_more: false, truncated: false, latest_id: events.length ? events[0].id : null, sessions: {} })
const callouts = (root) => descendants(root, (n) => n.type === 'wa-callout')

test('the band is mounted once and is the same element across loading, list, empty and error', async (t) => {
    const f = fixture({ projectsLoaded: false, workspaces: { w: { projectIds: ['p1'] } } })
    const root = await mount(t, f, 'ws:w')
    const bands = () => byClass(root, 'sticky-band')
    assert.equal(bands().length, 1)
    const band = bands()[0]
    const sameNode = () => bands().length === 1 && bands()[0] === band
    assert.match(textOf(stateBlock(root)), /Loading/)
    f.dataStore.projectsLoaded = true
    await settle()
    assert.equal(rowIds(root).length, 3, 'the list')
    assert.equal(sameNode(), true)
    f.server.respond = () => page([])
    f.workspacesStore.workspaces.w = { projectIds: ['p1', 'p2'] }
    await settle()
    assert.equal(textOf(stateBlock(root)), 'No events yet')
    assert.equal(sameNode(), true)
    f.server.fail = true
    f.workspacesStore.workspaces.w = { projectIds: ['p1', 'p2', 'p3'] }
    await settle()
    assert.match(textOf(stateBlock(root)), /Failed to load the timeline/)
    assert.equal(sameNode(), true)
})

test('a filter change reloads the first page with the new query and scrolls to the top', async (t) => {
    const f = fixture({ fakeScroller: true })
    await mount(t, f, 'p1')
    const tops = () => f.scroller.log.calls.filter(([name]) => name === 'top').length
    assert.equal(tops(), 0, 'the first load does not move the scroll')
    f.filtersStore.setView('p1', 'waits_problems')
    f.filtersStore.toggle('p1', 'families', 'processes')
    await settle()
    const query = filtersModule.toQuery(f.filtersStore.filtersFor('p1'))
    assert.deepEqual(query, { view: 'waits_problems', families: 'processes' })
    assert.deepEqual(f.calls, [
        { projectIds: ['p1'], query: {}, before: null, afterId: null },
        { projectIds: ['p1'], query, before: null, afterId: null },
    ])
    assert.equal(tops(), 1)
    assert.equal(f.scroller.log.mounts, 1, 'the scroller stays')
    // An action that leaves the query as it is reloads nothing.
    f.filtersStore.resetPanel('p1')
    f.filtersStore.toggle('p1', 'families', 'processes')
    await settle()
    assert.equal(f.calls.length, 2)
})

test('the no-match state shows only after a successful first page with zero events and a filter set', async (t) => {
    // Filters set, first page in flight: loading, not "no match".
    const slow = fixture({ fakeScroller: true })
    slow.filtersStore.setView('p1', 'waits_problems')
    const pending = deferred()
    slow.server.respond = () => pending.promise
    const root = await mount(t, slow, 'p1')
    assert.match(textOf(stateBlock(root)), /Loading/)
    assert.ok(!textOf(root).includes('Nothing matches'))
    pending.resolve(page([]))
    await settle()
    const block = stateBlock(root)
    assert.equal(textOf(byClass(block, 'timeline-no-match-message')[0]), 'Nothing matches Waits & problems with these filters')
    const [clear] = descendants(block, (n) => n.type === 'wa-button')
    assert.equal(textOf(clear).trim(), 'Clear all')
    clear.props.onClick()
    await settle()
    assert.deepEqual(slow.filterActions, [['setView', 'p1', 'waits_problems'], ['clearAll', 'p1']])
    assert.deepEqual(slow.calls.at(-1).query, {}, 'Clear all reloads without a filter')
    assert.equal(textOf(stateBlock(root)), 'No events yet')

    // Error wins over it.
    const broken = fixture({ events: [] })
    broken.filtersStore.toggle('__all__', 'providers', 'codex')
    broken.server.fail = true
    const root2 = await mount(t, broken)
    assert.match(textOf(stateBlock(root2)), /Failed to load the timeline/)
    assert.ok(!textOf(root2).includes('Nothing matches'))

    // A panel value with the `all` view: the message names no view.
    const panel = fixture({ events: [] })
    panel.filtersStore.toggle('__all__', 'authors', 'you')
    const root3 = await mount(t, panel)
    assert.equal(textOf(byClass(stateBlock(root3), 'timeline-no-match-message')[0]), 'Nothing matches these filters')

    // Only a session filter: filtered too.
    const session = fixture({ events: [] })
    session.filtersStore.setSession('__all__', { id: 's9', title: 'Other' })
    assert.match(textOf(stateBlock(await mount(t, session))), /Nothing matches these filters/)

    // No filter: the unfiltered empty timeline.
    const plain = await mount(t, fixture({ events: [] }))
    assert.equal(textOf(stateBlock(plain)), 'No events yet')
})

test('the no-match "Clear all" clears the filters, then focuses the Filters button after the next tick', async (t) => {
    const f = fixture({ events: [] })
    f.filtersStore.setView('p1', 'waits_problems')
    const root = await mount(t, f, 'p1')
    const [clear] = descendants(stateBlock(root), (n) => n.type === 'wa-button')
    assert.equal(textOf(clear).trim(), 'Clear all')
    clear.props.onClick()
    assert.deepEqual(f.bandCalls, [], 'not before the tick')
    await Vue.nextTick()
    assert.deepEqual(f.bandCalls, [['focusFiltersButton', 'all']], 'after the filters were cleared')
    await settle()
    assert.equal(f.bandCalls.length, 1)
    assert.deepEqual(f.filterActions.at(-1), ['clearAll', 'p1'])
})

test('"Only this session" from a row sets the session filter, then focuses the session pill after the next tick', async (t) => {
    const f = fixture()
    const root = await mount(t, f, 'p1')
    const [first] = descendants(root, (n) => n.type === 'article')
    assert.equal(first.props['data-session-filter'], null, 'no session filtered yet')
    first.props.onToggle({ id: 's1', title: 'My session' })
    assert.deepEqual(f.filterActions, [['setSession', 'p1', { id: 's1', title: 'My session' }]])
    assert.deepEqual(f.bandCalls, [], 'not before the tick')
    await Vue.nextTick()
    assert.deepEqual(f.bandCalls, [['focusSessionPill']])
    await settle()
    assert.deepEqual(f.calls.at(-1), { projectIds: ['p1'], query: { session: 's1' }, before: null, afterId: null })
    // Every row is told which session is filtered.
    assert.deepEqual(descendants(root, (n) => n.type === 'article').map((n) => n.props['data-session-filter']), ['s1', 's1', 's1'])
    // Pressed again on the same session: released, then the focus goes to the Filters button.
    descendants(root, (n) => n.type === 'article')[0].props.onToggle({ id: 's1', title: 'My session' })
    assert.deepEqual(f.filterActions.at(-1), ['clearSession', 'p1'])
    assert.equal(f.bandCalls.length, 1, 'not before the tick')
    await Vue.nextTick()
    assert.deepEqual(f.bandCalls, [['focusSessionPill'], ['focusFiltersButton', 'all']])
    await settle()
    assert.equal(f.filtersStore.filtersFor('p1').session, null)
    assert.deepEqual(f.calls.at(-1), { projectIds: ['p1'], query: {}, before: null, afterId: null })
    assert.equal(f.bandCalls.length, 2)
})

test('"Only this session" on another session replaces the filtered one, and keeps the other filters', async (t) => {
    const f = fixture()
    f.filtersStore.setView('p1', 'without_turns')
    f.filtersStore.setSession('p1', { id: 's1', title: 'My session' })
    const root = await mount(t, f, 'p1')
    descendants(root, (n) => n.type === 'article')[0].props.onToggle({ id: 's2', title: null })
    await Vue.nextTick()
    assert.deepEqual(f.filterActions.at(-1), ['setSession', 'p1', { id: 's2', title: null }])
    assert.deepEqual(f.bandCalls, [['focusSessionPill']])
    const filters = f.filtersStore.filtersFor('p1')
    assert.deepEqual([filters.view, filters.session.id, filters.session.title], ['without_turns', 's2', null])
})

test('every value of a group checked filters nothing: an empty first page is "No events yet", not the no-match state', async (t) => {
    // Spec 4.5: checking every value of a group is the same as checking none. The query is empty, so is the timeline.
    const f = fixture({ events: [] })
    for (const { value } of filtersModule.FAMILIES) f.filtersStore.toggle('__all__', 'families', value)
    assert.equal(filtersModule.isFiltered(f.filtersStore.filtersFor('__all__')), true, 'the checked values still show as checked')
    const root = await mount(t, f)
    assert.deepEqual(f.calls, [{ projectIds: [], query: {}, before: null, afterId: null }])
    assert.equal(textOf(stateBlock(root)), 'No events yet')
    assert.equal(byClass(root, 'timeline-no-match-message').length, 0)
})

test('a page keeps its filters when shown again', async (t) => {
    const projects = { pA: { id: 'pA' }, pB: { id: 'pB' } }
    const f = fixture({ projects })
    const root = await mount(t, f, 'pA')
    f.filtersStore.toggle('pA', 'families', 'processes')
    await settle()
    const filtered = { families: 'processes' }
    assert.deepEqual(f.calls.at(-1), { projectIds: ['pA'], query: filtered, before: null, afterId: null })
    await root.setProjectId('pB')
    assert.deepEqual(f.calls.at(-1), { projectIds: ['pB'], query: {}, before: null, afterId: null })
    await root.setProjectId('pA')
    assert.deepEqual(f.calls.at(-1), { projectIds: ['pA'], query: filtered, before: null, afterId: null })
    // Another panel of the same app (the page mounted again): the same memory.
    const again = fixture({ projects, pinia: f.pinia })
    await mount(t, again, 'pA')
    assert.deepEqual(again.calls, [{ projectIds: ['pA'], query: filtered, before: null, afterId: null }])
    assert.deepEqual(filtersModule.toQuery(f.filtersStore.filtersFor('pB')), {})
})

test('loadMore is never asked while the state item is shown', async (t) => {
    const nearEnd = { startIndex: 0, endIndex: 2, visibleStartIndex: 0, visibleEndIndex: 2 }
    const watchLoadMore = (f) => {
        const asked = { count: 0 }
        const original = f.feed.loadMore
        f.feed.loadMore = () => { asked.count += 1; original() }
        return asked
    }
    // Loading (projects not loaded), then empty, then error: the end of the list is always in view.
    for (const make of [
        () => fixture({ fakeScroller: true, projectsLoaded: false }),
        () => fixture({ fakeScroller: true, events: [] }),
        () => { const f = fixture({ fakeScroller: true }); f.server.fail = true; return f },
    ]) {
        const f = make()
        const asked = watchLoadMore(f)
        await mount(t, f, 'p1')
        f.scroller.log.range = { start: 0, end: 2 }
        f.scroller.log.emit(nearEnd)
        f.scroller.log.emitVisible()
        await settle()
        assert.equal(asked.count, 0)
    }
    // A list, then a filter change whose first page is in flight: the loading state item replaces the rows.
    const f = fixture({ fakeScroller: true, events: sixty(), hasMore: true })
    const root = await mount(t, f, 'p1')
    const asked = watchLoadMore(f)
    const pending = deferred()
    f.server.respond = () => pending.promise
    f.filtersStore.setView('p1', 'without_turns')
    await settle()
    assert.match(textOf(stateBlock(root)), /Loading/)
    f.scroller.log.range = { start: 0, end: 2 }
    f.scroller.log.emit(nearEnd)
    f.scroller.log.emitVisible()
    await settle()
    assert.equal(asked.count, 0)
    assert.equal(beforeRequests(f), 0)
    pending.resolve(page([]))
    await settle()
})

test('a failed first page shows one error, not the "load more" footer too', async (t) => {
    const f = fixture()
    f.server.fail = true
    const root = await mount(t, f)
    assert.equal(f.state.loadError.phase, 'first')
    assert.equal(callouts(root).length, 1)
    assert.equal(byClass(root, 'timeline-footer').length, 0)
    assert.ok(!textOf(root).includes('Failed to load more events'))
})

// ── Staying current ─────────────────────────────────────────────────────────────────────────────────────────

const signal = (latestId) => window.dispatchEvent(new CustomEvent('twicc:timeline-changed', { detail: { latestId } }))

test('a signal with a new id fetches what is new once and shows it at the top', async (t) => {
    const f = fixture({ fakeScroller: true })
    const root = await mount(t, f)
    f.server.respond = ((respond) => (params) => (params.afterId !== null
        ? { events: [event(4)], has_more: false, truncated: false, latest_id: 4, sessions: {} }
        : respond(params)))(f.server.respond)
    signal(4)
    signal(4)
    await settle()
    assert.deepEqual(f.calls.map((c) => c.afterId), [null, 3])
    assert.deepEqual(rowIds(root), ['event-4', 'event-3', 'event-2', 'event-1'])
})

test('a signal with a known id does nothing', async (t) => {
    const f = fixture({ fakeScroller: true })
    await mount(t, f)
    signal(3)
    await settle()
    assert.equal(f.calls.length, 1)
})

test('a reconnection and the first OPEN of the socket catch up, as one fetch', async (t) => {
    const f = fixture({ fakeScroller: true })
    await mount(t, f)
    window.dispatchEvent(new Event('twicc:ws-reconnected'))
    window.dispatchEvent(new Event('twicc:ws-first-open'))
    await settle()
    assert.deepEqual(f.calls.map((c) => c.afterId), [null, 3])
})

test('a resync request triggers nothing timeline-specific', async (t) => {
    const f = fixture({ fakeScroller: true })
    await mount(t, f)
    window.dispatchEvent(new Event('twicc:resync-required'))
    await settle()
    assert.equal(f.calls.length, 1)
})

test('coming back to the Timeline tab catches up once', async (t) => {
    const f = fixture({ fakeScroller: true })
    const root = await mount(t, f, '__all__', { active: false })
    assert.equal(f.calls.length, 1, 'a hidden page loads like a visible one')
    await root.setActive(true)
    assert.deepEqual(f.calls.map((c) => c.afterId), [null, 3])
    await root.setActive(false)
    assert.equal(f.calls.length, 2, 'leaving the tab fetches nothing')
})

test('an unmounted page stops listening: its window listeners are removed and nothing is fetched', async (t) => {
    const before = listenerCounts()
    const f = fixture({ fakeScroller: true })
    const root = await mount(t, f)
    assert.deepEqual(listenerCounts(), Object.fromEntries(TIMELINE_EVENT_TYPES.map((type) => [type, before[type] + 1])))
    root.unmount()
    assert.deepEqual(listenerCounts(), before)
    signal(9)
    window.dispatchEvent(new Event('twicc:ws-reconnected'))
    await settle()
    assert.equal(f.calls.length, 1)
})

test('scrolled down, rows inserted above put the first visible row back; at the top nothing moves', async (t) => {
    const f = fixture({ fakeScroller: true })
    await mount(t, f)
    f.server.respond = ((respond) => (params) => (params.afterId !== null
        ? { events: [event(4)], has_more: false, truncated: false, latest_id: 4, sessions: {} }
        : respond(params)))(f.server.respond)
    // The top room is row 0, the day separator row 1, event-3 row 2: that is the first visible row (`anchorIndex`).
    f.scroller.log.anchorIndex = 2
    f.scroller.log.scrollTop = 400
    signal(4)
    await settle()
    const restores = f.scroller.log.calls.filter(([name]) => name !== 'seed')
    assert.equal(restores.length, 1)
    // The row captured BEFORE the render (event-3, not the event-4 that took its place at index 2) ...
    assert.deepEqual(restores[0][1], { key: 'event-3', index: 2, offset: 7 })
    // ... and put back AFTER the render (the new row is among the rendered ones).
    assert.ok(restores[0][2].includes('event-4'), 'the restore waits for the render')
    f.scroller.log.calls.length = 0
    f.scroller.log.scrollTop = 0
    f.server.respond = (params) => (params.afterId !== null
        ? { events: [event(5)], has_more: false, truncated: false, latest_id: 5, sessions: {} }
        : { events: [], has_more: false, truncated: false, latest_id: 5, sessions: {} })
    signal(5)
    await settle()
    assert.deepEqual(f.scroller.log.calls.filter(([name]) => name !== 'seed'), [])
})

test('scrolled within the top room (the room row is the anchor), a live insertion puts the visible events back', async (t) => {
    const f = fixture({ fakeScroller: true })
    await mount(t, f)
    f.server.respond = ((respond) => (params) => (params.afterId !== null
        ? { events: [event(4)], has_more: false, truncated: false, latest_id: 4, sessions: {} }
        : respond(params)))(f.server.respond)
    // Rows: top room (0), day separator (1), event-3 (2). 20px down the room: the room row is the first visible one.
    f.scroller.log.anchorIndex = 0
    f.scroller.log.scrollTop = 20
    signal(4)
    await settle()
    const restores = f.scroller.log.calls.filter(([name]) => name !== 'seed')
    // The anchor is the first event row (event-3, 60px below the room top), not the room that never moves.
    assert.equal(restores.length, 1)
    assert.deepEqual(restores[0][1], { key: 'event-3', index: 2, offset: 7 - 60 })
})

test('source pins: the live events and the active prop are wired, useWebSocket dispatches them', () => {
    const panel = readFileSync(new URL('./TimelinePanel.vue', import.meta.url), 'utf8')
    assert.match(panel, /bindTimelineEvents\(window, feed\)/)
    // The order that keeps the view still: capture BEFORE the DOM moves (pre-flush watcher), restore after it.
    assert.match(panel, /watch\(items, async \(next\) => \{\s*const snapshot = scrollKeeper\.capture\(\)\s*if \(!snapshot\) return\s*await nextTick\(\)\s*scrollKeeper\.restore\(snapshot, [^\n]*\)\s*\}, \{ flush: 'pre' \}\)/)
    assert.match(panel, /watch\(\(\) => props\.active, \(active\) => \{\s*if \(active\) feed\.refresh\(\)/)
    const socket = readFileSync(new URL('../../composables/useWebSocket.js', import.meta.url), 'utf8')
    const changed = socket.slice(socket.indexOf("case 'timeline_changed':"))
    assert.ok(changed.slice(0, 600).includes("window.dispatchEvent(new CustomEvent('twicc:timeline-changed', { detail: { latestId: msg.latest_id } }))"))
    const reconnect = socket.slice(socket.indexOf("window.dispatchEvent(new CustomEvent('twicc:ws-reconnected'))"))
    assert.match(reconnect.slice(0, 500), /\} else \{[^}]*window\.dispatchEvent\(new CustomEvent\('twicc:ws-first-open'\)\)[^}]*\}\s*wasConnected = true/)
    const detail = readFileSync(new URL('../project/ProjectDetailPanel.vue', import.meta.url), 'utf8')
    assert.match(detail, /<TimelinePanel :project-id="projectId" :active="isActive && activeTab === 'timeline'" \/>/)
})
