import test from 'node:test'
import assert from 'node:assert/strict'
import { createRouter, createMemoryHistory } from 'vue-router'
import { navigateAndroidNotification } from './androidNotificationNavigation.js'

async function fixture(path, { initialGuard, waitForInitial = true } = {}) {
    const component = { render: () => null }
    const router = createRouter({ history: createMemoryHistory(), routes: [
        { path: '/', name: 'home', component },
        { path: '/project/:projectId', name: 'project', component },
        { path: '/projects/all', name: 'projects-all', component },
        { path: '/project/:projectId/session/:sessionId', name: 'session', component },
        { path: '/projects/:projectId/session/:sessionId', name: 'projects-session', component },
        { path: '/projects/:projectId/session/:sessionId/files/:file', name: 'projects-session-files', component },
        { path: '/login', component },
    ] })
    if (initialGuard) router.beforeEach(initialGuard)
    const initialNavigation = router.push(path)
    if (waitForInitial) await initialNavigation
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

for (const [current, expected] of [
    ['/project/filter/session/one?workspace=w', '/project/filter/session/two?workspace=w'],
    ['/project/filter?workspace=w', '/project/filter/session/two?workspace=w'],
    ['/projects/filter/session/one?workspace=w', '/projects/actual/session/two?workspace=w'],
    ['/projects/all?workspace=w', '/projects/actual/session/two?workspace=w'],
    ['/', '/project/actual/session/two'],
]) test(`notification uses the shared session navigation context from ${current}`, async () => {
    const f = await fixture(current)
    await navigateAndroidNotification(f.client, f.router, { id: 'tap', path: '/project/actual/session/two' })
    assert.equal(f.router.currentRoute.value.fullPath, expected)
    assert.equal(f.acknowledged.length, 1)
})

test('the same session under another project filter keeps its current tab and workspace', async () => {
    const f = await fixture('/projects/filter/session/one/files/readme?workspace=w')
    await navigateAndroidNotification(f.client, f.router, { id: 'tap', path: '/project/actual/session/one' })
    assert.equal(f.router.currentRoute.value.fullPath, '/projects/filter/session/one/files/readme?workspace=w')
    assert.equal(f.acknowledged.length, 1)
})

test('cold entry waits for the initial route before reading its project and workspace', async () => {
    let ready
    const gate = new Promise(resolve => { ready = resolve })
    const f = await fixture('/project/filter/session/one?workspace=w', {
        initialGuard: () => gate, waitForInitial: false,
    })
    const navigation = navigateAndroidNotification(f.client, f.router, { id: 'tap', path: '/project/actual/session/two' })
    assert.equal(f.acknowledged.length, 0)
    ready()
    await navigation
    assert.equal(f.router.currentRoute.value.fullPath, '/project/filter/session/two?workspace=w')
    assert.equal(f.acknowledged.length, 1)
})

test('a session tap waits for login so the restored context survives authentication', async () => {
    const f = await fixture('/login?redirect=/project/filter/session/one?workspace=w')
    await navigateAndroidNotification(f.client, f.router, { id: 'tap', path: '/project/actual/session/two' })
    assert.equal(f.router.currentRoute.value.path, '/login')
    assert.equal(f.acknowledged.length, 0)
    await f.router.push('/project/filter/session/one?workspace=w')
    await navigateAndroidNotification(f.client, f.router, { id: 'tap', path: '/project/actual/session/two' })
    assert.equal(f.router.currentRoute.value.fullPath, '/project/filter/session/two?workspace=w')
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
