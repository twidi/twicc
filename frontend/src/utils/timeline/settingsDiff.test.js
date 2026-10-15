import assert from 'node:assert/strict'
import test from 'node:test'

import { MAX_SHOWN_CHANGES, describeSettingsChange } from './settingsDiff.js'

const labels = {
    field: (field) => ({ selected_model: 'Model', effort: 'Effort', permission_mode: 'Permission', fast_mode: 'Fast mode' }[field] ?? field),
    choice: (field, value) => ({ effort: { high: 'High', max: 'Max' }, permission_mode: { default: 'Default', yolo: 'YOLO' } }[field]?.[value] ?? null),
    model: (value) => ({ sonnet: 'Sonnet', opus: 'Opus' }[value] ?? value),
}

test('a change reads "<Field>: <old> -> <new>" with provider labels, joined by commas', () => {
    const result = describeSettingsChange(
        { selected_model: ['sonnet', 'opus'], effort: ['high', 'max'] }, '', labels,
    )
    assert.equal(result.text, 'Model: Sonnet -> Opus, Effort: High -> Max')
    assert.equal(result.severity, 'neutral')
})

test('booleans read on/off and null reads default', () => {
    const result = describeSettingsChange({ fast_mode: [false, true], effort: [null, 'high'] }, '', labels)
    assert.equal(result.text, 'Fast mode: off -> on, Effort: default -> High')
})

test('an unknown provider shows raw field names and raw values, still with on/off and default', () => {
    const result = describeSettingsChange({ selected_model: ['sonnet', 'opus'], fast_mode: [true, null] }, '', null)
    assert.equal(result.text, 'selected_model: sonnet -> opus, fast_mode: on -> default')
})

test('a field outside the bundle keeps its raw name and raw values even with labels', () => {
    const result = describeSettingsChange({ brand_new: ['a', 'b'] }, '', labels)
    assert.equal(result.text, 'brand_new: a -> b')
})

test('a value with no choice label falls back to the raw value', () => {
    assert.equal(describeSettingsChange({ effort: ['low', 'high'] }, '', labels).text, 'Effort: low -> High')
})

test('at most three changes are written, then "+N more"; the full text lists all', () => {
    const changes = {
        selected_model: ['sonnet', 'opus'], effort: ['high', 'max'], permission_mode: ['default', 'yolo'],
        fast_mode: [false, true], thinking_enabled: [true, false],
    }
    const result = describeSettingsChange(changes, '', labels)
    assert.equal(MAX_SHOWN_CHANGES, 3)
    assert.equal(result.text, 'Model: Sonnet -> Opus, Effort: High -> Max, Permission: Default -> YOLO, +2 more')
    assert.match(result.full, /Fast mode: off -> on/)
    assert.match(result.full, /thinking_enabled: on -> off/)
    assert.ok(!result.full.includes('more'))
})

test('exactly three changes get no "+N more"', () => {
    const changes = { selected_model: ['a', 'b'], effort: ['low', 'high'], fast_mode: [false, true] }
    assert.ok(!describeSettingsChange(changes, '', labels).text.includes('more'))
})

test('the reason prefixes', () => {
    const changes = { effort: ['high', 'max'] }
    assert.equal(describeSettingsChange(changes, 'model_retired', labels).text, 'Model upgraded (retired): Effort: High -> Max')
    assert.equal(describeSettingsChange(changes, 'set_mode', labels).text, 'Permission mode set: Effort: High -> Max')
    assert.equal(describeSettingsChange(changes, 'switch-hybrid', labels).text, 'Hybrid switch: Effort: High -> Max')
    assert.equal(describeSettingsChange(changes, 'something-new', labels).text, 'Effort: High -> Max')
    assert.equal(describeSettingsChange(changes, '', labels).text, 'Effort: High -> Max')
    // An unknown reason has no prefix, even when it names an Object.prototype member.
    for (const reason of ['constructor', 'toString', '__proto__', 'hasOwnProperty']) {
        assert.equal(describeSettingsChange(changes, reason, labels).text, 'Effort: High -> Max', reason)
    }
})

test('a new permission mode that runs without asking is a warning', () => {
    assert.equal(describeSettingsChange({ permission_mode: ['default', 'bypassPermissions'] }, '', labels).severity, 'warning')
    assert.equal(describeSettingsChange({ permission_mode: ['default', 'yolo'] }, '', labels).severity, 'warning')
    assert.equal(describeSettingsChange({ permission_mode: ['yolo', 'default'] }, '', labels).severity, 'neutral')
})

test('empty, missing or malformed changes read "Settings changed"', () => {
    for (const changes of [undefined, null, {}, [], 'x', { effort: 'oops' }, { effort: ['only-one'] }]) {
        assert.equal(describeSettingsChange(changes, '', labels).text, 'Settings changed', JSON.stringify(changes))
    }
})
