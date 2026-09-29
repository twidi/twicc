// Run with: node --test src/utils/tabCrossfade.test.js (from the frontend dir)
// Tab-panel crossfade (visual refresh step 5c, docs/plans/2026-09-29-list-cascade-scheme-reveal-design.md §12.4):
// the setActiveTab wrapper armed by a user gesture on the bar's own tabs, with a fake
// tab group and a fake dispatch (capture, Web Awesome's handler, bubble).
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

import { installTabCrossfade } from './tabCrossfade.js'

/** A fake element: a tag, classes and a parent; closest() matches `tag` or `.class` lists. */
class FakeElement {
    constructor(tag, { parent = null, classes = [], disabled = false } = {}) {
        this.tagName = tag.toUpperCase()
        this.parent = parent
        this.classes = new Set(classes)
        this.disabled = disabled
    }

    matches(selector) {
        return selector.split(',').map((s) => s.trim()).some((s) =>
            s.startsWith('.') ? this.classes.has(s.slice(1)) : this.tagName.toLowerCase() === s)
    }

    closest(selector) {
        for (let node = this; node; node = node.parent) {
            if (node.matches(selector)) return node
        }
        return null
    }
}

/** The fake wa-tab-group: setActiveTab on the prototype, asserting `this`. */
class FakeGroup extends FakeElement {
    constructor() {
        super('wa-tab-group')
        this.activeTab = null
        this.calls = []
        this.listeners = []
    }

    setActiveTab(tab, options) {
        assert.equal(this, group, 'the original runs with this === host')
        this.calls.push({ tab, options })
        this.activeTab = tab
    }

    addEventListener(type, listener, capture) {
        this.listeners.push({ type, listener, capture: !!capture })
    }

    removeEventListener(type, listener, capture) {
        this.listeners = this.listeners.filter((l) => !(l.type === type && l.listener === listener && l.capture === !!capture))
    }
}

let group

function makeEnv() {
    const env = { now: 0, timers: new Map(), nextId: 1 }
    env.setTimeout = (fn, ms) => {
        const id = env.nextId++
        env.timers.set(id, { fn, at: env.now + ms })
        return id
    }
    env.clearTimeout = (id) => { env.timers.delete(id) }
    env.advance = (ms) => {
        const target = env.now + ms
        for (;;) {
            const due = [...env.timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0]
            if (!due) break
            env.timers.delete(due[0])
            env.now = due[1].at
            due[1].fn()
        }
        env.now = target
    }
    return env
}

function setup() {
    group = new FakeGroup()
    const other = new FakeGroup()
    const tabA = new FakeElement('wa-tab', { parent: group })
    const tabB = new FakeElement('wa-tab', { parent: group })
    const tabC = new FakeElement('wa-tab', { parent: group })
    const disabled = new FakeElement('wa-tab', { parent: group, disabled: true })
    const foreign = new FakeElement('wa-tab', { parent: other })
    const panel = new FakeElement('wa-tab-panel', { parent: group })
    const inPanel = new FakeElement('a', { parent: panel })
    const closeIcon = new FakeElement('wa-icon', { parent: tabB, classes: ['tab-close-icon'] })
    const dropdown = new FakeElement('wa-dropdown', { parent: tabB })
    const inDropdown = new FakeElement('wa-button', { parent: dropdown })
    group.activeTab = tabA
    const env = makeEnv()
    const starts = []
    const runs = []
    const run = (update, options) => { runs.push({ update, options }) }
    const uninstall = installTabCrossfade(group, { onStart: () => starts.push(1), run, env })
    const host = group

    /** Capture listeners, then Web Awesome's handler, then bubble listeners. */
    function dispatch(type, event, waHandler = () => {}) {
        for (const l of host.listeners.filter((x) => x.type === type && x.capture)) l.listener(event)
        waHandler(event)
        for (const l of host.listeners.filter((x) => x.type === type && !x.capture)) l.listener(event)
    }
    const click = (target, waHandler) => dispatch('click', { target }, waHandler)
    const keydown = (target, key, waHandler) => dispatch('keydown', { target, key }, waHandler)

    return { host, tabA, tabB, tabC, disabled, foreign, panel, inPanel, closeIcon, inDropdown, env, starts, runs, uninstall, click, keydown }
}

test('33. a click on a tab of the bar: onStart, run(kind tab, settle), the original inside the update', () => {
    const { host, tabB, starts, runs, click } = setup()
    click(tabB, () => host.setActiveTab(tabB, { scrollBehavior: 'smooth' }))
    assert.equal(starts.length, 1)
    assert.equal(runs.length, 1)
    assert.equal(runs[0].options.kind, 'tab')
    assert.equal(runs[0].options.settle, true)
    assert.equal(host.calls.length, 0, 'not before the update')
    runs[0].update()
    assert.equal(host.calls.length, 1)
    assert.equal(host.calls[0].tab, tabB)
    assert.deepEqual(host.calls[0].options, { scrollBehavior: 'smooth' })
})

test('34. pass-through cases: the original at once, no onStart', () => {
    const cases = [
        ['no gesture', (c) => c.host.setActiveTab(c.tabB), (c) => c.tabB],
        ['same tab', (c) => c.click(c.tabA, () => c.host.setActiveTab(c.tabA)), (c) => c.tabA],
        ['disabled tab', (c) => c.click(c.disabled, () => c.host.setActiveTab(c.disabled)), (c) => c.disabled],
        ['tab of another group', (c) => c.click(c.tabB, () => c.host.setActiveTab(c.foreign)), (c) => c.foreign],
        ['another tab than the clicked one', (c) => c.click(c.tabB, () => c.host.setActiveTab(c.tabC)), (c) => c.tabC],
        ['after the bubble-phase disarm', (c) => { c.click(c.tabB); c.host.setActiveTab(c.tabB) }, (c) => c.tabB],
        ['after the macrotask fallback', (c) => {
            for (const l of c.host.listeners.filter((x) => x.type === 'click' && x.capture)) l.listener({ target: c.tabB })
            c.env.advance(0)
            c.host.setActiveTab(c.tabB)
        }, (c) => c.tabB],
    ]
    for (const [label, act, expected] of cases) {
        const c = setup()
        act(c)
        assert.equal(c.starts.length, 0, `${label}: no onStart`)
        assert.equal(c.runs.length, 0, `${label}: no run`)
        assert.equal(c.host.calls.length, 1, `${label}: the original at once`)
        assert.equal(c.host.calls[0].tab, expected(c), label)
    }
})

test('35. only a gesture on a tab of the bar arms', async () => {
    const notArmed = [
        ['a click in a panel, then a microtask switch', async (c) => {
            c.click(c.inPanel)
            await Promise.resolve()
            c.host.setActiveTab(c.tabB)
        }],
        ['a click on the close icon', (c) => c.click(c.closeIcon, () => c.host.setActiveTab(c.tabB))],
        ['a click in a dropdown of a tab', (c) => c.click(c.inDropdown, () => c.host.setActiveTab(c.tabB))],
        ['a keydown in a panel', (c) => c.keydown(c.inPanel, 'Enter', () => c.host.setActiveTab(c.tabB))],
        ['another key on a tab', (c) => c.keydown(c.tabA, 'a', () => c.host.setActiveTab(c.tabB))],
    ]
    for (const [label, act] of notArmed) {
        const c = setup()
        await act(c)
        assert.equal(c.starts.length, 0, `${label}: no onStart`)
        assert.equal(c.host.calls.length, 1, `${label}: the original at once`)
    }
    const c = setup()
    c.keydown(c.tabA, 'ArrowRight', () => c.host.setActiveTab(c.tabC))
    assert.equal(c.starts.length, 1, 'ArrowRight on a tab arms for any tab of the bar')
    assert.equal(c.runs.length, 1)
})

test('36. uninstall restores the prototype method and removes the four listeners', () => {
    const { host, uninstall } = setup()
    assert.ok(Object.hasOwn(host, 'setActiveTab'))
    assert.equal(host.listeners.length, 4)
    assert.deepEqual(host.listeners.map((l) => `${l.type}:${l.capture}`).sort(),
        ['click:false', 'click:true', 'keydown:false', 'keydown:true'])
    uninstall()
    assert.ok(!Object.hasOwn(host, 'setActiveTab'))
    assert.equal(host.setActiveTab, FakeGroup.prototype.setActiveTab)
    assert.equal(host.listeners.length, 0)
})

// ── Wiring (file scans) ─────────────────────────────────────────────────────

const srcDir = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (rel) => readFileSync(join(srcDir, rel), 'utf8')
/** An element's opening tag (attribute values may hold `>`). */
const openingTags = (sfc, name) => [...sfc.matchAll(new RegExp(`<${name}\\b(?:[^>"']|"[^"]*"|'[^']*')*>`, 'g'))].map((m) => m[0])
/** The body of a top-level `function name(` in a script, by brace count. */
function functionBody(text, name) {
    const start = text.indexOf(`function ${name}(`)
    assert.ok(start >= 0, `function ${name}`)
    const open = text.indexOf('{', start)
    let depth = 0
    for (let i = open; i < text.length; i++) {
        if (text[i] === '{') depth++
        else if (text[i] === '}' && --depth === 0) return text.slice(start, i + 1)
    }
    throw new Error('unbalanced braces')
}

test('37. SessionView.vue: the center bar crossfades, the overlay handlers transition, the drag does not', () => {
    const sfc = read('views/SessionView.vue')
    const center = openingTags(sfc, 'TabBar').find((tag) => tag.includes('ref="sessionTabsRef"'))
    assert.ok(center, 'the center TabBar')
    assert.match(center, /\scrossfade[\s>]/)
    assert.match(center, /@crossfade-start="cancelPaneFocus"/)
    const [layoutTag] = openingTags(sfc, 'SessionLayout')
    assert.match(layoutTag, /@crossfade-start="cancelPaneFocus"/)
    assert.ok(functionBody(sfc, 'onOverlayActivate').includes('runOverlayTransition('))
    assert.ok(functionBody(sfc, 'onOverlayDismiss').includes('runOverlayTransition('))
    const drag = functionBody(sfc, 'onLayoutTabDragStart')
    assert.ok(drag.includes('overlayDismissNow()'))
    assert.ok(!drag.includes('onOverlayDismiss('))
})

test('38. DockRegion, SessionLayout, LayoutOverlay and TabBar wiring', () => {
    const dock = read('components/session/layout/DockRegion.vue')
    assert.match(dock.match(/defineEmits\(\[([^\]]*)\]\)/)[1], /'crossfade-start'/)
    const [dockBar] = openingTags(dock, 'TabBar')
    assert.match(dockBar, /\scrossfade\s/)
    assert.match(dockBar, /@crossfade-start="emit\('crossfade-start'\)"/)

    const layout = read('components/session/layout/SessionLayout.vue')
    assert.match(layout.match(/defineEmits\(\[([^\]]*)\]\)/)[1], /'crossfade-start'/)
    const regions = openingTags(layout, 'DockRegion')
    assert.equal(regions.length, 2, 'the maximized and the normal DockRegion')
    for (const tag of regions) assert.match(tag, /@crossfade-start="emit\('crossfade-start'\)"/)

    for (const tag of openingTags(read('components/session/layout/LayoutOverlay.vue'), 'TabBar')) {
        assert.ok(!/\scrossfade[\s>=]/.test(tag), 'the overlay bar does not crossfade')
    }
    assert.ok(!read('components/ui/TabBar.vue').includes('startViewTransition'))
    assert.ok(!read('utils/tabCrossfade.js').includes('startViewTransition'))

    // The overlay's pooled iframe cell is tagged, so it gets its own sliding group (§12.5).
    assert.match(read('components/frames/FrameHost.vue'), /'frame-cell--overlay': pool\.frames\[id\]\.zTier === 'overlay'/)
    // The slide offsets: exactly the gutter edges the resolver produces (§12.5).
    assert.match(read('views/SessionView.vue'),
        /const SLIDE_OFFSETS = \{ right: \['100vw', '0px'\], left: \['-100vw', '0px'\], bottom: \['0px', '100vh'\] \}/)
})
