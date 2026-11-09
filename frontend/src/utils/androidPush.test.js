import test from 'node:test'
import assert from 'node:assert/strict'
import { createAndroidPush } from './androidPush.js'

const preferences = { user_turn: true, pending_request: false, extra_usage: false, peer: false }
function fixture(permission = 'granted', fetchImpl) {
    const calls = [], uploads = []
    const client = { async request(command, args) {
        calls.push([command, args])
        return { device_id: 'phone', name: 'Phone', permission, status: command === 'push.configure' ? 'registering' : 'idle' }
    } }
    return { calls, uploads, controller: createAndroidPush(client, { fetchImpl: fetchImpl || (async (_, options) => {
        uploads.push(JSON.parse(options.body))
        return Response.json({ protocol: 1, public_key: 'key', token: 'grant' })
    }) }) }
}
test('automatically enrolls and keeps choices native without a browser permission request', async () => {
    const f = fixture()
    await f.controller.synchronize(preferences)
    assert.deepEqual(f.uploads, [{ device_id: 'phone', name: 'Phone', enabled: true, preferences }])
    assert.equal(f.calls.at(-1)[0], 'push.configure')
    await f.controller.synchronize({ ...preferences, peer: true })
    assert.equal(f.uploads.length, 1)
    assert.equal(f.calls.at(-2)[0], 'push.preferences')
})
test('denied OS permission still permits individual notification choices', async () => {
    const f = fixture('denied')
    await f.controller.synchronize(preferences)
    assert.equal(f.uploads[0].enabled, false)
    assert.equal(f.calls.some(([c]) => c === 'push.permission'), false)
})
test('requests the initial Android permission only when a selected event needs it', async () => {
    const f = fixture('default')
    await f.controller.synchronize({ user_turn: false, pending_request: false, extra_usage: false, peer: false })
    assert.equal(f.uploads.length, 0)
    await f.controller.synchronize(preferences)
    assert.equal(f.calls.filter(([c]) => c === 'push.permission').length, 1)
})
test('logout waits for enrollment then removes the current native grant', async () => {
    let finish
    const f = fixture('granted', () => new Promise(resolve => { finish = resolve }))
    const sync = f.controller.synchronize(preferences)
    while (!finish) await new Promise(resolve => setImmediate(resolve))
    const stopped = f.controller.stop(true)
    finish(Response.json({ protocol: 1, public_key: 'key', token: 'grant' }))
    await Promise.all([sync, stopped])
    assert.deepEqual(f.calls.slice(-2).map(([c]) => c), ['push.configure', 'push.remove'])
    await f.controller.synchronize(preferences)
    assert.equal(f.calls.at(-1)[0], 'push.remove')
})

test('a ready old endpoint cannot hide failed enrollment or prevent its retry', async () => {
    const { reactive } = await import('vue')
    const { startAndroidPush, observeAndroidPush, disposeAndroidPush } = await import('./androidPush.js')
    const { androidPushState } = await import('./androidEnvironment.js')
    const originalFetch = globalThis.fetch, originalNow = Date.now
    let uploads = 0, now = originalNow()
    const native = { device_id:'phone', name:'Phone', permission:'granted', status:'ready' }
    const client = { request: async command => ({ ...native, status:command === 'push.configure' ? 'registering' : 'ready' }) }
    try {
        Date.now = () => now
        globalThis.fetch = async () => ++uploads === 1 ? Response.json({}, { status:503 }) : Response.json({ protocol:1, public_key:'key', token:'grant' })
        startAndroidPush(client, reactive({ isNotifUserTurnBrowser:true, isNotifPendingRequestBrowser:false,
            isNotifExtraUsageStartBrowser:false, isNotifPeerBrowser:false }), reactive({ authenticated:true }))
        await new Promise(resolve => setImmediate(resolve))
        assert.equal(androidPushState.value.status, 'server')
        observeAndroidPush(native)
        assert.equal(androidPushState.value.status, 'server')
        now += 31000
        observeAndroidPush(native)
        await new Promise(resolve => setImmediate(resolve))
        assert.equal(uploads, 2)
        assert.equal(androidPushState.value.status, 'registering')
        observeAndroidPush(native)
        assert.equal(androidPushState.value.status, 'ready')
    } finally {
        disposeAndroidPush()
        androidPushState.value = null
        globalThis.fetch = originalFetch
        Date.now = originalNow
    }
})


async function compatibilityFixture(payloadVersion, serverVersion) {
    const states = [], uploads = [], calls = []
    const controller = createAndroidPush({ request: async (command, args) => {
        calls.push([command, args])
        return { device_id: 'phone', name: 'Phone', permission: 'granted', status: command === 'push.configure' ? 'ready' : 'idle',
            ...(payloadVersion === undefined ? {} : { payloadVersion }) }
    } }, { onState: state => states.push(state), fetchImpl: async (_, options) => {
        uploads.push(JSON.parse(options.body))
        return Response.json({ protocol: 1, public_key: 'key', token: 'grant',
            ...(serverVersion === undefined ? {} : { notification_payload_version: serverVersion }) })
    } })
    await controller.synchronize(preferences)
    return { states, uploads, calls, controller }
}

test('newApkOldServerShowsGenericCompatibility', async () => {
    const f = await compatibilityFixture(2, undefined)
    assert.equal(f.states.at(-1).status, 'update_server')
    assert.deepEqual(f.uploads, [{ device_id: 'phone', name: 'Phone', enabled: true, preferences, notification_payload_version: 2 }])
    await f.controller.synchronize(preferences)
    assert.equal(f.uploads.length, 1)
    assert.equal(f.states.at(-1).status, 'update_server')
})

test('oldApkNewServerShowsUpdateRequired', async () => {
    const f = await compatibilityFixture(undefined, 2)
    assert.equal(f.states.at(-1).status, 'update_app')
    assert.deepEqual(f.uploads, [{ device_id: 'phone', name: 'Phone', enabled: true, preferences }])
    assert.equal(f.calls.at(-1)[0], 'push.configure')
})

test('compatible APK and server keep enrollment protocol separate from payload capability', async () => {
    const f = await compatibilityFixture(2, 2)
    assert.equal(f.states.at(-1).status, 'ready')
    assert.deepEqual(f.calls.at(-1), ['push.configure', { vapid: 'key', token: 'grant', preferences, payloadVersion: 2 }])
    assert.equal(f.uploads[0].notification_payload_version, 2)
})

test('old APK and old server retain the legacy enrollment flow', async () => {
    const f = await compatibilityFixture(undefined, undefined)
    assert.equal(f.states.at(-1).status, 'ready')
    assert.deepEqual(f.calls.at(-1), ['push.configure', { vapid: 'key', token: 'grant', preferences }])
})


test('native confirmation after a server upgrade clears the earlier compatibility state', async () => {
    let status = 'idle'
    const states = []
    const controller = createAndroidPush({ request: async command => ({ device_id: 'phone', name: 'Phone',
        permission: 'granted', payloadVersion: 2, status: command === 'push.configure' ? 'update_server' : status }) },
    { onState: value => states.push(value), fetchImpl: async () => Response.json({ protocol: 1, public_key: 'key', token: 'grant' }) })
    await controller.synchronize(preferences)
    assert.equal(states.at(-1).status, 'update_server')
    status = 'ready' // The finite native renewal confirms payload 2 on the upgraded server.
    await controller.synchronize(preferences)
    assert.equal(states.at(-1).status, 'ready')
})


test('old APK heartbeats preserve the app update instruction without scheduling enrollment', async () => {
    const { observeAndroidPush } = await import('./androidPush.js')
    const { androidPushState } = await import('./androidEnvironment.js')
    androidPushState.value = { status: 'update_app', compatibility: 'update_app', permission: 'granted' }
    try {
        observeAndroidPush({ status: 'ready', permission: 'granted' })
        assert.equal(androidPushState.value.status, 'update_app')
        observeAndroidPush({ status: 'ready', payloadVersion: 2, permission: 'granted' })
        assert.equal(androidPushState.value.status, 'ready')
    } finally { androidPushState.value = null }
})

test('category settings and sound preparation use the existing native client and merge publication state', async () => {
    const push = await import('./androidPush.js')
    const { androidPushState } = await import('./androidEnvironment.js')
    const { isAndroidApp } = await import('./androidEnvironment.js')
    const originalWindow = globalThis.window
    const calls = []
    const native = { status: 'ready', permission: 'denied', payloadVersion: 2, soundLibrary: 'preparing' }
    const target = { addEventListener() {}, removeEventListener() {}, dispatchEvent() {}, location: { origin: 'https://twicc.example' },
        TwiccAndroid: { postMessage(encoded) {
            const request = JSON.parse(encoded)
            calls.push(request)
            const rejected = request.kind === 'invalid'
            this.onmessage({ data: JSON.stringify({ protocol: 1, id: request.id, ok: !rejected,
                ...(rejected ? { ui_message: { key: 'common.android.errors.actionFailed', params: {} } } : { state: native }) }) })
        } } }
    globalThis.window = target
    androidPushState.value = { ...native, status: 'server' }
    try {
        assert.equal(typeof push.openAndroidNotificationSoundSettings, 'function')
        assert.equal(typeof push.prepareAndroidNotificationSounds, 'function')
        for (const kind of ['user_turn', 'pending_request', 'extra_usage', 'peer']) await push.openAndroidNotificationSoundSettings(kind)
        assert.deepEqual(calls.map(({ command, kind }) => [command, kind]), ['user_turn', 'pending_request', 'extra_usage', 'peer'].map(kind => ['push.soundSettings', kind]))
        push.observeAndroidPush({ ...native, status: 'error' })
        await push.prepareAndroidNotificationSounds()
        assert.equal(calls.at(-1).command, 'push.prepareSounds')
        assert.equal(androidPushState.value.soundLibrary, 'preparing')
        assert.equal(androidPushState.value.status, 'error')
        assert.equal(androidPushState.value.permission, 'denied')
        assert.equal(androidPushState.value.payloadVersion, 2)
        push.observeAndroidPush({ ...native, status: 'error', soundLibrary: 'ready' })
        assert.equal(androidPushState.value.soundLibrary, 'ready')
        await assert.rejects(push.openAndroidNotificationSoundSettings('invalid'), error => error.uiMessage.key === 'common.android.errors.actionFailed')
    } finally { globalThis.window = originalWindow; androidPushState.value = null; isAndroidApp.value = false }
})
