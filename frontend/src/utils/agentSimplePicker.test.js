import test from 'node:test'
import assert from 'node:assert/strict'
import {
    currentBlock, selectedRow, selectedEffort, sliderRows, enabledEfforts,
    effortForModel, providerStartSelection,
} from './agentSimplePicker.js'

const columns = ['low', 'medium', 'high', 'max'].map(effort => ({ effort, label: effort }))

function row(model, isLatest, enabledEfforts_, selected = null, extra = {}) {
    return {
        model, isLatest,
        cells: columns.map(c => ({
            effort: c.effort,
            enabled: enabledEfforts_.includes(c.effort),
            selected: selected === c.effort,
            ...(extra[c.effort] ?? {}),
        })),
    }
}

const all = ['low', 'medium', 'high', 'max']

test('picks the current block, else the first', () => {
    const a = { provider: 'a', rows: [] }
    const b = { provider: 'b', isCurrent: true, rows: [] }
    assert.equal(currentBlock([a, b]), b)
    assert.equal(currentBlock([a]), a)
    assert.equal(currentBlock([]), null)
})

test('finds the selected row and effort', () => {
    const block = { rows: [row('opus', true, all), row('sonnet', true, all, 'high')] }
    assert.equal(selectedRow(block).model, 'sonnet')
    assert.equal(selectedEffort(selectedRow(block)), 'high')
    assert.equal(selectedRow({ rows: [row('opus', true, all)] }), null)
})

test('lists latest usable models from weakest to strongest', () => {
    const block = {
        rows: [
            row('opus', true, all),
            row('opus-4.8', false, all),
            row('sonnet', true, all),
            row('gone', true, []),
            row('haiku', true, ['low']),
        ],
    }
    assert.deepEqual(sliderRows(block).map(r => r.model), ['haiku', 'sonnet', 'opus'])
})

test('lists the efforts a model supports', () => {
    assert.deepEqual(enabledEfforts(row('haiku', true, ['low', 'high'])), ['low', 'high'])
})

test('keeps the effort when the new model supports it', () => {
    assert.equal(effortForModel(row('m', true, all), columns, 'high'), 'high')
})

test('falls back to the closest supported effort, the lower on a tie', () => {
    assert.equal(effortForModel(row('m', true, ['low', 'high']), columns, 'max'), 'high')
    assert.equal(effortForModel(row('m', true, ['low', 'high']), columns, 'medium'), 'low')
    assert.equal(effortForModel(row('m', true, []), columns, 'medium'), null)
})

test('starts a provider switch on its default cell', () => {
    const block = {
        rows: [
            row('opus', true, all),
            row('sonnet', true, all, null, { medium: { isProviderDefault: true } }),
        ],
    }
    assert.deepEqual(providerStartSelection(block), { model: 'sonnet', effort: 'medium' })
})

test('starts a provider switch on its strongest model when no default is flagged', () => {
    const block = { rows: [row('opus', true, all), row('haiku', true, all)] }
    assert.deepEqual(providerStartSelection(block), { model: 'opus', effort: 'medium' })
})
