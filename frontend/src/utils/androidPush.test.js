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
