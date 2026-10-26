import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { createPinia } from 'pinia'
import * as Vue from 'vue'

import { declarationsOf } from '../../../tests/helpers/cssDeclarations.js'
import { compileComponent, descendants, flush, makeRenderer } from '../../../tests/helpers/scrollerComponentHarness.js'
import { useTimelineFiltersStore } from '../../stores/timelineFilters.js'
import * as filtersModule from '../../utils/timeline/filters.js'
import { createPanelController } from '../../utils/timeline/panelController.js'

const SOURCE = readFileSync(new URL('./TimelineFilterBand.vue', import.meta.url), 'utf8')
const STYLE = SOURCE.slice(SOURCE.indexOf('<style'))

const SegmentedControl = compileComponent(new URL('../ui/SegmentedControl.vue', import.meta.url), {
    '../../composables/useGlideInk': { useGlideInk: () => {} },
})

// Claude Code names itself; the other provider has no helpers: its raw key is its label.
const providers = { getProviderHelpers: (provider) => (provider === 'claude_code' ? { constructor: { serviceProductLabel: 'Claude Code' } } : null) }

/**
 * Every call made on the panel controller of the band, by method: `[name, ...args]`. The elements in them come
 * from template refs: Vue makes a plain object (a test node) reactive there, so compare them with `Vue.toRaw`.
 */
let controllerCalls = []
const spiedController = {
    createPanelController: (...args) => {
        const controller = createPanelController(...args)
        return Object.fromEntries(Object.entries(controller).map(([name, method]) => [name, (...callArgs) => {
            controllerCalls.push([name, ...callArgs])
            return method(...callArgs)
        }]))
    },
}

const byClass = (root, name) => descendants(root, (node) => String(node.props.class ?? '').split(' ').includes(name))
const classesOf = (node) => String(node.props.class ?? '')
const textOf = (node) => (node.type === '#comment' ? '' : `${node.text ?? ''}${node.children.map(textOf).join('')}`)
const elementsOf = (node) => node.children.filter((child) => child.type !== '#comment' && child.type !== '#text')
/**
 * The same node. `assert.ok`, not `assert.equal`: a failure must not print two test nodes (circular trees with their
 * vnodes, too big to inspect).
 */
const assertSame = (actual, expected, message = 'not the same node') => assert.ok(actual === expected, message)
/** The same nodes, in order (see `assertSame`). */
const assertSameList = (actual, expected, message = 'not the same nodes') => assert.ok(actual.length === expected.length && actual.every((node, index) => node === expected[index]), message)

// ── The width model of the layout tests ──────────────────────────────────────────────────────────────────────
// The panel width, then the content widths: the title, the gaps, the views (full or short labels), the Filters
// button (with its label or its icon only) and its count. The line and the views + button line answer their
// `scrollWidth` / `clientWidth` from them, by level, as the browser lays them out (see the band's styles).
const MODEL = { title: 80, gap: 16, viewsFull: 330, viewsShort: 170, buttonFull: 90, buttonIcon: 40, count: 24 }

function levelOf(root) {
    const [line] = byClass(root, 'timeline-filter-line')
    return ['line', 'stacked', 'compact'].find((level) => classesOf(line).includes(`timeline-filter-line--${level}`))
}

function widthsOf(element, root, width) {
    const classes = classesOf(element).split(' ')
    const isLine = classes.includes('timeline-filter-line')
    const isControls = classes.includes('timeline-filter-controls')
    if (!isLine && !isControls) return { scroll: 0, client: 0 }
    const level = levelOf(root)
    const compact = level === 'compact'
    const hasCount = byClass(root, 'timeline-filter-count').length > 0
    const controls = (compact ? MODEL.viewsShort : MODEL.viewsFull) + MODEL.gap
        + (compact ? MODEL.buttonIcon : MODEL.buttonFull) + (hasCount ? MODEL.count : 0)
    const all = MODEL.title + MODEL.gap + controls
    if (isLine) return { scroll: level === 'line' ? Math.max(width, all) : width, client: width }
    // The views + button line: shrinkable and visible on the `line` level (its content overflows it), a full row
    // that clips on `stacked`, a full row that wraps on `compact`.
    if (level === 'line') return { scroll: controls, client: Math.max(0, Math.min(controls, width - MODEL.title - MODEL.gap)) }
    if (level === 'stacked') return { scroll: Math.max(width, controls), client: width }
    return { scroll: width, client: width }
}

/** Set a global for one test, restored after it. */
function setGlobal(t, name, value) {
    const had = Object.hasOwn(globalThis, name)
    const previous = globalThis[name]
    globalThis[name] = value
    t.after(() => {
        if (had) globalThis[name] = previous
        else delete globalThis[name]
    })
}

/** A fake requestAnimationFrame, set on `globalThis` for one test: the callbacks wait in `frames`. */
function installAnimationFrames(t) {
    const frames = []
    setGlobal(t, 'requestAnimationFrame', (callback) => frames.push(callback))
    setGlobal(t, 'cancelAnimationFrame', (id) => { frames[id - 1] = null })
    /** Run the frames asked so far (and the ones they ask), as the browser does before its next paints. */
    const runFrames = () => {
        for (let index = 0; index < frames.length; index++) {
            const callback = frames[index]
            frames[index] = null
            callback?.()
        }
        frames.length = 0
    }
    return { frames, runFrames }
}

/** A fake ResizeObserver, set on `globalThis` for one test. */
function installResizeObserver(t) {
    const observers = []
    const previous = globalThis.ResizeObserver
    globalThis.ResizeObserver = class {
        constructor(callback) {
            this.callback = callback
            this.targets = []
            this.disconnected = false
            observers.push(this)
        }

        observe(target) { this.targets.push(target) }

        disconnect() { this.disconnected = true }
    }
    t.after(() => {
        if (previous === undefined) delete globalThis.ResizeObserver
        else globalThis.ResizeObserver = previous
    })
    return observers
}

const Elsewhere = { setup: () => () => Vue.h('section', 'a session') }

/**
 * Mount one band (or two, `bands: 2`) in one app, inside the StickyBand element (`.sticky-band`), with a fresh
 * Pinia unless one is given. `keepAlive`: the band is the child of a KeepAlive, `setShown(false)` swaps it for
 * another view (it is deactivated, not unmounted). Returns the root, the store, its recorded actions, the focus
 * log and the props.
 */
async function mount(t, { pageId = 'p1', active = true, pinia = createPinia(), bands = 1, width = 1000, resizeObserver = false, keepAlive = false, stickyBand = true } = {}) {
    controllerCalls = []
    // The band reads the focused element when the panel closes.
    setGlobal(t, 'document', { activeElement: null })
    const { frames, runFrames } = installAnimationFrames(t)
    const observers = resizeObserver ? installResizeObserver(t) : []
    const store = useTimelineFiltersStore(pinia)
    const actions = []
    store.$onAction(({ name, args }) => { if (name !== 'filtersFor' && name !== 'ensure') actions.push([name, ...args]) })
    const Band = compileComponent(new URL('./TimelineFilterBand.vue', import.meta.url), {
        '../../stores/timelineFilters': { useTimelineFiltersStore: () => store },
        '../../providers': providers,
        '../../utils/timeline/filters': filtersModule,
        '../../utils/timeline/panelController': spiedController,
        '../ui/SegmentedControl.vue': SegmentedControl,
    })
    const focusLog = []
    const blurLog = []
    const view = { width }
    let rootNode = null
    // Every level the line element goes through, in order (a class write naming a level).
    const levelLog = []
    const { renderer, root } = makeRenderer(40, (element) => {
        element.props = new Proxy({}, {
            set(target, key, value) {
                const level = key === 'class' && /\btimeline-filter-line--(\w+)/.exec(String(value))?.[1]
                if (level && level !== target.class?.match(/\btimeline-filter-line--(\w+)/)?.[1]) levelLog.push(level)
                target[key] = value
                return true
            },
        })
        // The listeners added in code (`addEventListener`), as `[type, listener, capture]`.
        element.listeners = []
        element.addEventListener = (type, listener, capture) => { element.listeners.push([type, listener, capture]) }
        element.removeEventListener = (type, listener, capture) => {
            const index = element.listeners.findIndex((entry) => entry[0] === type && entry[1] === listener && entry[2] === capture)
            if (index >= 0) element.listeners.splice(index, 1)
        }
        element.closest = (selector) => {
            assert.match(selector, /^\.[\w-]+$/, 'the test renderer knows class selectors only')
            for (let current = element; current; current = current.parent) {
                if (classesOf(current).split(' ').includes(selector.slice(1))) return current
            }
            return null
        }
        element.contains = (other) => {
            for (let current = other; current; current = current.parent) if (current === element) return true
            return false
        }
        element.focus = () => { focusLog.push(element); globalThis.document.activeElement = element }
        element.blur = () => { blurLog.push(element) }
        Object.defineProperty(element, 'isConnected', {
            get: () => {
                for (let current = element; current; current = current.parent) if (current === rootNode) return true
                return false
            },
        })
        Object.defineProperty(element, 'scrollWidth', { get: () => widthsOf(element, rootNode, view.width).scroll })
        Object.defineProperty(element, 'clientWidth', { get: () => widthsOf(element, rootNode, view.width).client })
    })
    rootNode = root
    const props = Vue.reactive({ pageId, active })
    const bandRefs = []
    const shown = Vue.ref(true)
    const renderBands = () => Array.from({ length: bands }, (_, index) => Vue.h(Band, { ...props, ref: (instance) => { bandRefs[index] = instance } }))
    const app = renderer.createApp({
        render: () => {
            const content = keepAlive
                ? [Vue.h(Vue.KeepAlive, null, { default: () => (shown.value ? renderBands()[0] : Vue.h(Elsewhere)) })]
                : renderBands()
            return stickyBand ? Vue.h('div', { class: 'sticky-band glass-sticky' }, content) : Vue.h('div', { class: 'no-band' }, content)
        },
    })
    app.use(pinia)
    app.mount(root)
    let mounted = true
    t.after(() => { if (mounted) app.unmount() })
    await flush()
    /** Resize the panel: every observer fires, as the browser does after a layout, then the frames they ask run. */
    const resize = async (next) => {
        view.width = next
        for (const observer of observers) if (!observer.disconnected) observer.callback([])
        runFrames()
        await flush()
    }
    const setShown = async (value) => { shown.value = value; await flush() }
    const unmount = () => { app.unmount(); mounted = false }
    return { root, store, actions, focusLog, blurLog, props, bandRefs, observers, resize, view, levelLog, frames, runFrames, setShown, unmount }
}

const popoverOf = (root) => descendants(root, (node) => node.type === 'wa-popover')[0]
const filtersButtonOf = (root) => byClass(root, 'timeline-filter-button')[0]
const radiosOf = (root) => descendants(root, (node) => node.type === 'wa-radio')
const removeOf = (root, label) => byClass(root, 'timeline-filter-pill')
    .find((pill) => textOf(byClass(pill, 'timeline-filter-pill-name')[0]) === `${label}:`)
    ?.children.find((child) => classesOf(child).includes('timeline-filter-pill-remove'))

// ── The band line ──────────────────────────────────────────────────────────────────────────────────────────

test('the band line: the title, the three views bound to the store, the Filters button', async (t) => {
    const { root, store, actions } = await mount(t)
    const [line] = byClass(root, 'timeline-filter-line')
    assert.deepEqual(elementsOf(line).map(classesOf), ['timeline-filter-title', 'timeline-filter-controls'])
    assert.equal(textOf(byClass(line, 'timeline-filter-title')[0]), 'Timeline')
    const [views] = byClass(root, 'timeline-filter-views')
    assert.ok(byClass(views, 'segmented-control').length === 1, 'the views are a SegmentedControl')
    const radios = radiosOf(root)
    assert.deepEqual(radios.map((radio) => radio.props.value), ['all', 'without_turns', 'waits_problems'])
    assert.deepEqual(radios.map((radio) => textOf(radio).trim()), ['All', 'Without turns', 'Waits & problems'])
    assert.deepEqual(radios.map((radio) => radio.props.title), ['All', 'Without turns', 'Waits & problems'])
    assert.deepEqual(radios.map((radio) => radio.props['aria-label'] ?? null), [null, null, null], 'the label is the name')
    const [group] = descendants(root, (node) => node.type === 'wa-radio-group')
    assert.equal(group.props.label, 'View')
    assert.equal(group.props['.value'] ?? group.props.value, 'all')
    // Selecting a view goes through the store's action.
    group.props.onChange({ target: { value: 'waits_problems' }, currentTarget: { value: 'waits_problems' } })
    await flush()
    assert.ok(!actions.some(([name]) => name === 'setView'), 'a radio change bubbling to the group is ignored')
    const event = { target: group, currentTarget: group }
    group.value = 'waits_problems'
    group.props.onChange(event)
    await flush()
    assert.deepEqual(actions, [['setView', 'p1', 'waits_problems']])
    assert.equal(group.props['.value'] ?? group.props.value, 'waits_problems')
    assert.equal(store.filtersFor('p1').view, 'waits_problems')
})

test('the Filters button shows the count of checked panel values only when there is one', async (t) => {
    const { root, store } = await mount(t)
    const button = filtersButtonOf(root)
    assert.equal(button.type, 'wa-button')
    assert.equal(textOf(button).trim(), 'Filters')
    assert.equal(byClass(button, 'timeline-filter-count').length, 0)
    store.toggle('p1', 'families', 'processes')
    store.toggle('p1', 'authors', 'you')
    store.setView('p1', 'without_turns')
    store.setSession('p1', { id: 's1', title: 'One' })
    await flush()
    assert.equal(textOf(byClass(button, 'timeline-filter-count')[0]), '2', 'the view and the session do not count')
    store.resetPanel('p1')
    await flush()
    assert.equal(byClass(button, 'timeline-filter-count').length, 0)
})

// ── The panel ──────────────────────────────────────────────────────────────────────────────────────────────

test('the popover comes right after the Filters button (before the pills) and is anchored to it', async (t) => {
    const { root, store } = await mount(t)
    store.toggle('p1', 'families', 'turns')
    await flush()
    const button = filtersButtonOf(root)
    const popover = popoverOf(root)
    const siblings = elementsOf(button.parent)
    assertSame(siblings[siblings.indexOf(button) + 1], popover, 'the next element after the button')
    const order = descendants(root, (node) => node === button || node === popover || classesOf(node).includes('timeline-filter-pills'))
    assert.deepEqual(order.map((node) => node.type), ['wa-button', 'wa-popover', 'div'])
    assert.ok(button.props.id)
    assert.equal(popover.props.for, button.props.id)
})

test('the panel lists the three groups in constant order, with the family hints and the provider labels', async (t) => {
    const { root, store, actions } = await mount(t)
    const groups = byClass(popoverOf(root), 'timeline-filter-group')
    assert.deepEqual(groups.map((group) => textOf(byClass(group, 'timeline-filter-group-label')[0])), ['Events', 'Provider', 'Done by'])
    for (const group of groups) {
        const [label] = byClass(group, 'timeline-filter-group-label')
        assert.equal(group.props.role, 'group')
        assert.equal(group.props['aria-labelledby'], label.props.id)
    }
    const boxes = groups.map((group) => descendants(group, (node) => node.type === 'wa-checkbox'))
    assert.deepEqual(boxes[0].map((box) => textOf(box)), filtersModule.FAMILIES.map((family) => family.label))
    assert.deepEqual(boxes[0].map((box) => box.props.hint), filtersModule.FAMILIES.map((family) => family.hint))
    assert.deepEqual(boxes[1].map((box) => textOf(box)), ['Claude Code', 'codex'], 'the product name, else the raw key')
    assert.deepEqual(boxes[2].map((box) => textOf(box)), ['Unknown', 'Automatic', 'You', 'Agent via CLI/MCP', 'External MCP', "The session's agent"])
    assert.ok(boxes.flat().every((box) => box.props.checked === false))
    // Checking goes through the store's action, and the box shows the store's state.
    boxes[1][1].props.onChange({ target: boxes[1][1] })
    boxes[0][6].props.onChange({ target: boxes[0][6] })
    await flush()
    assert.deepEqual(actions, [['toggle', 'p1', 'providers', 'codex'], ['toggle', 'p1', 'families', 'twicc']])
    assert.equal(boxes[1][1].props.checked, true)
    assert.equal(boxes[0][6].props.checked, true)
    assert.deepEqual(store.filtersFor('p1').providers, ['codex'])
    // Reset clears the panel.
    const [reset] = descendants(byClass(popoverOf(root), 'timeline-filter-panel-actions')[0], (node) => node.type === 'wa-button')
    assert.equal(textOf(reset), 'Reset')
    reset.props.onClick()
    await flush()
    assert.deepEqual(actions.at(-1), ['resetPanel', 'p1'])
    assert.ok(boxes.flat().every((box) => box.props.checked === false))
})

test('the panel content scrolls inside a short window', () => {
    const panel = declarationsOf(STYLE, '.timeline-filter-panel')
    assert.equal(panel.get('max-height'), 'calc(100dvh - 8rem)')
    assert.equal(panel.get('overflow-y'), 'auto')
})

test('the Filters button id is unique per band and is the for of its own popover', async (t) => {
    const { root } = await mount(t, { bands: 2 })
    const buttons = byClass(root, 'timeline-filter-button')
    const popovers = descendants(root, (node) => node.type === 'wa-popover')
    assert.equal(buttons.length, 2)
    assert.notEqual(buttons[0].props.id, buttons[1].props.id)
    assert.deepEqual(popovers.map((popover) => popover.props.for), buttons.map((button) => button.props.id))
    // The group label ids too.
    const labelIds = byClass(root, 'timeline-filter-group-label').map((label) => label.props.id)
    assert.equal(new Set(labelIds).size, 6)
    assert.match(SOURCE, /const uid = useId\(\)/)
})

// ── The pills ──────────────────────────────────────────────────────────────────────────────────────────────

const LONG_TITLE = 'A'.repeat(200)

async function withPills(t, options) {
    const mounted = await mount(t, options)
    const { store } = mounted
    for (const value of ['sessions', 'processes', 'twicc']) store.toggle('p1', 'families', value)
    store.toggle('p1', 'providers', 'claude_code')
    store.toggle('p1', 'authors', 'you')
    store.setSession('p1', { id: 'session-1', title: LONG_TITLE })
    await flush()
    mounted.actions.length = 0
    return mounted
}

test('one pill per non-empty group plus the session, +N for the others, the full list as tooltip, then Clear all', async (t) => {
    const { root } = await withPills(t)
    const pills = byClass(root, 'timeline-filter-pill')
    assert.deepEqual(pills.map((pill) => textOf(byClass(pill, 'timeline-filter-pill-name')[0])), ['Events:', 'Provider:', 'Done by:', 'Session:'])
    assert.deepEqual(pills.map((pill) => textOf(byClass(pill, 'timeline-filter-pill-value')[0])), ['Sessions, Processes', 'Claude Code', 'You', LONG_TITLE])
    assert.deepEqual(pills.map((pill) => byClass(pill, 'timeline-filter-pill-more').map(textOf)), [['+1'], [], [], []])
    assert.deepEqual(pills.map((pill) => pill.props.title), ['Sessions, Processes, TwiCC', 'Claude Code', 'You', LONG_TITLE])
    assert.deepEqual(pills.map((pill) => byClass(pill, 'timeline-filter-pill-remove')[0].props['aria-label']),
        ['Remove the Events filter', 'Remove the Provider filter', 'Remove the Done by filter', 'Remove the Session filter'])
    const [container] = byClass(root, 'timeline-filter-pills')
    const last = elementsOf(container).at(-1)
    assert.equal(classesOf(last), 'timeline-filter-clear')
    assert.equal(textOf(last), 'Clear all')
})

test('no pill and no Clear all when nothing is checked and no session is filtered (the view is not a pill)', async (t) => {
    const { root, store } = await mount(t)
    store.setView('p1', 'without_turns')
    await flush()
    assert.equal(byClass(root, 'timeline-filter-pills').length, 0)
    assert.equal(byClass(root, 'timeline-filter-clear').length, 0)
})

test('a long pill value is cut with an ellipsis, the pills wrap: nothing scrolls sideways', () => {
    const value = declarationsOf(STYLE, '.timeline-filter-pill-value')
    assert.ok(value.has('max-width'))
    assert.equal(value.get('overflow'), 'hidden')
    assert.equal(value.get('text-overflow'), 'ellipsis')
    assert.equal(value.get('white-space'), 'nowrap')
    assert.equal(value.get('min-width'), '0')
    const pill = declarationsOf(STYLE, '.timeline-filter-pill')
    assert.equal(pill.get('min-width'), '0')
    assert.equal(pill.get('max-width'), '100%')
    const pills = declarationsOf(STYLE, '.timeline-filter-pills')
    assert.equal(pills.get('flex-wrap'), 'wrap')
    assert.equal(pills.get('min-width'), '0')
    // The native <button> of the remove control: Web Awesome's form-control height is reset.
    assert.equal(declarationsOf(STYLE, '.timeline-filter-pill-remove').get('height'), 'auto')
})

test('a pill remove control clears its group or the session filter', async (t) => {
    const { root, actions } = await withPills(t)
    removeOf(root, 'Provider').props.onClick()
    await flush()
    removeOf(root, 'Session').props.onClick()
    await flush()
    assert.deepEqual(actions, [['clearGroup', 'p1', 'providers'], ['clearSession', 'p1']])
    assert.deepEqual(byClass(root, 'timeline-filter-pill').map((pill) => textOf(byClass(pill, 'timeline-filter-pill-name')[0])), ['Events:', 'Done by:'])
})

test('focus after a pill removal: the next pill remove control, else the previous one, else the Filters button', async (t) => {
    const { root, focusLog } = await withPills(t)
    const doneBy = removeOf(root, 'Done by')
    const events = removeOf(root, 'Events')
    // A middle pill (Events, [Provider], Done by, Session): the next one, not the previous one.
    removeOf(root, 'Provider').props.onClick()
    await flush()
    assertSame(focusLog.at(-1), doneBy, 'the next pill of a middle one')
    assert.ok(doneBy.isConnected)
    // The last pill (Events, Done by, [Session]): the previous one.
    removeOf(root, 'Session').props.onClick()
    await flush()
    assertSame(focusLog.at(-1), doneBy, 'the previous pill of the last one')
    // The first pill ([Events], Done by): the next one.
    events.props.onClick()
    await flush()
    assertSame(focusLog.at(-1), doneBy, 'the next pill of the first one')
    // The only pill: the Filters button.
    doneBy.props.onClick()
    await flush()
    assertSame(focusLog.at(-1), filtersButtonOf(root), 'the Filters button')
    assert.equal(byClass(root, 'timeline-filter-pills').length, 0)
    // Every move went through the controller, after the render.
    assert.equal(controllerCalls.filter(([name]) => name === 'focus').length, 4)
})

test('Clear all clears everything and focuses the Filters button', async (t) => {
    const { root, actions, focusLog, store } = await withPills(t)
    const [clear] = byClass(root, 'timeline-filter-clear')
    clear.props.onClick()
    assert.equal(focusLog.length, 0, 'not before the render')
    await flush()
    assert.deepEqual(actions, [['clearAll', 'p1']])
    assert.deepEqual(filtersModule.toQuery(store.filtersFor('p1')), {})
    assertSameList(focusLog, [filtersButtonOf(root)])
})

test('the exposed focus methods go through the controller: the Filters button, the session pill remove control', async (t) => {
    const { root, bandRefs, focusLog } = await withPills(t)
    controllerCalls = []
    bandRefs[0].focusSessionPill()
    assertSameList(focusLog, [removeOf(root, 'Session')])
    bandRefs[0].focusFiltersButton()
    assertSameList(focusLog, [removeOf(root, 'Session'), filtersButtonOf(root)])
    assert.deepEqual(controllerCalls.map(([name]) => name), ['focus', 'focus'])
    assert.ok(controllerCalls.every(([, , popover]) => Vue.toRaw(popover) === popoverOf(root)))
})

// ── The popover handlers ───────────────────────────────────────────────────────────────────────────────────

test('the popover handlers are wired to the controller and ignore events of nested elements', async (t) => {
    const { root } = await mount(t)
    const popover = popoverOf(root)
    const nested = { name: 'a nested wa-checkbox' }
    const prevented = []
    const event = (target) => ({ target, currentTarget: popover, preventDefault: () => prevented.push(target) })
    controllerCalls = []
    popover.props.onWaShow(event(nested))
    popover.props.onWaHide(event(nested))
    popover.props.onWaAfterHide(event(nested))
    assert.deepEqual(controllerCalls.map(([name]) => name), [])
    const own = event(popover)
    popover.props.onWaShow(own)
    globalThis.document.activeElement = filtersButtonOf(root)
    popover.props.onWaHide(own)
    popover.props.onWaAfterHide(own)
    assert.deepEqual(controllerCalls.map(([name]) => name), ['onShow', 'onHide', 'onAfterHide'])
    assertSame(Vue.toRaw(controllerCalls[1][2].activeElement), filtersButtonOf(root))
    assertSame(Vue.toRaw(controllerCalls[2][1].button), filtersButtonOf(root))
})

/** The capture `click` listeners added on a node. */
const captureClicks = (node) => node.listeners.filter(([type, , capture]) => type === 'click' && capture === true)

/** The popover's own `wa-hide`, as Web Awesome dispatches it: returns whether the close was cancelled. */
function hideVetoed(popover) {
    const event = { target: popover, currentTarget: popover, prevented: false, preventDefault() { event.prevented = true } }
    popover.props.onWaHide(event)
    return event.prevented
}

test('the band-click listener is a capture listener on the StickyBand element itself, removed at unmount', async (t) => {
    const { root, unmount } = await mount(t)
    const [stickyBand] = byClass(root, 'sticky-band')
    const [band] = byClass(root, 'timeline-filter-band')
    assert.equal(captureClicks(stickyBand).length, 1)
    assert.equal(band.listeners.length, 0, 'nothing on the band root: a click on the padding would never reach it')
    assert.ok(!SOURCE.includes('@click.capture'), 'no template listener on a descendant')
    unmount()
    assert.equal(captureClicks(stickyBand).length, 0)
})

test('a click on the padding of the sticky band, on a view or on a pill keeps the panel open; the Filters button and the list do not', async (t) => {
    const { root, store } = await mount(t)
    store.toggle('p1', 'families', 'turns')
    await flush()
    const [stickyBand] = byClass(root, 'sticky-band')
    const [[, listener]] = captureClicks(stickyBand)
    const popover = popoverOf(root)
    popover.props.onWaShow({ target: popover, currentTarget: popover })
    controllerCalls = []
    // The real path: the listener registered on the StickyBand, called with the event's target.
    listener({ target: stickyBand })
    assertSame(Vue.toRaw(controllerCalls[0][2].band), stickyBand, 'the boundary is the StickyBand element')
    assertSame(Vue.toRaw(controllerCalls[0][2].button), filtersButtonOf(root))
    assert.equal(hideVetoed(popover), true, 'the padding')
    assert.equal(hideVetoed(popover), false, 'one shot')
    listener({ target: radiosOf(root)[1] })
    assert.equal(hideVetoed(popover), true, 'a view')
    listener({ target: byClass(root, 'timeline-filter-pill-remove')[0] })
    assert.equal(hideVetoed(popover), true, 'a pill remove control')
    listener({ target: filtersButtonOf(root) })
    assert.equal(hideVetoed(popover), false, 'the Filters button')
    // A click in the list does not reach this listener in a real DOM (its target is outside the StickyBand). Even
    // when a click outside the boundary reaches it, right after a band click armed the veto, it disarms it: the
    // `contains` boundary is checked, not only the listener's place.
    listener({ target: radiosOf(root)[2] })
    listener({ target: root })
    assert.equal(hideVetoed(popover), false, 'a target outside the StickyBand')
})

test('without a StickyBand around it, the listener and the boundary are the band root', async (t) => {
    const { root, unmount } = await mount(t, { stickyBand: false })
    const [band] = byClass(root, 'timeline-filter-band')
    const [[, listener]] = captureClicks(band)
    controllerCalls = []
    listener({ target: radiosOf(root)[0] })
    assertSame(Vue.toRaw(controllerCalls[0][2].band), band)
    unmount()
    assert.equal(captureClicks(band).length, 0)
})

test('a page detached by KeepAlive with the panel open closes it from the code; it stays closed on return', async (t) => {
    const { root, setShown } = await mount(t, { keepAlive: true })
    const popover = popoverOf(root)
    popover.open = true
    popover.props.onWaShow({ target: popover, currentTarget: popover })
    controllerCalls = []
    // A session opened: the page is deactivated while `active` is still true.
    await setShown(false)
    assert.equal(byClass(root, 'timeline-filter-band').length, 0, 'detached')
    assert.deepEqual(controllerCalls.map(([name]) => name), ['closeFromCode'])
    assert.equal(popover.open, false)
    await setShown(true)
    assertSame(popoverOf(root), popover, 'the same band, reactivated')
    assert.equal(popover.open, false)
    assert.deepEqual(controllerCalls.map(([name]) => name), ['closeFromCode'])
})

test('unmounting the band with the panel open closes it from the code', async (t) => {
    const { root, unmount } = await mount(t)
    const popover = popoverOf(root)
    popover.open = true
    popover.props.onWaShow({ target: popover, currentTarget: popover })
    controllerCalls = []
    unmount()
    assert.deepEqual(controllerCalls.map(([name]) => name), ['closeFromCode'])
    assert.equal(popover.open, false)
})

test('the document global of the band tests is restored after each test', () => {
    assert.ok(!Object.hasOwn(globalThis, 'document') || globalThis.document?.activeElement === undefined)
})

test('leaving the timeline closes the panel from the code; on return it stays closed', async (t) => {
    const { root, props } = await mount(t)
    const popover = popoverOf(root)
    popover.open = true
    popover.props.onWaShow({ target: popover, currentTarget: popover })
    controllerCalls = []
    props.active = false
    await flush()
    assert.deepEqual(controllerCalls.map(([name]) => name), ['closeFromCode'])
    assert.equal(popover.open, false)
    props.active = true
    await flush()
    assert.equal(popover.open, false)
    assert.deepEqual(controllerCalls.map(([name]) => name), ['closeFromCode'])
})

test('source pin: the band does not use v-popover-focus-fix (it would undo the focus targets)', () => {
    const template = SOURCE.slice(SOURCE.indexOf('<template>'), SOURCE.indexOf('<style'))
    assert.ok(!template.includes('v-popover-focus-fix'))
    assert.ok(!/^import .*(?:vPopoverFocusFix|directives\/)/m.test(SOURCE), 'no directive import')
    assert.match(SOURCE, /addEventListener\('click', onBandClick, true\)/)
    assert.match(SOURCE, /removeEventListener\('click', onBandClick, true\)/)
})

// ── Layout levels ──────────────────────────────────────────────────────────────────────────────────────────
// With the model: line content 532 px (title 80 + 16 + views 330 + 16 + button 90), views + button 436 px with
// full labels, 226 px in compact.

test('the lines and their children are observed', async (t) => {
    const { root, observers } = await mount(t, { resizeObserver: true })
    assert.equal(observers.length, 1)
    const expected = [
        byClass(root, 'timeline-filter-line')[0], byClass(root, 'timeline-filter-controls')[0],
        byClass(root, 'segmented-control')[0], filtersButtonOf(root),
    ]
    assert.equal(observers[0].targets.length, expected.length)
    expected.forEach((element, index) => assertSame(Vue.toRaw(observers[0].targets[index]), element, `target ${index}`))
})

test('line to stacked when the line overflows, stacked to compact when the views line overflows; back at the records', async (t) => {
    const { root, resize } = await mount(t, { resizeObserver: true, width: 600 })
    assert.equal(levelOf(root), 'line')
    await resize(532)
    assert.equal(levelOf(root), 'line', 'it fits exactly')
    await resize(531)
    assert.equal(levelOf(root), 'stacked')
    await resize(436)
    assert.equal(levelOf(root), 'stacked', 'the views line fits exactly')
    await resize(435)
    assert.equal(levelOf(root), 'compact')
    // Back: compact -> stacked at the views line record (436), stacked -> line at the line record (532).
    await resize(435.5)
    assert.equal(levelOf(root), 'compact')
    await resize(436)
    assert.equal(levelOf(root), 'stacked')
    await resize(531)
    assert.equal(levelOf(root), 'stacked')
    await resize(532)
    assert.equal(levelOf(root), 'line')
})

test('a big drop goes up two levels at once, a big rise comes back two levels', async (t) => {
    const { root, resize } = await mount(t, { resizeObserver: true, width: 600 })
    await resize(300)
    assert.equal(levelOf(root), 'compact')
    await resize(800)
    assert.equal(levelOf(root), 'line')
})

test('a down-then-up sequence writes no record on the way down', async (t) => {
    const { root, resize, levelLog } = await mount(t, { resizeObserver: true, width: 600 })
    await resize(500)
    await resize(400)
    assert.equal(levelOf(root), 'compact')
    // compact -> stacked at 450: nothing recorded, so the way back to `line` still needs the line record (532): the
    // band goes to `stacked` and stays there (no record of 450 that would send it to `line` and back).
    levelLog.length = 0
    await resize(450)
    assert.equal(levelOf(root), 'stacked')
    assert.deepEqual(levelLog, ['stacked'])
    await resize(430)
    assert.equal(levelOf(root), 'compact', 'up again, at the overflow of the full labels')
    await resize(436)
    assert.equal(levelOf(root), 'stacked', 'the record is still 436, not a width seen on the way down')
    await resize(531)
    assert.equal(levelOf(root), 'stacked')
    await resize(532)
    assert.equal(levelOf(root), 'line')
})

test('the count appearing at a fixed width moves the level, the count disappearing moves it back', async (t) => {
    const { root, store } = await mount(t, { resizeObserver: true, width: 540 })
    assert.equal(levelOf(root), 'line')
    store.toggle('p1', 'families', 'turns')
    await flush()
    assert.equal(levelOf(root), 'stacked', '556 px with the count')
    store.clearGroup('p1', 'families')
    await flush()
    assert.equal(levelOf(root), 'line')
})

test('the count disappearing from compact measures again from the first level', async (t) => {
    const { root, store, resize } = await mount(t, { resizeObserver: true, width: 600 })
    store.toggle('p1', 'families', 'turns')
    await flush()
    await resize(455)
    assert.equal(levelOf(root), 'compact', 'the views line with the count is 460 px')
    store.clearGroup('p1', 'families')
    await flush()
    assert.equal(levelOf(root), 'stacked', 'without the count the views line fits (436 px)')
})

test('the ResizeObserver callback never changes the level itself: it asks one frame, which measures', async (t) => {
    const { root, observers, view, frames, runFrames, levelLog } = await mount(t, { resizeObserver: true, width: 600 })
    view.width = 300
    observers[0].callback([])
    observers[0].callback([])
    // The callback and the microtask checkpoint after it: still inside the broadcast, no level change.
    await flush()
    assert.equal(levelOf(root), 'line')
    assert.equal(frames.filter(Boolean).length, 1, 'two callbacks, one frame')
    levelLog.length = 0
    runFrames()
    await flush()
    assert.equal(levelOf(root), 'compact', 'the frame measures, then the rendered level is measured again')
    assert.deepEqual(levelLog, ['stacked', 'compact'])
    assert.equal(frames.length, 0)
    assert.match(SOURCE, /observer = new ResizeObserver\(scheduleMeasure\)/)
})

test('a frame still asked at unmount is cancelled', async (t) => {
    const { observers, view, frames, unmount } = await mount(t, { resizeObserver: true, width: 600 })
    view.width = 300
    observers[0].callback([])
    assert.equal(frames.filter(Boolean).length, 1)
    unmount()
    assert.equal(frames.filter(Boolean).length, 0)
    assert.equal(observers[0].disconnected, true)
})

test('a second measure before the render of a level change reads nothing (the DOM is still the previous level)', async (t) => {
    const { root, observers, view, frames, levelLog } = await mount(t, { resizeObserver: true, width: 600 })
    // 440 px: the line overflows (532), the views line with full labels fits (436): `stacked`. Read on the DOM of
    // the `line` level, the views line would look overflowing (its content overflows its shrunk box) and the band
    // would wrongly go to `compact`.
    view.width = 440
    observers[0].callback([])
    const [frame] = frames
    levelLog.length = 0
    // Two measures in the same task, before Vue renders the first level change.
    frame()
    frame()
    await flush()
    assert.equal(levelOf(root), 'stacked')
    assert.deepEqual(levelLog, ['stacked'], 'no detour through compact')
})

test('a hidden band (no width) keeps its level', async (t) => {
    const { root, resize } = await mount(t, { resizeObserver: true, width: 400 })
    assert.equal(levelOf(root), 'compact')
    await resize(0)
    assert.equal(levelOf(root), 'compact')
})

test('the views and the Filters button by level: short labels and the hidden button label in compact, the same button', async (t) => {
    const { root, resize } = await mount(t, { resizeObserver: true, width: 600 })
    const button = filtersButtonOf(root)
    for (const [width, level] of [[600, 'line'], [500, 'stacked'], [300, 'compact']]) {
        await resize(width)
        assert.equal(levelOf(root), level)
        assertSame(filtersButtonOf(root), button, `${level}: the same button node`)
        assert.equal(textOf(button).trim(), 'Filters', `${level}: "Filters" stays in the accessible text`)
        const radios = radiosOf(root)
        if (level === 'compact') {
            assert.deepEqual(radios.map((radio) => textOf(radio).trim()), ['All', 'No turns', 'Waits'])
            assert.deepEqual(radios.map((radio) => radio.props.title), ['All', 'Without turns', 'Waits & problems'])
            assert.deepEqual(radios.map((radio) => radio.props['aria-label']), ['All', 'Without turns', 'Waits & problems'])
        } else {
            assert.deepEqual(radios.map((radio) => textOf(radio).trim()), ['All', 'Without turns', 'Waits & problems'])
        }
    }
    const hidden = declarationsOf(STYLE, '.timeline-filter-line--compact .timeline-filter-button-label')
    assert.notEqual(hidden.get('display'), 'none')
    assert.equal(hidden.get('position'), 'absolute')
    assert.equal(hidden.get('clip-path'), 'inset(50%)')
    assert.ok(!/\.timeline-filter-button-label \{/.test(STYLE.replace('.timeline-filter-line--compact .timeline-filter-button-label {', '')),
        'the label is hidden in compact only')
})

test('the declarations of each level', () => {
    const band = declarationsOf(STYLE, '.timeline-filter-band')
    assert.equal(band.get('flex'), '1 1 auto')
    assert.equal(band.get('min-width'), '0')
    assert.equal(band.get('padding-inline'), 'var(--wa-space-m)')

    const line = declarationsOf(STYLE, '.timeline-filter-line')
    assert.equal(line.get('display'), 'flex')
    assert.equal(line.get('flex-wrap'), 'nowrap')
    assert.equal(line.get('min-width'), '0')
    assert.equal(line.get('overflow'), 'clip')
    assert.equal(line.get('overflow-clip-margin'), '0.25rem')
    assert.equal(declarationsOf(STYLE, '.timeline-filter-line--stacked').get('flex-wrap'), 'wrap')
    assert.equal(declarationsOf(STYLE, '.timeline-filter-line--compact').get('flex-wrap'), 'wrap')

    assert.equal(declarationsOf(STYLE, '.timeline-filter-line--stacked .timeline-filter-title').get('flex-basis'), '100%')
    assert.equal(declarationsOf(STYLE, '.timeline-filter-line--compact .timeline-filter-title').get('flex-basis'), '100%')

    const controls = declarationsOf(STYLE, '.timeline-filter-controls')
    assert.equal(controls.get('display'), 'flex')
    assert.equal(controls.get('flex-wrap'), 'nowrap')
    assert.equal(controls.get('min-width'), '0')
    assert.equal(controls.get('overflow'), 'visible')
    const stacked = declarationsOf(STYLE, '.timeline-filter-line--stacked .timeline-filter-controls')
    assert.equal(stacked.get('flex'), '1 1 100%')
    assert.equal(stacked.get('flex-wrap'), 'nowrap')
    assert.equal(stacked.get('overflow'), 'clip')
    assert.equal(stacked.get('overflow-clip-margin'), '0.25rem')
    const compact = declarationsOf(STYLE, '.timeline-filter-line--compact .timeline-filter-controls')
    assert.equal(compact.get('flex'), '1 1 100%')
    assert.equal(compact.get('flex-wrap'), 'wrap')
    assert.equal(compact.get('overflow'), 'clip')
    assert.equal(compact.get('overflow-clip-margin'), '0.25rem')

    assert.equal(declarationsOf(STYLE, '.timeline-filter-views').get('flex'), 'none')
    assert.equal(declarationsOf(STYLE, '.timeline-filter-button').get('flex'), 'none')

    // Chrome computes a math function in overflow-clip-margin to 0px: only literals there.
    for (const match of STYLE.matchAll(/overflow-clip-margin:\s*([^;]+);/g)) assert.equal(match[1], '0.25rem')
    // The comment says where the value comes from.
    assert.match(STYLE, /--wa-focus-ring-width \(0\.25rem\) \+ --wa-focus-ring-offset \(0rem\)/)
})

test('the views and the button are the elements that carry the classes (the SegmentedControl root, the wa-button)', async (t) => {
    const { root } = await mount(t)
    const [controls] = byClass(root, 'timeline-filter-controls')
    const [views, button] = elementsOf(controls)
    assert.ok(classesOf(views).includes('segmented-control') && classesOf(views).includes('timeline-filter-views'))
    assertSame(button, filtersButtonOf(root))
})
