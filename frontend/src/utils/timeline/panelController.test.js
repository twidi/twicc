import assert from 'node:assert/strict'
import test from 'node:test'

import { createPanelController, panelMaxHeight } from './panelController.js'

/**
 * A small world: a document focus, elements that know their parent (for `contains`), the band, the Filters
 * button, a view, the session pill's remove control, a control of the list, and the popover (`open`).
 */
function world() {
    const log = []
    const doc = { activeElement: null }
    const element = (name, parent = null) => {
        const node = {
            name, parent, isConnected: true,
            contains(other) {
                for (let current = other; current; current = current.parent) if (current === node) return true
                return false
            },
            focus() { log.push(['focus', name]); doc.activeElement = node },
            blur() { log.push(['blur', name]); if (doc.activeElement === node) doc.activeElement = null },
        }
        return node
    }
    const band = element('band')
    const button = element('button', band)
    const buttonIcon = element('button-icon', button)
    const view = element('view', band)
    const pillRemove = element('pill-remove', band)
    const listControl = element('list-control')
    const popover = { open: true }
    const timers = []
    const controller = createPanelController({ setTimeout: (callback) => timers.push(callback) })
    const runTimers = () => { while (timers.length) timers.shift()() }
    const hideEvent = () => {
        const event = { prevented: false, preventDefault() { event.prevented = true } }
        return event
    }
    const click = (target) => controller.onBandClick({ target }, { band, button })
    /** The popover's own close: `wa-hide` (with the active element now), then `wa-after-hide` unless vetoed. */
    const hide = () => {
        const event = hideEvent()
        controller.onHide(event, { activeElement: doc.activeElement })
        return event
    }
    const afterHide = () => controller.onAfterHide({ button })
    return { log, doc, band, button, buttonIcon, view, pillRemove, listControl, popover, controller, timers, runTimers, click, hide, afterHide }
}

test('a band click on a view or a pill cancels the next wa-hide once', () => {
    for (const target of ['view', 'pillRemove']) {
        const w = world()
        w.controller.onShow()
        w.click(w[target])
        assert.equal(w.hide().prevented, true, `${target}: the close is cancelled`)
        assert.equal(w.hide().prevented, false, `${target}: the next close (no click) is not`)
    }
})

test('a click on the Filters button (or inside it) does not cancel the close', () => {
    for (const target of ['button', 'buttonIcon']) {
        const w = world()
        w.controller.onShow()
        w.click(w[target])
        assert.equal(w.hide().prevented, false, target)
    }
})

test('Escape (no click) is not cancelled', () => {
    const w = world()
    w.controller.onShow()
    assert.equal(w.hide().prevented, false)
})

test('the band-click flag does not outlive the click: a band click, the timeout, then a list click closes', () => {
    const w = world()
    w.controller.onShow()
    w.click(w.view)
    w.runTimers()
    w.click(w.listControl)
    assert.equal(w.hide().prevented, false)
})

test('a list click right after a band click is not cancelled (the flag is per click)', () => {
    const w = world()
    w.controller.onShow()
    w.click(w.view)
    w.click(w.listControl)
    assert.equal(w.hide().prevented, false)
    // The first click's timeout does not clear a later click's flag.
    const v = world()
    v.controller.onShow()
    v.click(v.listControl)
    v.click(v.view)
    v.timers.shift()()
    assert.equal(v.hide().prevented, true)
})

test('usual order, for each target: the target ends focused after the dialog moved the focus to the button', () => {
    for (const target of ['pillRemove', 'button']) {
        const w = world()
        w.controller.onShow()
        // The band's nextTick resolves before the document listener: the popover is still open.
        w.controller.focus(() => w[target], w.popover)
        w.popover.open = false
        assert.equal(w.hide().prevented, false)
        // The native dialog close focuses the anchor.
        w.button.focus()
        w.log.length = 0
        w.afterHide()
        assert.deepEqual(w.log, [['blur', 'button'], ['focus', w[target].name]], target)
        assert.equal(w.doc.activeElement, w[target], target)
    }
})

test('defensive order (the close already under way at the focus call): the target is focused last, the button not blurred after it', () => {
    for (const target of ['pillRemove', 'button']) {
        const w = world()
        w.controller.onShow()
        w.popover.open = false
        w.controller.focus(() => w[target], w.popover)
        assert.equal(w.doc.activeElement, w[target], 'focused at once')
        w.hide()
        w.button.focus()
        w.log.length = 0
        w.afterHide()
        assert.deepEqual(w.log, [['focus', w[target].name]], target)
        assert.equal(w.doc.activeElement, w[target])
    }
})

test('a vetoed close stores no target: nothing is focused by wa-after-hide', () => {
    const w = world()
    w.controller.onShow()
    w.click(w.pillRemove)
    w.popover.open = false
    w.controller.focus(() => w.pillRemove, w.popover)
    assert.equal(w.hide().prevented, true)
    w.popover.open = true
    w.log.length = 0
    w.afterHide()
    assert.deepEqual(w.log, [])
})

test('a focus call while the panel is closed focuses at once', () => {
    const w = world()
    w.popover.open = false
    w.controller.focus(() => w.pillRemove, w.popover)
    assert.deepEqual(w.log, [['focus', 'pill-remove']])
    w.controller.focus(() => null, w.popover)
    assert.deepEqual(w.log, [['focus', 'pill-remove']], 'a missing target is skipped')
})

test('a list click on another control: wa-hide saves it, wa-after-hide blurs the button and focuses it', () => {
    const w = world()
    w.controller.onShow()
    w.listControl.focus()
    w.click(w.listControl)
    w.popover.open = false
    w.hide()
    w.button.focus()
    w.log.length = 0
    w.afterHide()
    assert.deepEqual(w.log, [['blur', 'button'], ['focus', 'list-control']])
})

test('a saved element no longer connected is not focused', () => {
    const w = world()
    w.controller.onShow()
    w.listControl.focus()
    w.hide()
    w.listControl.isConnected = false
    w.button.focus()
    w.log.length = 0
    w.afterHide()
    assert.deepEqual(w.log, [])
    assert.equal(w.doc.activeElement, w.button)
})

test('closeFromCode with the popover open closes even right after a band click, and touches no focus', () => {
    const w = world()
    w.controller.onShow()
    w.click(w.view)
    w.listControl.focus()
    w.controller.closeFromCode(w.popover)
    assert.equal(w.popover.open, false)
    assert.equal(w.hide().prevented, false)
    w.button.focus()
    w.log.length = 0
    w.afterHide()
    assert.deepEqual(w.log, [])
})

test('the code close flag lives until its wa-after-hide only: a later band click vetoes again', () => {
    const w = world()
    w.controller.onShow()
    w.controller.closeFromCode(w.popover)
    w.hide()
    w.afterHide()
    w.runTimers()
    w.popover.open = true
    w.controller.onShow()
    w.click(w.view)
    assert.equal(w.hide().prevented, true)
})

test('closeFromCode with the popover closed sets nothing: the next band-click veto still works', () => {
    const w = world()
    w.popover.open = false
    w.controller.closeFromCode(w.popover)
    assert.equal(w.popover.open, false)
    w.popover.open = true
    w.controller.onShow()
    w.click(w.view)
    assert.equal(w.hide().prevented, true)
})

test('the module is pure: no Vue, no DOM global', async () => {
    const { readFileSync } = await import('node:fs')
    const source = readFileSync(new URL('./panelController.js', import.meta.url), 'utf8')
    assert.ok(!/^import /m.test(source), 'no import')
    assert.ok(!/\b(?:document|window)\./.test(source.replace(/\/\/.*$/gm, '')), 'no document or window')
})

test('panelMaxHeight: the room under the button, minus the popover chrome, never under 10rem', () => {
    // 16px root: chrome = 8 + 2 + 16 × 4.125 = 76px.
    assert.equal(panelMaxHeight({ anchorBottom: 200, viewportHeight: 1000, remPx: 16 }), 724)
    assert.equal(panelMaxHeight({ anchorBottom: 900, viewportHeight: 1000, remPx: 16 }), 160, 'the 10rem floor')
    assert.equal(panelMaxHeight({ anchorBottom: 200, viewportHeight: 1000, remPx: 20 }), 707, 'follows the font size')
    assert.equal(panelMaxHeight({ anchorBottom: undefined, viewportHeight: 1000, remPx: 16 }), null)
    assert.equal(panelMaxHeight({ anchorBottom: 200, viewportHeight: 1000, remPx: Number.NaN }), null)
})
