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

/**
 * The formats each platform plays from the whole downloaded file instead of streaming.
 * iOS/iPadOS and Safari are untested, and Safari needs byte ranges for media: they keep the full download.
 */
export const BUFFERED_FORMATS = { ios: ['pcm', 'mp3'], safari: ['mp3'] }

/**
 * The formats each platform asks the backend to send in one go (no pacing), while it keeps its
 * streaming player. Filled only if a platform cuts paced audio (the Android check).
 */
export const BUFFERED_SENDING = {}

/**
 * Whether the speech open step asks for buffered sending: the platform buffers this format or asks for it.
 * It reads the requested format: the open step asks before it knows the audio type.
 */
export function buffersSending(responseFormat, platform) {
    return Boolean(BUFFERED_FORMATS[platform]?.includes(responseFormat) || BUFFERED_SENDING[platform]?.includes(responseFormat))
}

/** 'buffered', 'pcm-stream' or 'media': the player for this audio type on this platform. */
export function playbackMode(contentType, platform) {
    const format = isPcmType(contentType) ? 'pcm' : 'mp3'
    if (BUFFERED_FORMATS[platform]?.includes(format)) return 'buffered'
    return format === 'pcm' ? 'pcm-stream' : 'media'
}

/**
 * 'ios', 'android', 'safari' (desktop WebKit: Safari and other WebKit browsers) or 'desktop'.
 * iPadOS shows a Mac user agent: its touch points tell it apart.
 * The other browsers also put "Safari/" in their user agent: their own token tells them apart.
 */
export function currentPlatform(nav = globalThis.navigator) {
    const userAgent = nav?.userAgent ?? ''
    if (/iPhone|iPad|iPod/.test(userAgent) || (nav?.platform === 'MacIntel' && nav.maxTouchPoints > 1)) return 'ios'
    if (/Android/.test(userAgent)) return 'android'
    if (/Safari\//.test(userAgent) && /Version\//.test(userAgent)
        && !/Chrome|Chromium|CriOS|FxiOS|Edg|OPR|Firefox/.test(userAgent)) return 'safari'
    return 'desktop'
}
