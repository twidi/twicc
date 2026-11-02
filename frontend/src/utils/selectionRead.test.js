import test from 'node:test'
import assert from 'node:assert/strict'

import { READ_TITLES, canShowRead, readIcon } from './selectionRead.js'

const base = { collapsed: true, persistent: false, readOrigin: 'rendered', available: true, busy: false }

test('canShowRead is true for a collapsed widget with a known origin and an available service', () => {
    assert.equal(canShowRead(base), true)
    assert.equal(canShowRead({ ...base, readOrigin: 'file' }), true)
})

test('canShowRead is false when expanded or persistent', () => {
    assert.equal(canShowRead({ ...base, collapsed: false }), false)
    assert.equal(canShowRead({ ...base, persistent: true }), false)
})

test('canShowRead is false for a null or unknown origin', () => {
    assert.equal(canShowRead({ ...base, readOrigin: null }), false)
    assert.equal(canShowRead({ ...base, readOrigin: 'other' }), false)
})

test('canShowRead needs availability or a busy reading', () => {
    assert.equal(canShowRead({ ...base, available: false }), false)
    assert.equal(canShowRead({ ...base, available: false, busy: true }), true)
})

test('readIcon is Read while idle and Stop otherwise', () => {
    assert.equal(readIcon('idle'), 'volume-up')
    assert.equal(readIcon('loading'), 'stop')
    assert.equal(readIcon('playing'), 'stop')
})

test('READ_TITLES holds the three titles', () => {
    assert.deepEqual(READ_TITLES, { idle: 'Read aloud', loading: 'Cancel the request', playing: 'Stop reading' })
})
