import assert from 'node:assert/strict'
import test from 'node:test'

import { describeSettingsChange } from './settingsDiff.js'
import { createLabelsFor } from './providerLabels.js'

const helpers = {
    serviceProductLabel: 'Claude Code',
    getFieldLabel: (field) => ({ effort: 'Effort', selected_model: 'Model' }[field] ?? field),
    getChoiceLabel: (field, value) => (field === 'effort' && value === 'high' ? 'High' : null),
    getModelLabel: (model) => (model === 'opus' ? 'Opus' : ''),
}
const labelsFor = createLabelsFor((provider) => (provider === 'claude_code' ? helpers : null))

test('a known provider gives labels, an unknown or missing one gives null', () => {
    assert.equal(labelsFor('codex'), null)
    assert.equal(labelsFor(null), null)
    assert.equal(labelsFor(undefined), null)
    const labels = labelsFor('claude_code')
    assert.deepEqual([labels.field('effort'), labels.choice('effort', 'high'), labels.choice('effort', 'x'), labels.model('opus')], ['Effort', 'High', null, 'Opus'])
    assert.equal(labels.product, 'Claude Code')
})

test('the diff uses them and falls back to the raw value when the helper has no label', () => {
    const labels = labelsFor('claude_code')
    assert.equal(
        describeSettingsChange({ effort: ['low', 'high'], selected_model: ['mystery', 'opus'] }, '', labels).text,
        'Effort: low → High, Model: mystery → Opus',
    )
})
