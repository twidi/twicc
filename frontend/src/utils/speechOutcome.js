// How a speech playback ends, and which player plays it (spec: docs/plans/2026-10-30-tts-streaming-design.md).

import { isPcmType } from './pcmAudio.js'

export const REFUSED = 'The browser refused to play the audio'
export const UNREACHABLE = 'Could not reach TwiCC'
export const CUT = 'The audio stopped before the end'
export const GONE = 'The audio is no longer available'

/**
 * The end of a playback, from the player side and the backend's ending answer (null: no answer).
 * `player` is `{ kind: 'ended' }` or `{ kind: 'error', reason: 'refused' | 'network', started }`.
 * Returns `{ ok: true }` or `{ ok: false, error }`.
 */
export function speechEndResult(player, backend) {
    const state = backend?.state
    if (state === 'failed') return { ok: false, error: backend.error }
    if (player.kind === 'ended') {
        // Unknown or no answer: the backend restarted or the network dropped after the download.
        return state === 'running' ? { ok: false, error: CUT } : { ok: true }
    }
    if (!backend) return { ok: false, error: UNREACHABLE }
    if (state !== 'complete' && state !== 'running') return { ok: false, error: GONE }
    if (player.reason === 'refused') return { ok: false, error: REFUSED }
    return { ok: false, error: player.started ? CUT : UNREACHABLE }
}

/** The formats each platform plays from the whole downloaded file instead of streaming. */
export const BUFFERED_FORMATS = { ios: ['pcm'] }

/** 'buffered', 'pcm-stream' or 'media': the player for this audio type on this platform. */
export function playbackMode(contentType, platform) {
    const format = isPcmType(contentType) ? 'pcm' : 'mp3'
    if (BUFFERED_FORMATS[platform]?.includes(format)) return 'buffered'
    return format === 'pcm' ? 'pcm-stream' : 'media'
}

/** 'ios', 'android' or 'desktop'. iPadOS shows a Mac user agent: its touch points tell it apart. */
export function currentPlatform(nav = globalThis.navigator) {
    const userAgent = nav?.userAgent ?? ''
    if (/iPhone|iPad|iPod/.test(userAgent) || (nav?.platform === 'MacIntel' && nav.maxTouchPoints > 1)) return 'ios'
    if (/Android/.test(userAgent)) return 'android'
    return 'desktop'
}
