import test from 'node:test'
import assert from 'node:assert/strict'
import { setParsedContent } from './parsedContent.js'
import * as api from './scrollerLoadWindow.js'
test('bounded inclusive window excludes separators and synthetic content', () => {
    assert.equal(typeof api.collectMissingScrollerLines, 'function')
    const items = Array.from({ length: 9 }, (_, i) => ({ lineNum: i + 1, content: null }))
    items[2] = { lineNum: 'day', isDaySeparator: true }
    setParsedContent(items[3], { text: 'synthetic' })
    items[5].content = '{}'
    assert.deepEqual(api.collectMissingScrollerLines(items, { start: 3, end: 5 }, 1), [5, 7])
    assert.deepEqual(api.collectMissingScrollerLines([], { start: 0, end: 0 }, 50), [])
})
test('candidate equality and pagination cursor/canonical membership progress', () => {
    assert.equal(typeof api.sameScrollerLoadCandidates, 'function')
    assert.equal(api.sameScrollerLoadCandidates([2, 4], [2, 4]), true)
    assert.equal(api.sameScrollerLoadCandidates([2, 4], [4, 2]), false)
    const before = { cursor: 100, ids: new Set(['a']) }
    assert.equal(api.hasScrollerPaginationProgress(before, { cursor: 90, ids: new Set(['a']) }), true)
    assert.equal(api.hasScrollerPaginationProgress(before, { cursor: 100, ids: new Set(['a', 'b']) }), true)
    assert.equal(api.hasScrollerPaginationProgress(before, { cursor: 100, ids: new Set(['a']) }), false)
})
