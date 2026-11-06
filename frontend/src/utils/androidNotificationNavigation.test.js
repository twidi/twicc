import test from 'node:test'
import assert from 'node:assert/strict'
import { createRouter, createMemoryHistory } from 'vue-router'
import { navigateAndroidNotification } from './androidNotificationNavigation.js'

async function fixture(path) {
    const component = { render: () => null }
    const router = createRouter({ history: createMemoryHistory(), routes: [
        { path: '/', component },
        { path: '/project/:projectId/session/:sessionId', component },
        { path: '/projects/:projectId/session/:sessionId/files/:file', component },
        { path: '/login', component },
    ] })
    await router.push(path)
    const acknowledged = []
    const client = { request: async (command, args) => { acknowledged.push([command, args]) } }
    return { router, client, acknowledged }
}

test('another session opens through Vue Router and consumes the native target', async () => {
    const f = await fixture('/project/p/session/one')
    await navigateAndroidNotification(f.client, f.router, { id: 'tap', path: '/project/p/session/two' })
    assert.equal(f.router.currentRoute.value.fullPath, '/project/p/session/two')
    assert.deepEqual(f.acknowledged, [['navigation.acknowledge', { navigationId: 'tap' }]])
})

test('an already open session keeps its tab and workspace', async () => {
    const f = await fixture('/projects/p/session/one/files/readme?workspace=w')
    await navigateAndroidNotification(f.client, f.router, { id: 'tap', path: '/project/p/session/one' })
    assert.equal(f.router.currentRoute.value.fullPath, '/projects/p/session/one/files/readme?workspace=w')
    assert.equal(f.acknowledged.length, 1)
})

test('a guard refusal consumes the tap without repeating navigation later', async () => {
    const f = await fixture('/project/p/session/one')
    f.router.beforeEach(() => false)
    await navigateAndroidNotification(f.client, f.router, { id: 'tap', path: '/project/p/session/two' })
    assert.equal(f.router.currentRoute.value.fullPath, '/project/p/session/one')
    assert.equal(f.acknowledged.length, 1)
})

test('foreign URLs and non-session targets never reach the router', async () => {
    const f = await fixture('/')
    for (const path of ['https://other.example/project/p/session/s', '//other.example', '/logout', '/project/p/session/s?x=1', '/project/p/session/s\n']) {
        await assert.rejects(navigateAndroidNotification(f.client, f.router, { id: 'tap', path }), /Invalid notification target/)
    }
    assert.equal(f.router.currentRoute.value.fullPath, '/')
    assert.equal(f.acknowledged.length, 0)
})


test('home and peer destinations work without a session path', async () => {
    const f = await fixture('/project/p/session/one')
    await navigateAndroidNotification(f.client, f.router, { id: 'usage', action: 'home' })
    assert.equal(f.router.currentRoute.value.path, '/')
    const events = []
    const dispatch = (name, detail) => events.push([name, detail])
    await navigateAndroidNotification(f.client, f.router, { id: 'peer', action: 'peerInbox', detail: '42' }, dispatch)
    await navigateAndroidNotification(f.client, f.router, { id: 'request', action: 'peerRequests' }, dispatch)
    assert.deepEqual(events, [['twicc:open-peer-inbox', { messageId: '42' }], ['twicc:open-peers-manager', undefined]])
})

test('a peer tap waits for login instead of consuming an action behind the login page', async () => {
    const f = await fixture('/login')
    const events = []
    await navigateAndroidNotification(f.client, f.router, { id: 'peer', action: 'peerInbox', detail: '42' }, (...args) => events.push(args))
    assert.equal(f.acknowledged.length, 0)
    assert.equal(events.length, 0)
    await f.router.push('/')
    await navigateAndroidNotification(f.client, f.router, { id: 'peer', action: 'peerInbox', detail: '42' }, (...args) => events.push(args))
    assert.equal(f.acknowledged.length, 1)
    assert.equal(events.length, 1)
})
