import test from 'node:test'
import assert from 'node:assert/strict'
import { browserDescription, profileId, configureTelemetry, captureTelemetry, reportActivity, reportClient, setAndroidTelemetry, setTelemetryEpoch } from './telemetryClient.js'

test('browser reduction preserves distinguishing families and mobile form factor', () => {
    for (const [ua, expected] of [
        ['Mozilla Chrome/130 Safari/537 Edg/130', 'edge'],
        ['Mozilla Chrome/130 Safari/537 OPR/100', 'opera'],
        ['Mozilla Android Chrome/130 SamsungBrowser/24', 'samsung_internet'],
        ['Mozilla Firefox/130', 'firefox'], ['Mozilla Version/18 Safari/605', 'safari'],
        ['Mozilla CriOS/130 Mobile Safari/605', 'chrome'],
        ['Mozilla FxiOS/130 Mobile Safari/605', 'firefox'], ['unknown', 'other'],
    ]) assert.equal(browserDescription({ userAgent: ua }).browser, expected)
    assert.equal(browserDescription({ userAgent: 'Mozilla Android Mobile Chrome/130' }).device_type, 'mobile')
    assert.equal(browserDescription({ userAgent: 'Mozilla Macintosh Safari/605', maxTouchPoints: 5 }).device_type, 'mobile')
    assert.equal(browserDescription({ userAgent: 'Windows Chrome/130' }).device_type, 'desktop')
})

test('tabs and reloads share one profile key without a hardware identifier', () => {
    const values = new Map(), storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) }
    assert.equal(profileId(storage), profileId(storage))
    assert.match(profileId(storage), /^[0-9a-f-]{36}$/)
})

test('no buffering, no disabled or previous-epoch activity; only safe client metadata', async () => {
    const sent = []
    let enabled = true
    configureTelemetry({ send: value => sent.push(value), enabled: () => enabled })
    setTelemetryEpoch('first')
    const token = captureTelemetry()
    reportActivity('manual_tts_messages', token)
    enabled = false
    reportActivity('manual_tts_messages', token)
    enabled = true
    setTelemetryEpoch('second')
    reportActivity('manual_tts_messages', token)
    assert.equal(sent.length, 1)
    assert.deepEqual(sent[0], { type: 'telemetry_activity', epoch: 'first', metric: 'manual_tts_messages' })
    setAndroidTelemetry({ version: '0.118.0', engine: 'GeckoView 159', instances: [{ address: 'private' }] })
    await reportClient({ locale: 'fr', navigator: { userAgent: 'private UA' }, storage: null })
    assert.equal(sent[1].client.browser, 'android_app')
    assert.equal(sent[1].client.engine, 'gecko')
    assert.equal(sent[1].client.app_version, '0.118.0')
    assert.ok(!JSON.stringify(sent).includes('private'))
    setAndroidTelemetry(null)
    configureTelemetry({})
})

test('first-use tabs serialize profile creation with an origin-wide Web Lock', async () => {
    const first = await import('./telemetryClient.js?first-tab')
    const second = await import('./telemetryClient.js?second-tab')
    const values = new Map(), sent = [], locks = []
    const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) }
    let queue = Promise.resolve()
    const navigator = { userAgent: 'Chrome/130', locks: { request(name, callback) {
        locks.push(name)
        const pending = queue.then(callback)
        queue = pending.catch(() => {})
        return pending
    } } }
    for (const tab of [first, second]) {
        tab.configureTelemetry({ send: value => sent.push(value), enabled: () => true })
        tab.setTelemetryEpoch('epoch')
    }
    await Promise.all([first.reportClient({ locale: 'en', storage, navigator }), second.reportClient({ locale: 'fr', storage, navigator })])
    assert.deepEqual(locks, ['twicc-telemetry-profile', 'twicc-telemetry-profile'])
    assert.equal(new Set(sent.map(report => report.client_id)).size, 1)
})

test('queued client metadata cannot survive a collection epoch change', async () => {
    const sent = []
    let release
    configureTelemetry({ send: value => sent.push(value), enabled: () => true })
    setTelemetryEpoch('before')
    const pending = reportClient({ locale: 'en', storage: null,
        navigator: { locks: { request: (_name, action) => new Promise(resolve => { release = () => resolve(action()) }) } } })
    setTelemetryEpoch('after')
    release()
    await pending
    assert.equal(sent.length, 0)
    configureTelemetry({})
})
