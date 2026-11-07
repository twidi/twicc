import test from 'node:test'
import assert from 'node:assert/strict'
import { i18n } from '../i18n/index.js'
import {
    REFUSED,
    UNREACHABLE,
    CUT,
    GONE,
    BUFFERED_FORMATS,
    BUFFERED_SENDING,
    buffersSending,
    speechEndResult,
    playbackMode,
    currentPlatform,
} from './speechOutcome.js'

const ENDED = { kind: 'ended' }
const refused = (started = true) => ({ kind: 'error', reason: 'refused', started })
const network = started => ({ kind: 'error', reason: 'network', started })
const backend = (state, error = null) => ({ state, error })

test('the messages', () => {
    assert.equal(REFUSED, 'The browser refused to play the audio')
    assert.equal(UNREACHABLE, 'Could not reach TwiCC')
    assert.equal(CUT, 'The audio stopped before the end')
    assert.equal(GONE, 'The audio is no longer available')
})

test('ended + complete is a normal end', () => {
    assert.deepEqual(speechEndResult(ENDED, backend('complete')), { ok: true })
})

test('ended + failed shows the backend message', () => {
    assert.deepEqual(speechEndResult(ENDED, backend('failed', 'Upstream broke')), { ok: false, error: 'Upstream broke' })
})

test('ended + running means the browser side was cut', () => {
    assert.deepEqual(speechEndResult(ENDED, backend('running')), { ok: false, error: CUT })
})

test('ended + unknown or no answer is a normal end', () => {
    assert.deepEqual(speechEndResult(ENDED, backend('unknown')), { ok: true })
    assert.deepEqual(speechEndResult(ENDED, null), { ok: true })
})

test('error + failed shows the backend message', () => {
    for (const player of [refused(), network(true), network(false)]) {
        assert.deepEqual(speechEndResult(player, backend('failed', 'Upstream broke')), { ok: false, error: 'Upstream broke' })
    }
})

test('error refused + complete or running is a refusal', () => {
    for (const state of ['complete', 'running']) {
        assert.deepEqual(speechEndResult(refused(true), backend(state)), { ok: false, reason: 'refused', error: REFUSED }, state)
        assert.deepEqual(speechEndResult(refused(false), backend(state)), { ok: false, reason: 'refused', error: REFUSED }, state)
    }
})

test('a localized refusal retains its stable control reason', context => {
    const previous = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previous })
    i18n.global.locale.value = 'pt-BR'
    const result = speechEndResult(refused(), backend('complete'))
    assert.equal(result.reason, 'refused')
    assert.equal(result.error, i18n.global.t('session.speech.errors.refused'))
    assert.notEqual(result.error, REFUSED)
    assert.deepEqual(speechEndResult(refused(), backend('failed', 'Provider-specific error')), {
        ok: false, error: 'Provider-specific error',
    })
})

test('error network after the start + complete or running is a cut', () => {
    for (const state of ['complete', 'running']) {
        assert.deepEqual(speechEndResult(network(true), backend(state)), { ok: false, error: CUT }, state)
    }
})

test('error network before the start + complete or running is unreachable', () => {
    for (const state of ['complete', 'running']) {
        assert.deepEqual(speechEndResult(network(false), backend(state)), { ok: false, error: UNREACHABLE }, state)
    }
})

test('error + unknown means the audio is gone', () => {
    for (const player of [refused(), network(true), network(false)]) {
        assert.deepEqual(speechEndResult(player, backend('unknown')), { ok: false, error: GONE })
    }
})

test('error + no answer is unreachable', () => {
    for (const player of [refused(), network(true), network(false)]) {
        assert.deepEqual(speechEndResult(player, null), { ok: false, error: UNREACHABLE })
    }
})

test('playbackMode follows the buffered table', () => {
    assert.deepEqual(BUFFERED_FORMATS, { ios: ['pcm', 'mp3'], safari: ['mp3'] })
    const pcm = 'audio/pcm;rate=24000;channels=1'
    assert.equal(playbackMode(pcm, 'ios'), 'buffered')
    assert.equal(playbackMode(pcm, 'safari'), 'pcm-stream')
    assert.equal(playbackMode(pcm, 'android'), 'pcm-stream')
    assert.equal(playbackMode(pcm, 'desktop'), 'pcm-stream')
    // iOS/iPadOS and Safari are untested, and Safari needs byte ranges for media.
    assert.equal(playbackMode('audio/mpeg', 'ios'), 'buffered')
    assert.equal(playbackMode('audio/mpeg', 'safari'), 'buffered')
    assert.equal(playbackMode('audio/mpeg', 'android'), 'media')
    assert.equal(playbackMode('audio/mpeg', 'desktop'), 'media')
    assert.equal(playbackMode('audio/wav', 'android'), 'media')
})

test('buffersSending follows both tables', () => {
    assert.deepEqual(BUFFERED_SENDING, {})
    assert.equal(buffersSending('pcm', 'ios'), true)
    assert.equal(buffersSending('mp3', 'safari'), true)
    assert.equal(buffersSending('pcm', 'safari'), false)
    assert.equal(buffersSending('mp3', 'android'), false)
    assert.equal(buffersSending('pcm', 'desktop'), false)
    BUFFERED_SENDING.android = ['mp3']
    try {
        assert.equal(buffersSending('mp3', 'android'), true)
        // Buffered sending only: the platform keeps its streaming player.
        assert.equal(playbackMode('audio/mpeg', 'android'), 'media')
    } finally {
        delete BUFFERED_SENDING.android
    }
})

test('currentPlatform detects iOS, iPadOS and Android', () => {
    const nav = (userAgent, platform = '', maxTouchPoints = 0) => ({ userAgent, platform, maxTouchPoints })
    assert.equal(currentPlatform(nav('Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)', 'iPhone', 5)), 'ios')
    assert.equal(currentPlatform(nav('Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)', 'iPad', 5)), 'ios')
    assert.equal(currentPlatform(nav('Mozilla/5.0 (iPod touch; CPU iPhone OS 15_0 like Mac OS X)', 'iPod', 5)), 'ios')
    // iPadOS shows a Mac user agent; only the touch points tell it apart.
    const macUserAgent = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
    assert.equal(currentPlatform(nav(macUserAgent, 'MacIntel', 5)), 'ios')
    assert.equal(currentPlatform(nav(macUserAgent, 'MacIntel', 0)), 'desktop')
    // The boundary: one touch point is a Mac with a touch device, not an iPad.
    assert.equal(currentPlatform(nav(macUserAgent, 'MacIntel', 1)), 'desktop')
    assert.equal(currentPlatform(nav('Mozilla/5.0 (Linux; Android 15; Pixel 9)', 'Linux armv8l', 5)), 'android')
    assert.equal(currentPlatform(nav('Mozilla/5.0 (X11; Linux x86_64; rv:158.0) Gecko/20100101 Firefox/158.0', 'Linux x86_64')), 'desktop')
    assert.equal(currentPlatform(undefined), 'desktop')
})

test('currentPlatform detects desktop Safari, not the other macOS browsers', () => {
    const nav = (userAgent, maxTouchPoints = 0) => ({ userAgent, platform: 'MacIntel', maxTouchPoints })
    const mac = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)'
    assert.equal(currentPlatform(nav(`${mac} Version/19.0 Safari/605.1.15`)), 'safari')
    // iPadOS shows the same user agent: the ios rule wins first.
    assert.equal(currentPlatform(nav(`${mac} Version/19.0 Safari/605.1.15`, 5)), 'ios')
    // Their user agents also contain "Safari/".
    const blink = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)'
    assert.equal(currentPlatform(nav(`${blink} Chrome/156.0.0.0 Safari/537.36`)), 'desktop')
    assert.equal(currentPlatform(nav(`${blink} Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0`)), 'desktop')
    assert.equal(currentPlatform(nav(`${blink} Chrome/156.0.0.0 Safari/537.36 OPR/120.0.0.0`)), 'desktop')
    assert.equal(currentPlatform(nav(`${blink} Chromium/156.0.0.0 Safari/537.36`)), 'desktop')
    assert.equal(currentPlatform(nav('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:160.0) Gecko/20100101 Firefox/160.0')), 'desktop')
    // A user agent with "Version/" and "Safari/" plus another browser token is that browser.
    for (const token of ['Chrome', 'Chromium', 'CriOS', 'FxiOS', 'Edg', 'OPR', 'Firefox']) {
        assert.equal(currentPlatform(nav(`${mac} Version/19.0 ${token}/1.0 Safari/605.1.15`)), 'desktop', token)
    }
    // Without "Version/": not Safari.
    assert.equal(currentPlatform(nav(`${mac} Safari/605.1.15`)), 'desktop')
})
