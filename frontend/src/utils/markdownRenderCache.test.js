import test from 'node:test'
import assert from 'node:assert/strict'
import { markdownReferenceContextKey, markdownBlockCacheKey } from './markdownRenderCache.js'

test('reference context uses complete deterministic definitions', () => {
    assert.equal(markdownReferenceContextKey(undefined), markdownReferenceContextKey({}))
    assert.equal(markdownReferenceContextKey({ B: { title: 'b', href: '/b' }, A: ['/a', 'a'] }), markdownReferenceContextKey({ A: ['/a', 'a'], B: { href: '/b', title: 'b' } }))
    for (const next of [{ A: { href: '/new', title: 'old' } }, { A: { href: '/old', title: 'new' } }]) {
        assert.notEqual(markdownReferenceContextKey({ A: { href: '/old', title: 'old' } }), markdownReferenceContextKey(next))
    }
})
test('block keys encode context, slash mode, Mermaid theme and exact source', () => {
    const key = (source, theme = 'dark', slashTag = false, referenceContext = '{}') => markdownBlockCacheKey({ source, theme, slashTag, referenceContext })
    assert.equal(key('plain'), key('plain', 'default'))
    assert.notEqual(key('```mermaid\na-->b\n```'), key('```mermaid\na-->b\n```', 'default'))
    assert.notEqual(key('/go'), key('/go', 'dark', true))
    assert.notEqual(key('x', 'dark', false, '["a","b"]'), key('x["a"', 'dark', false, '"b"]'))
    assert.notEqual(key('x'), key('x', 'dark', false, '{"A":"/new"}'))
})
