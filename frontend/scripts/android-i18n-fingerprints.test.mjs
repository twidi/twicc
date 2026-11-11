import test from 'node:test'
import assert from 'node:assert/strict'
import { createAndroidFingerprints } from './android-i18n-fingerprints.mjs'
import { verifyFingerprints } from './check-android-i18n-source.mjs'

const path = 'frontend/src/i18n/locales/en.json'
const manifest = { messages: { 'native.label': {} } }
const catalog = { 'native.label': 'Café 😀\n{name}', 'web.label': 'Web only' }
const sources = { [path]: Buffer.from(JSON.stringify(catalog)), 'generator.mjs': Buffer.from('generator') }
const outputs = { 'output.json': 'generated' }
const read = input => Buffer.from(input === path ? JSON.stringify(catalog) : input === 'generator.mjs' ? 'generator' : 'generated')

test('web-only translations, JSON formatting and key order do not invalidate Android', async () => {
    const fingerprint = createAndroidFingerprints(sources, outputs, manifest)
    assert.ok(fingerprint.includes(`${path}#native.label`))
    assert.ok(!fingerprint.includes('#web.label'))
    assert.deepEqual(await verifyFingerprints(fingerprint, input => input === path
        ? Buffer.from(JSON.stringify({ 'web.new': 'Added', 'web.label': 'Changed', 'native.label': catalog['native.label'] }, null, 4))
        : read(input)), [])
})

test('native translation edits, removal and type changes fail verification', async () => {
    const fingerprint = createAndroidFingerprints(sources, outputs, manifest)
    for (const value of ['Changed', undefined, 42]) {
        const failures = await verifyFingerprints(fingerprint, input => input === path
            ? Buffer.from(JSON.stringify({ ...catalog, 'native.label': value })) : read(input))
        assert.equal(failures.length, 1)
        assert.match(failures[0], /en.json#native.label/)
    }
})

test('generator and output changes still invalidate Android', async () => {
    const fingerprint = createAndroidFingerprints(sources, outputs, manifest)
    for (const modified of ['generator.mjs', 'output.json']) {
        assert.deepEqual(await verifyFingerprints(fingerprint, input => input === modified ? Buffer.from('changed') : read(input)),
            [`Stale fingerprint: ${modified}`])
    }
})
