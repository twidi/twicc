// A list off screen lets the store defer its session's rows (stores/data.js
// registerVisualItemsView); shown again, it takes them in before anything reads them. These
// tests run the list's real reactivation and "Deferred rows" code, with the real store
// actions, inside a real KeepAlive: a cached view (like SessionView) providing `sessionActive`,
// and a scroller child whose activation hook runs before the list's (as VirtualScroller's).
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { createPinia, defineStore } from 'pinia'
import { makeRenderer, flush } from '../../../../tests/helpers/scrollerComponentHarness.js'
import { normalizeChatTimestamp, placeSessionEvents, orderCompactItems } from '../../../utils/chatEvents/placement.js'
import { projectEventSegments } from '../../../utils/chatEvents/segments.js'
import { mergeSessionEventRows, insertEventDaySeparators } from '../../../utils/chatEvents/rows.js'
import { jsonValuesEqual } from '../../../utils/jsonValuesEqual.js'
import { DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM } from '../../../constants.js'
import { computeVisualItems, visualItemEqual, insertDaySeparators, makeBackgroundWorkStatusItem, markLiveTimestampAnchor } from '../../../utils/visualItems.js'
import { getParsedContent, setParsedContent, hasContent } from '../../../utils/parsedContent.js'
import { buildBackgroundWorkStatusLines } from '../../../utils/backgroundWork.js'
import { createVisualItemsViews } from '../../../utils/visualItemsViews.js'

const { h, ref, reactive, readonly, computed, provide, inject, watch, nextTick, unref, KeepAlive,
    onActivated, onDeactivated, onBeforeUnmount } = Vue

function slice(text, startText, endText) {
    const start = text.indexOf(startText)
    const end = text.indexOf(endText, start)
    assert.ok(start >= 0 && end > start, startText)
    return text.slice(start, end)
}
const listSource = readFileSync(new URL('./SessionItemsList.vue', import.meta.url), 'utf8')
// The deactivation hook, from the reactivation hook to the end of "Deferred rows", and the
// length watcher (the live follow to the bottom).
const deactivationSource = slice(listSource, '// Cleanup timers when deactivated', '// On reactivation:')
const reactivationSource = slice(listSource, 'onActivated(() => {\n    // The route change usually', '// Build base URL for API calls')
const followSource = slice(listSource, 'watch(\n    () => visualItems.value?.length,', '\n/**\n * Preserve the scroll position')
const listSetup = new Function('watch', 'nextTick', 'unref', 'ref', 'onActivated', 'onDeactivated', 'onBeforeUnmount', 'useRoute',
    'store', 'props', 'sessionActive', 'scrollerRef', 'visualItems', 'displayItems', 'entrance', 'scrollToBottomUntilStable',
    'isAutoScrollingToBottom', 'isSessionEvent', 'resolveStability', 'isInitialScrolling', `
    let itemCountAtDeactivation = null
    let wasNearBottomAtDeactivation = false
    let savedScrollAnchor = null
    let scrollIntent = 0, scrollIntentAtDeactivation = null
    let temporaryIndicatorTimer = null, pendingEventScroll = null, pendingScrollToBottom = null
    ${deactivationSource}
    ${reactivationSource}
    ${followSource}
`)

const dataSource = readFileSync(new URL('../../../stores/data.js', import.meta.url), 'utf8')
const helperSource = slice(dataSource, 'function getStreamingItemMetadata(', '\n/**') + slice(dataSource, 'function computeLastToolVisible(', '\n/**')
    + slice(dataSource, 'function isDebugForced(', '\n/**')
const actionSource = ['recomputeVisualItems(sessionId)', 'registerVisualItemsView(', 'unregisterVisualItemsView(', 'flushDeferredVisualItems(',
    'flushShownVisualItems(', 'addSessionItems('].map(name => slice(dataSource, `        ${name}`, '\n        /**')).join('\n')
let storeId = 0
function makeStore(page, { defers = true } = {}) {
    const settings = { getDisplayMode: DISPLAY_MODE.NORMAL, areMessageTimestampsShown: false }
    const deps = { placeSessionEvents, projectEventSegments, mergeSessionEventRows, insertEventDaySeparators, jsonValuesEqual,
        useSettingsStore: () => settings, DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM, computeVisualItems, visualItemEqual,
        insertDaySeparators, makeBackgroundWorkStatusItem, markLiveTimestampAnchor, orderCompactItems, getParsedContent, setParsedContent,
        hasContent, buildBackgroundWorkStatusLines, isBufferActive: () => false, isLaunchedEphemeral: () => false, toRaw: Vue.toRaw,
        normalizeChatTimestamp, getProviderHelpers: () => ({ matchesStreamingBlock: () => false }),
        // `defers: false`: the store before deferral, the reference a deferring store must match.
        visualItemsViews: Object.assign(createVisualItemsViews({ document: page }), defers ? {} : { defers: () => false }) }
    const actions = new Function(...Object.keys(deps), `const debugAppendSafeSessions = new Set()\n${helperSource}\nreturn { ${actionSource} }`)(...Object.values(deps))
    return defineStore(`deferred-list-${++storeId}`, {
        state: () => ({ sessions: { main: { id: 'main' }, other: { id: 'other' } }, sessionItems: {}, processStates: {},
            localState: { sessionEventSegments: {}, sessionEventRetired: {}, sessionInternalExpandedGroups: {}, failedSends: {},
                optimisticMessages: {}, sessionDebugOverride: {}, sessionExpandedGroups: {}, sessionDetailedBlocks: {}, streamingBlocks: {},
                sessionVisualItems: {}, visualItemCache: {}, sessionEventHistory: {} } }),
        actions: { ...actions, resolveInflightSends() {}, ensureSessionItemsCoverage: async () => true, _retireStreamingBlocks: () => [],
            areSessionItemsFetched: () => true },
    })(createPinia())
}
const message = line => ({ line_num: line, kind: 'user_message', display_level: DISPLAY_LEVEL.ALWAYS, group_head: null, group_tail: null,
    timestamp: null, content: JSON.stringify({ type: 'user', message: { content: `Message ${line}` } }) })

/**
 * App → KeepAlive → cached session view (provides `sessionActive`, flips it in its own
 * activation hook, after its children's) → list → scroller. `away()` caches the view, `back()`
 * reactivates it; `route` is the router's current route.
 */
function mount(t, { providesActive = true, defers = true } = {}) {
    const page = { hidden: false, listeners: [], addEventListener(type, listener) { this.listeners.push(listener) } }
    const store = makeStore(page, { defers })
    const route = reactive({ params: { sessionId: 'main' } })
    const showing = ref('view'), tabActive = ref(true), sessionId = ref('main')
    const log = []
    let list = null
    // Like VirtualScroller: it snapshots its anchor from the items it has when it suspends
    // (KeepAlive deactivation, or its container hidden by another tab: auto-suspend, after
    // the render), and restores it when it resumes (activation, or its container shown).
    const Scroller = {
        props: ['items'],
        setup(props, { expose }) {
            const suspended = ref(false)
            let kept = true
            const suspend = why => {
                if (suspended.value) return
                log.push(`${why} with ${props.items.length}`)
                suspended.value = true
            }
            const resume = () => {
                if (!suspended.value || !kept || !tabActive.value) return
                log.push(`resume with ${props.items.length}`)
                suspended.value = false
            }
            onDeactivated(() => { kept = false; suspend('suspend') })
            onActivated(() => { kept = true; resume() })
            watch(tabActive, shown => shown ? resume() : suspend('auto-suspend'), { flush: 'post' })
            expose({ suspended, isAtBottom: () => true, getScrollAnchor: () => null })
            return () => null
        },
    }
    const List = {
        props: ['viewActive', 'sessionId', 'parentSessionId'],
        setup(props) {
            const sessionActive = inject('sessionActive', ref(true))
            const scrollerRef = ref(null)
            const visualItems = computed(() => store.localState.sessionVisualItems[props.sessionId] || [])
            const entrance = { resetBaseline: () => log.push(`entrance baseline ${visualItems.value.length}`), clear() {} }
            listSetup(watch, nextTick, unref, ref, onActivated, onDeactivated, onBeforeUnmount, () => route, store, props, sessionActive,
                scrollerRef, visualItems, visualItems, entrance,
                // A suspended scroller ignores the jump (useVirtualScroll scrollToBottom).
                () => { if (!scrollerRef.value?.suspended) log.push(`bottom with ${visualItems.value.length}`) }, ref(false), () => false,
                () => {}, ref(false))
            list = { visualItems }
            return () => h(Scroller, { ref: scrollerRef, items: visualItems.value })
        },
    }
    const SessionView = {
        setup() {
            const isActive = ref(true)
            onActivated(() => { isActive.value = true })
            onDeactivated(() => { isActive.value = false })
            if (providesActive) provide('sessionActive', readonly(isActive))
            return () => h(List, { viewActive: (!providesActive || isActive.value) && tabActive.value, sessionId: sessionId.value })
        },
    }
    const Other = { render: () => null }
    const App = { render: () => h(KeepAlive, null, [showing.value === 'view' ? h(SessionView, { key: 'view' }) : h(Other, { key: 'other' })]) }
    const { renderer, root } = makeRenderer()
    const app = renderer.createApp(App)
    app.mount(root)
    let mounted = true
    const unmount = () => { if (mounted) app.unmount(); mounted = false }
    t.after(unmount)
    return {
        store, route, page, tabActive, sessionId, log, unmount,
        rows: () => list.visualItems.value.map(row => row.lineNum),
        async away({ route: viaRoute = true } = {}) {
            if (viaRoute) route.params.sessionId = 'other'
            showing.value = 'other'
            await flush()
        },
        async back({ route: viaRoute = true } = {}) {
            if (viaRoute) route.params.sessionId = 'main'
            showing.value = 'view'
            await flush()
        },
        setPageHidden(hidden) { page.hidden = hidden; for (const listener of page.listeners) listener() },
    }
}
test('the route coming back catches up before the scroller resumes and handlePostResume counts', async t => {
    const view = mount(t)
    view.store.addSessionItems('main', [message(1)])
    await flush()
    assert.deepEqual(view.rows(), [1])

    await view.away()
    view.store.addSessionItems('main', [message(2), message(3)])
    await flush()
    assert.deepEqual(view.rows(), [1], 'off screen, the rows wait')

    view.log.length = 0
    await view.back()
    assert.deepEqual(view.rows(), [1, 2, 3])
    assert.deepEqual(view.log, ['resume with 3', 'bottom with 3'],
        'the scroller resumes with the new rows, then handlePostResume follows them to the bottom once')
    view.store.addSessionItems('main', [message(4)])
    assert.deepEqual(view.rows(), [1, 2, 3, 4], 'shown again, the list is registered: rows recompute at once')
})

test('a reactivation without the route signal still catches up before handlePostResume, and takes the rows in', async t => {
    const view = mount(t)
    view.store.addSessionItems('main', [message(1)])
    await flush()
    // Cached by something other than a route change (the route stays on the session).
    await view.away({ route: false })
    view.store.addSessionItems('main', [message(2), message(3)])
    await flush()
    assert.deepEqual(view.rows(), [1])

    view.log.length = 0
    await view.back({ route: false })
    assert.deepEqual(view.rows(), [1, 2, 3])
    assert.deepEqual(view.log, ['resume with 1', 'entrance baseline 3', 'bottom with 3'],
        'handlePostResume sees the new rows; they do not enter, and the live follow does not scroll a second time')
})

test('a tab shown again takes the rows in without entering nor following them', async t => {
    const view = mount(t)
    view.store.addSessionItems('main', [message(1)])
    await flush()
    view.tabActive.value = false
    await flush()
    view.store.addSessionItems('main', [message(2)])
    await flush()
    assert.deepEqual(view.rows(), [1], 'another tab of the session is on screen: the rows wait')

    view.log.length = 0
    view.tabActive.value = true
    await flush()
    assert.deepEqual(view.rows(), [1, 2])
    assert.deepEqual(view.log, ['entrance baseline 2', 'resume with 2'],
        'no entrance, no follow (rows that arrived on another tab never did); the scroller resumes with them')
    view.store.addSessionItems('main', [message(3)])
    await flush()
    assert.deepEqual(view.log.slice(2), ['bottom with 3'], 'a row arriving now is followed')
})

test('the page shown again takes the rows in, and follows them as a visible page did', async t => {
    const view = mount(t)
    view.store.addSessionItems('main', [message(1)])
    await flush()
    view.setPageHidden(true)
    view.store.addSessionItems('main', [message(2)])
    await flush()
    assert.deepEqual(view.rows(), [1], 'a hidden page waits')

    view.log.length = 0
    view.setPageHidden(false)
    await flush()
    assert.deepEqual(view.rows(), [1, 2])
    assert.deepEqual(view.log, ['entrance baseline 2', 'bottom with 2'])
})

test('a list follows its session id and stops counting when it unmounts', async t => {
    const view = mount(t)
    view.store.addSessionItems('main', [message(1)])
    view.store.addSessionItems('other', [message(1)])
    await flush()
    view.sessionId.value = 'other'
    await flush()
    view.store.addSessionItems('main', [message(2)])
    view.store.addSessionItems('other', [message(2)])
    assert.deepEqual(view.store.localState.sessionVisualItems.main.map(row => row.lineNum), [1], 'the old session no longer counts it')
    assert.deepEqual(view.rows(), [1, 2], 'the new one does')
    view.sessionId.value = 'main'
    await flush()
    assert.deepEqual(view.rows(), [1, 2], 'back to the first: it catches up')
    view.unmount()
    view.store.addSessionItems('main', [message(3)])
    assert.deepEqual(view.store.localState.sessionVisualItems.main.map(row => row.lineNum), [1, 2], 'unmounted, it no longer counts')
})

test('a cached list counts as off screen even when its parents do not say so', async t => {
    // No session view provides `sessionActive` and the view flag stays on: only the list's
    // own KeepAlive state tells it is cached.
    const view = mount(t, { providesActive: false })
    view.store.addSessionItems('main', [message(1)])
    await flush()
    await view.away()
    view.store.addSessionItems('main', [message(2)])
    assert.deepEqual(view.rows(), [1], 'cached: the rows wait')
    await view.back()
    assert.deepEqual(view.rows(), [1, 2])
})

// At the bottom of the chat, the user opens another tab of the session; rows arrive; the
// user goes to another session, then comes back (to the chat, or to that other tab and then
// the chat). Nothing arrived since leaving: the list must not scroll to the bottom, exactly
// as a list whose rows were never deferred.
// The scroller's side of a log (the entrance has no reference counterpart: rows a list
// never deferred entered while hidden).
const scrolls = log => log.filter(entry => !entry.startsWith('entrance'))
async function leaveFromAnotherTab(t, { defers, returnTo }) {
    const view = mount(t, { defers })
    view.store.addSessionItems('main', [message(1)])
    await flush()
    view.tabActive.value = false
    await flush()
    view.store.addSessionItems('main', [message(2), message(3)])
    await flush()
    await view.away()
    if (returnTo === 'chat') view.tabActive.value = true
    await view.back()
    if (returnTo === 'tab') {
        view.tabActive.value = true
        await flush()
    }
    return view.log
}
for (const returnTo of ['chat', 'tab']) {
    test(`rows that waited behind another tab count as seen when the list leaves (return to the ${returnTo})`, async t => {
        const reference = await leaveFromAnotherTab(t, { defers: false, returnTo })
        assert.deepEqual(reference, ['auto-suspend with 1', 'resume with 3'], 'never deferred: no scroll on return')
        const deferred = await leaveFromAnotherTab(t, { defers: true, returnTo })
        assert.deepEqual(scrolls(deferred), reference)
        assert.deepEqual(deferred.filter(entry => entry.startsWith('entrance')), ['entrance baseline 3'], 'taken in on leaving')
    })
}

test('rows that waited behind another tab are taken in even when the list leaves without a route change', async t => {
    const run = async defers => {
        const view = mount(t, { defers })
        view.store.addSessionItems('main', [message(1)])
        await flush()
        view.tabActive.value = false
        await flush()
        view.store.addSessionItems('main', [message(2), message(3)])
        await flush()
        await view.away({ route: false })
        view.tabActive.value = true
        await view.back({ route: false })
        return { log: view.log, rows: view.rows() }
    }
    const reference = await run(false)
    assert.deepEqual(reference, { log: ['auto-suspend with 1', 'resume with 3'], rows: [1, 2, 3] })
    const deferred = await run(true)
    assert.deepEqual({ ...deferred, log: scrolls(deferred.log) }, reference)
})

test('rows that waited behind a hidden page are followed on return, as a visible list followed them', async t => {
    const run = async defers => {
        const view = mount(t, { defers })
        view.store.addSessionItems('main', [message(1)])
        await flush()
        view.setPageHidden(true)
        view.store.addSessionItems('main', [message(2), message(3)])
        await flush()
        await view.away()
        view.setPageHidden(false)
        await view.back()
        return view.log
    }
    // Never deferred: the list followed the rows while the page was hidden, then left at the bottom.
    assert.deepEqual(await run(false), ['bottom with 3', 'suspend with 3', 'resume with 3'])
    // Deferred: the rows wait; on return handlePostResume counts them as new and goes to the bottom.
    assert.deepEqual(await run(true), ['suspend with 1', 'resume with 3', 'bottom with 3'])
})
