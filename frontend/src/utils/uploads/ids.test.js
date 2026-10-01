import { test } from 'node:test'
import assert from 'node:assert/strict'
import { hashBytes } from '../hash.js'
import { fingerprintFile, isClientIdOfTab, makeClientId, randomHexFromUUID, FINGERPRINT_BYTES } from './ids.js'
import { generateUUID } from '../crypto.js'

test('clientId: tab id prefix, unique per upload, at most 64 characters', () => {
    const tabId = generateUUID()
    const a = makeClientId(tabId, randomHexFromUUID)
    const b = makeClientId(tabId, randomHexFromUUID)
    assert.notEqual(a, b)
    assert.ok(a.startsWith(tabId + ':'))
    assert.equal(a.length, 53)
    assert.ok(a.length <= 64)
    assert.match(a.slice(tabId.length + 1), /^[0-9a-f]{16}$/)
    assert.equal(isClientIdOfTab(a, tabId), true)
    assert.equal(isClientIdOfTab(a, 'other-tab'), false)
    assert.equal(isClientIdOfTab(null, tabId), false)
})

test('fingerprint = hashBytes of the first bytes, then the size', async () => {
    const bytes = new TextEncoder().encode('hello')
    const file = new File([bytes], 'x.txt')
    assert.equal(await fingerprintFile(file), `${hashBytes(bytes)}:5`)
})

test('fingerprint reads at most 1 MiB', async () => {
    const big = new Uint8Array(FINGERPRINT_BYTES + 10).fill(7)
    const other = big.slice()
    other[FINGERPRINT_BYTES + 5] = 9 // differs only after the first MiB
    const fa = await fingerprintFile(new Blob([big]))
    const fb = await fingerprintFile(new Blob([other]))
    assert.equal(fa, fb)
    assert.equal(fa, `${hashBytes(big.subarray(0, FINGERPRINT_BYTES))}:${big.length}`)
})

test('fingerprint of an empty file', async () => {
    assert.equal(await fingerprintFile(new Blob([])), `${hashBytes(new Uint8Array(0))}:0`)
})

test('fingerprint rejects when the file cannot be read', async () => {
    const broken = { size: 3, slice: () => ({ arrayBuffer: async () => { throw new Error('gone') } }) }
    await assert.rejects(fingerprintFile(broken))
})
