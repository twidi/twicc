import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { parseInlineArtifactBlocks, publicationKey } from './publications.js'

const cases = JSON.parse(readFileSync(new URL('../../../fixtures/inline-artifacts/publications.json', import.meta.url)))
for (const fixture of cases) {
    test(fixture.name, () => {
        assert.deepEqual(parseInlineArtifactBlocks(fixture.text), fixture.expected)
    })
}

test('publication identity includes the source session and source occurrence', () => {
    const publication = { line_num: 42, text_block_index: 1, tag_offset: 15 }
    assert.equal(publicationKey('session-a', publication), '["session-a",42,1,15]')
    assert.notEqual(publicationKey('session-a', publication), publicationKey('session-b', publication))
    assert.notEqual(publicationKey('session-a', publication), publicationKey('session-a', { ...publication, tag_offset: 16 }))
})
