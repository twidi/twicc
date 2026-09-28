import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hashBytes, hashString } from './hash.js'

test('hashBytes is FNV-1a: same value as hashString for ASCII', () => {
    for (const text of ['', 'a', 'hello world', 'The quick brown fox']) {
        assert.equal(hashBytes(new TextEncoder().encode(text)), hashString(text))
    }
})

test('hashBytes of the empty array is the FNV offset basis', () => {
    assert.equal(hashBytes(new Uint8Array(0)), (0x811c9dc5).toString(36))
})

test('hashBytes differs for different bytes', () => {
    assert.notEqual(hashBytes(Uint8Array.of(1, 2, 3)), hashBytes(Uint8Array.of(3, 2, 1)))
})
