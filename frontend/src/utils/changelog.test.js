import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildCombinedVersion } from './changelog.js'

const release = (version, entries) => ({ version, entries })
const entry = (category, text) => ({ category, text })
const versions = [
    release('Unreleased', [entry('added', 'Future work')]),
    release('1.6.0', [entry('added', 'Beyond available update')]),
    release('1.5.0', [entry('summary', 'Latest summary'), entry('fixed', 'Latest fix'), entry('added', 'Latest feature')]),
    release('1.4.0', [entry('summary', 'Intermediate summary'), entry('added', 'Intermediate feature')]),
    release('1.2.0', [entry('added', 'Already installed')]),
]

test('available updates include intermediate releases and exclude installed and future entries', () => {
    const combined = buildCombinedVersion(versions, '1.2.0', '1.5.0')
    assert.equal(combined._previousVersion, '1.2.0')
    assert.equal(combined._currentVersion, '1.5.0')
    assert.deepEqual(combined.entries.map(e => [e.category, e._sourceVersion, e.text]), [
        ['summary', '1.4.0', 'Intermediate summary'],
        ['summary', '1.5.0', 'Latest summary'],
        ['added', '1.4.0', 'Intermediate feature'],
        ['added', '1.5.0', 'Latest feature'],
        ['fixed', '1.5.0', 'Latest fix'],
    ])
})

test('post-install grouping preserves the same version range', () => {
    const combined = buildCombinedVersion(versions, '1.2.0', '1.4.0')
    assert.deepEqual(combined.entries.map(e => e.text), ['Intermediate summary', 'Intermediate feature'])
})

test('missing target, equal versions and reversed ranges do not create a combined entry', () => {
    for (const [from, to] of [['1.2.0', '1.7.0'], ['1.5.0', '1.5.0'], ['1.5.0', '1.2.0'], [null, '1.5.0']]) {
        assert.equal(buildCombinedVersion(versions, from, to), null)
    }
})

test('an installed version missing from the changelog retains the existing oldest-entry fallback', () => {
    const combined = buildCombinedVersion(versions, '1.0.0', '1.5.0')
    assert.deepEqual([...new Set(combined.entries.map(e => e._sourceVersion))], ['1.4.0', '1.5.0', '1.2.0'])
})
