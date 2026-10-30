// Raw 16-bit PCM speech audio (the type the backend sends: `audio/pcm;rate=24000;channels=1`).

/** `{ rate, channels }` of an `audio/pcm` type, or null without an explicit, positive rate and channel count. */
export function parsePcmType(contentType) {
    const [mediaType, ...parts] = contentType.split(';')
    if (mediaType.trim().toLowerCase() !== 'audio/pcm') return null
    const params = {}
    for (const part of parts) {
        const [name, value = ''] = part.split('=')
        params[name.trim().toLowerCase()] = value.trim()
    }
    const positive = value => (/^\d+$/.test(value ?? '') && Number(value) > 0 ? Number(value) : null)
    const rate = positive(params.rate)
    const channels = positive(params.channels)
    return rate && channels ? { rate, channels } : null
}

/**
 * A decoder of little-endian 16-bit interleaved PCM: one Float32Array per channel, in [-1, 1).
 * The network splits the bytes anywhere: the bytes of an incomplete frame wait for the next call.
 */
export function createPcm16Decoder(channels) {
    const frameBytes = 2 * channels
    let rest = new Uint8Array(0)
    return {
        decode(bytes) {
            const all = new Uint8Array(rest.length + bytes.length)
            all.set(rest)
            all.set(bytes, rest.length)
            const frames = Math.floor(all.length / frameBytes)
            rest = all.slice(frames * frameBytes)
            const view = new DataView(all.buffer)
            const output = Array.from({ length: channels }, () => new Float32Array(frames))
            for (let frame = 0; frame < frames; frame++) {
                for (let channel = 0; channel < channels; channel++) {
                    output[channel][frame] = view.getInt16(frame * frameBytes + channel * 2, true) / 32768
                }
            }
            return output
        },
    }
}

/** The PCM chunks as a playable 16-bit WAV file (44-byte header, then the samples as received). */
export function pcmToWavBlob(chunks, rate, channels) {
    const dataBytes = chunks.reduce((total, chunk) => total + chunk.length, 0)
    const view = new DataView(new ArrayBuffer(44))
    const writeTag = (offset, tag) => [...tag].forEach((char, i) => view.setUint8(offset + i, char.charCodeAt(0)))
    writeTag(0, 'RIFF')
    view.setUint32(4, 36 + dataBytes, true)
    writeTag(8, 'WAVE')
    writeTag(12, 'fmt ')
    view.setUint32(16, 16, true) // fmt chunk size
    view.setUint16(20, 1, true) // PCM
    view.setUint16(22, channels, true)
    view.setUint32(24, rate, true)
    view.setUint32(28, rate * channels * 2, true) // byte rate
    view.setUint16(32, channels * 2, true) // block align
    view.setUint16(34, 16, true) // bits per sample
    writeTag(36, 'data')
    view.setUint32(40, dataBytes, true)
    return new Blob([view.buffer, ...chunks], { type: 'audio/wav' })
}
