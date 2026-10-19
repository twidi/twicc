import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { describeSettingsChange } from './settingsDiff.js'
import { createLabelsFor } from './providerLabels.js'

// Shaped like the app's helpers: an instance, the product name a static field of its class.
const helpers = new (class {
    static serviceProductLabel = 'Claude Code'
    getFieldLabel(field) { return { effort: 'Effort', selected_model: 'Model' }[field] ?? field }
    getChoiceLabel(field, value) { return field === 'effort' && value === 'high' ? 'High' : null }
    getModelLabel(model) { return model === 'opus' ? 'Opus' : '' }
})()
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

// The app's helpers are INSTANCES (`new ClaudeCodeHelpers()`) and the product name is a STATIC field of their class:
// a plain-object fake would hide a read on the instance.
class FakeProviderHelpers {
    static serviceProductLabel = 'Codex'
    getFieldLabel(field) { return field }
    getChoiceLabel() { return null }
    getModelLabel() { return '' }
}

test('the product name is read from the class of a helpers instance, as the app hands them', () => {
    const labels = createLabelsFor(() => new FakeProviderHelpers())('codex')
    assert.equal(labels.product, 'Codex')
    assert.equal(createLabelsFor(() => new (class extends FakeProviderHelpers { static serviceProductLabel = null })())('x').product, null)
})

test('the real provider helpers are instances whose class carries the product name', () => {
    // providers/index.js cannot be imported under node:test: its imports are extensionless Vite specifiers and the
    // helpers pull in the Pinia stores. The source shape is checked instead: the registry maps each provider to an
    // instance, and the product name is a static field of its class.
    const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
    const index = read('../../providers/index.js')
    for (const [dir, className, instance, product] of [
        ['claude_code', 'ClaudeCodeHelpers', 'claudeCodeHelpers', 'Claude Code'],
        ['codex', 'CodexHelpers', 'codexHelpers', 'Codex'],
    ]) {
        const source = read(`../../providers/${dir}/helpers.js`)
        const body = source.slice(source.indexOf(`export class ${className} `))
        assert.match(body, new RegExp(`\\n    static serviceProductLabel = '${product}'\\n`), className)
        assert.match(source, new RegExp(`export const ${instance} = new ${className}\\(\\)`), instance)
        assert.match(index, new RegExp(`\\[${className}\\.provider\\]: ${instance},`), `registry entry of ${className}`)
    }
})
