import test from 'node:test'
import assert from 'node:assert/strict'
import { isPcmType, parsePcmType, createPcm16Decoder, pcmToWavBlob } from './pcmAudio.js'

test('tells a pcm type, with or without its parameters', () => {
    assert.equal(isPcmType('audio/pcm;rate=24000;channels=1'), true)
    assert.equal(isPcmType('Audio/PCM ; rate=24000'), true)
    assert.equal(isPcmType('audio/pcm'), true)
    assert.equal(isPcmType('audio/mpeg'), false)
    assert.equal(isPcmType('audio/pcmx'), false)
    assert.equal(isPcmType(''), false)
})

test('parses only explicit pcm types', () => {
    assert.deepEqual(parsePcmType('audio/pcm;rate=24000;channels=1'), { rate: 24000, channels: 1 })
    assert.deepEqual(parsePcmType('audio/pcm; rate=44100; channels=2'), { rate: 44100, channels: 2 })
    assert.equal(parsePcmType('audio/pcm;rate=24000'), null)
    assert.equal(parsePcmType('audio/pcm;channels=1'), null)
    assert.equal(parsePcmType('audio/pcm'), null)
    assert.equal(parsePcmType('audio/pcm;rate=abc;channels=1'), null)
    assert.equal(parsePcmType('audio/pcm;rate=0;channels=1'), null)
    assert.equal(parsePcmType('audio/pcm;rate=24000;channels=0'), null)
    assert.equal(parsePcmType('audio/mpeg'), null)
    assert.equal(parsePcmType('audio/mpeg;rate=24000;channels=1'), null)
    assert.equal(parsePcmType(''), null)
})

test('decodes int16 little-endian to floats', () => {
    const decoder = createPcm16Decoder(1)
    const [samples] = decoder.decode(new Uint8Array([0x00, 0x80, 0xff, 0x7f, 0x00, 0x00, 0x00, 0x40]))
    assert.ok(samples instanceof Float32Array)
    assert.deepEqual([...samples], [-1, Math.fround(32767 / 32768), 0, 0.5])
})

test('keeps an odd byte for the next chunk', () => {
    const decoder = createPcm16Decoder(1)
    const [first] = decoder.decode(new Uint8Array([0x00, 0x40, 0x00]))
    assert.deepEqual([...first], [0.5])
    const [second] = decoder.decode(new Uint8Array([0x80]))
    assert.deepEqual([...second], [-1])
    // A lone byte makes no sample yet.
    const [third] = decoder.decode(new Uint8Array([0x00]))
    assert.equal(third.length, 0)
    const [fourth] = decoder.decode(new Uint8Array([0x40]))
    assert.deepEqual([...fourth], [0.5])
})

test('reads a chunk that starts inside its buffer', () => {
    // A stream chunk can be a view at a non-zero byteOffset of a larger buffer.
    const buffer = new Uint8Array([0xaa, 0x00, 0x40, 0x00, 0x80, 0xbb]).buffer
    const decoder = createPcm16Decoder(1)
    const [samples] = decoder.decode(new Uint8Array(buffer, 1, 4))
    assert.deepEqual([...samples], [0.5, -1])
    // The same with an odd length: the last byte waits, the next chunk completes it.
    const odd = createPcm16Decoder(1)
    assert.deepEqual([...odd.decode(new Uint8Array(buffer, 1, 3))[0]], [0.5])
    assert.deepEqual([...odd.decode(new Uint8Array([0x80]))[0]], [-1])
})

test('splits interleaved stereo frames', () => {
    const decoder = createPcm16Decoder(2)
    // Frame 1: left -1, right 0.5. Frame 2: left 0, then the first byte of its right sample.
    const [left, right] = decoder.decode(new Uint8Array([0x00, 0x80, 0x00, 0x40, 0x00, 0x00, 0x00]))
    assert.deepEqual([...left], [-1])
    assert.deepEqual([...right], [0.5])
    const [left2, right2] = decoder.decode(new Uint8Array([0x80]))
    assert.deepEqual([...left2], [0])
    assert.deepEqual([...right2], [-1])
})

test('wraps chunks in a wav header', async () => {
    const blob = pcmToWavBlob([new Uint8Array([1, 2, 3, 4]), new Uint8Array([5, 6])], 24000, 1)
    assert.equal(blob.type, 'audio/wav')
    assert.equal(blob.size, 44 + 6)
    const bytes = new Uint8Array(await blob.arrayBuffer())
    const view = new DataView(bytes.buffer)
    const tag = offset => String.fromCharCode(...bytes.slice(offset, offset + 4))
    assert.equal(tag(0), 'RIFF')
    assert.equal(view.getUint32(4, true), 36 + 6)
    assert.equal(tag(8), 'WAVE')
    assert.equal(tag(12), 'fmt ')
    assert.equal(view.getUint32(16, true), 16)
    assert.equal(view.getUint16(20, true), 1)
    assert.equal(view.getUint16(22, true), 1)
    assert.equal(view.getUint32(24, true), 24000)
    assert.equal(view.getUint32(28, true), 48000)
    assert.equal(view.getUint16(32, true), 2)
    assert.equal(view.getUint16(34, true), 16)
    assert.equal(tag(36), 'data')
    assert.equal(view.getUint32(40, true), 6)
    assert.deepEqual([...bytes.slice(44)], [1, 2, 3, 4, 5, 6])

    const stereo = new DataView(await pcmToWavBlob([new Uint8Array(8)], 44100, 2).arrayBuffer())
    assert.equal(stereo.getUint16(22, true), 2)
    assert.equal(stereo.getUint32(24, true), 44100)
    assert.equal(stereo.getUint32(28, true), 44100 * 4)
    assert.equal(stereo.getUint16(32, true), 4)
})

test('declares and keeps whole frames only', async () => {
    // Mono: 2 bytes a frame. 5 bytes received: the last byte is dropped.
    const mono = pcmToWavBlob([new Uint8Array([1, 2, 3]), new Uint8Array([4, 5])], 24000, 1)
    assert.equal(mono.size, 44 + 4)
    const monoBytes = new Uint8Array(await mono.arrayBuffer())
    const monoView = new DataView(monoBytes.buffer)
    assert.equal(monoView.getUint32(4, true), 36 + 4)
    assert.equal(monoView.getUint32(40, true), 4)
    assert.deepEqual([...monoBytes.slice(44)], [1, 2, 3, 4])

    // Stereo: 4 bytes a frame. 7 bytes received: one whole frame.
    const stereo = pcmToWavBlob([new Uint8Array([1, 2, 3, 4, 5, 6, 7])], 24000, 2)
    const stereoBytes = new Uint8Array(await stereo.arrayBuffer())
    assert.equal(new DataView(stereoBytes.buffer).getUint32(40, true), 4)
    assert.deepEqual([...stereoBytes.slice(44)], [1, 2, 3, 4])
})
