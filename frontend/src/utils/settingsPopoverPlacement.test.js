import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    SETTINGS_POPOVER_WIDTH_RATIO,
    SETTINGS_POPOVER_MAX_WIDTH,
    SETTINGS_POPOVER_MARGIN,
    resolveSettingsPlacement,
} from './settingsPopoverPlacement.js'

test('shared sizing constants', () => {
    assert.equal(SETTINGS_POPOVER_WIDTH_RATIO, 0.9)
    assert.equal(SETTINGS_POPOVER_MAX_WIDTH, 700)
    assert.equal(SETTINGS_POPOVER_MARGIN, 16)
})

for (const [preferred, anchorRight, innerWidth, expected] of [
    ['right-end', 50, 1200, 'right-end'],
    ['right-end', 50, 360, 'top'],
    ['top', 50, 360, 'top'],
    ['right-start', 50, 660, 'right-start'],
    ['right-start', 50, 659, 'top'],
    ['left-end', 50, 360, 'left-end'],
]) {
    test(`${preferred} at ${innerWidth}px resolves to ${expected}`, () => {
        assert.equal(resolveSettingsPlacement({ preferred, anchorRight, innerWidth }), expected)
    })
}
