import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { includeInlineArtifacts } from './shareOptions.js'

test('legacy and new shares default on; explicit false stays false', () => {
    assert.equal(includeInlineArtifacts(), true)
    assert.equal(includeInlineArtifacts({}), true)
    assert.equal(includeInlineArtifacts({ include_inline_artifacts: false }), false)
    assert.equal(includeInlineArtifacts({ include_inline_artifacts: true }), true)
})

test('dialog displays the exact scope and sends its switch state', () => {
    const source = readFileSync(new URL('../components/share/ShareDialog.vue', import.meta.url), 'utf8')
    assert.match(source, /Include inline artifacts/)
    assert.match(source, /Includes inline artifact pages, their assets, and their saved data\/ files\. Visitors cannot modify the saved data\./)
    assert.match(source, /include_inline_artifacts: form\.include_inline_artifacts/)
    assert.match(source, /includeInlineArtifacts\(e\?\.options\)/)
})
