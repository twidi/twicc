import { test } from 'node:test'
import assert from 'node:assert/strict'

import { vPopoverFocusFix } from './vPopoverFocusFix.js'

// Minimal stand-ins: the directive only needs addEventListener on the popover
// element and document.activeElement / focus() / blur() on the focused nodes.
function fakePopover() {
    const listeners = {}
    return {
        listeners,
        addEventListener: (type, fn) => { listeners[type] = fn },
        removeEventListener: (type) => { delete listeners[type] },
        fire(type, target) { listeners[type]?.({ target }) },
    }
}

function focusable(name, log) {
    return {
        name,
        focus() { log.push(`focus ${name}`); globalThis.document.activeElement = this },
        blur() { log.push(`blur ${name}`) },
    }
}

test('restores the clicked element when the popover itself closes', () => {
    const log = []
    const popover = fakePopover()
    const clicked = focusable('clicked', log)
    const trigger = focusable('trigger', log)
    globalThis.document = { body: {}, activeElement: clicked }
    vPopoverFocusFix.mounted(popover)

    popover.fire('wa-hide', popover)
    globalThis.document.activeElement = trigger // dialog.close() restored focus to the trigger
    popover.fire('wa-after-hide', popover)

    assert.deepEqual(log, ['blur trigger', 'focus clicked'])
    vPopoverFocusFix.unmounted(popover)
})

test('ignores wa-hide / wa-after-hide bubbling from a nested wa-select', () => {
    const log = []
    const popover = fakePopover()
    const select = focusable('select', log)
    const slider = focusable('slider', log)
    const nestedSelect = { name: 'nested wa-select' }
    globalThis.document = { body: {}, activeElement: select }
    vPopoverFocusFix.mounted(popover)

    // The select's listbox closes because the user clicked the slider.
    popover.fire('wa-hide', nestedSelect)
    globalThis.document.activeElement = slider
    popover.fire('wa-after-hide', nestedSelect)

    assert.deepEqual(log, [])
    assert.equal(globalThis.document.activeElement, slider)
    vPopoverFocusFix.unmounted(popover)
})
