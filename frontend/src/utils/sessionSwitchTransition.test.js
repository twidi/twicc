import test from 'node:test'
import assert from 'node:assert/strict'
import { installSessionSwitchTransition, isSessionSwitch } from './sessionSwitchTransition.js'

const route = (sessionId, name = 'session') => ({ name, params: sessionId ? { projectId: 'p', sessionId } : { projectId: 'p' } })

test('1. a switch is a move from one session to another', () => {
    assert.equal(isSessionSwitch(route('b'), route('a')), true)
    assert.equal(isSessionSwitch(route('b', 'projects-session'), route('a', 'projects-session')), true)
})

test('2. not a switch: the same session (a tab change), no session on either side (project, home, first load)', () => {
    assert.equal(isSessionSwitch(route('a', 'session-files'), route('a', 'session')), false)
    assert.equal(isSessionSwitch(route(null, 'project'), route('a')), false)
    assert.equal(isSessionSwitch(route('a'), route(null, 'project')), false)
    assert.equal(isSessionSwitch(route('a'), { name: undefined, params: {} }), false)
})

test('3. not a switch: a draft being swapped for its real session keeps what is on screen', () => {
    const isDraft = (id) => id === 'draft-1'
    assert.equal(isSessionSwitch(route('real'), route('draft-1'), isDraft), false)
    assert.equal(isSessionSwitch(route('draft-1'), route('real'), isDraft), true, 'opening a draft from a session is a switch')
})

function fakeRouter() {
    const guards = { resolve: [], after: [], error: [] }
    return {
        guards,
        beforeResolve: (fn) => { guards.resolve.push(fn); return () => guards.resolve.splice(guards.resolve.indexOf(fn), 1) },
        afterEach: (fn) => { guards.after.push(fn); return () => guards.after.splice(guards.after.indexOf(fn), 1) },
        onError: (fn) => { guards.error.push(fn); return () => guards.error.splice(guards.error.indexOf(fn), 1) },
    }
}

test('4. a switch holds the navigation until the transition runs its update, then the update waits for the navigation to land', async () => {
    const router = fakeRouter()
    const calls = []
    let release = null
    const run = (update, options) => { calls.push(options); release = update }
    installSessionSwitchTransition(router, { run })
    let passed = false
    const guarded = router.guards.resolve[0](route('b'), route('a')).then(() => { passed = true })
    await Promise.resolve()
    assert.equal(passed, false, 'held while the transition has not captured the old state')
    assert.deepEqual(calls, [{ kind: 'session', settle: true, updateTimeoutMs: 800 }])
    let landed = false
    const update = release()
    update.then(() => { landed = true })
    await guarded
    assert.equal(passed, true, 'the update lets the navigation through')
    await Promise.resolve()
    assert.equal(landed, false, 'the update waits for the navigation')
    router.guards.after[0]()
    await update
    assert.equal(landed, true)
})

test('5. a navigation that is not a switch is not held and runs no transition', () => {
    const router = fakeRouter()
    const calls = []
    installSessionSwitchTransition(router, { run: () => calls.push(1) })
    assert.equal(router.guards.resolve[0](route('a', 'session-files'), route('a')), undefined)
    assert.deepEqual(calls, [])
})

test('6. a failed navigation also lets the update finish (no frozen page)', async () => {
    const router = fakeRouter()
    let release = null
    installSessionSwitchTransition(router, { run: (update) => { release = update } })
    router.guards.resolve[0](route('b'), route('a'))
    const update = release()
    router.guards.error[0](new Error('x'))
    await update
})

test('7. without view transition support, the update runs at once and the navigation goes on', async () => {
    const router = fakeRouter()
    installSessionSwitchTransition(router, { run: (update) => { update() } })
    await router.guards.resolve[0](route('b'), route('a'))
})

test('8. uninstall removes the three guards', () => {
    const router = fakeRouter()
    const uninstall = installSessionSwitchTransition(router, { run: () => {} })
    uninstall()
    assert.deepEqual([router.guards.resolve.length, router.guards.after.length, router.guards.error.length], [0, 0, 0])
})
