import test from 'node:test'
import assert from 'node:assert/strict'

import { silentWavDataUri } from './speechPlayer.js'

test('the priming sound is a valid WAV with samples', () => {
    const uri = silentWavDataUri()
    const prefix = 'data:audio/wav;base64,'
    assert.ok(uri.startsWith(prefix))
    const bytes = Buffer.from(uri.slice(prefix.length), 'base64')
    const tag = (offset) => bytes.toString('ascii', offset, offset + 4)

    assert.equal(tag(0), 'RIFF')
    assert.equal(bytes.readUInt32LE(4), bytes.length - 8)
    assert.equal(tag(8), 'WAVE')
    assert.equal(tag(12), 'fmt ')
    assert.equal(bytes.readUInt16LE(20), 1) // PCM
    assert.equal(bytes.readUInt16LE(22), 1) // mono
    assert.equal(bytes.readUInt32LE(24), 8000)
    assert.equal(bytes.readUInt16LE(34), 16)
    assert.equal(tag(36), 'data')

    const dataBytes = bytes.readUInt32LE(40)
    assert.ok(dataBytes > 0)
    assert.equal(bytes.length, 44 + dataBytes)
    assert.ok(bytes.subarray(44).every(byte => byte === 0))
})
