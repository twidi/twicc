import test from 'node:test'
import assert from 'node:assert/strict'
import { createMemoryHistory, createRouter, isNavigationFailure } from 'vue-router'
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

// These cases catch missing mode transitions and accidental animation of pane changes.
for (const [fromName, toName] of [
    ['home', 'project'],
    ['project', 'home'],
    ['home', 'projects-artifacts'],
    ['projects-artifacts', 'home'],
    ['session-artifacts', 'project-artifacts'],
    ['project-artifacts', 'session-artifacts'],
]) {
    test(`mode change ${fromName} → ${toName} holds navigation for one root fade`, async () => {
        const router = fakeRouter()
        const calls = []
        let release
        installSessionSwitchTransition(router, { run: (update, options) => { calls.push(options); release = update } })
        let passed = false
        const guarded = router.guards.resolve[0](route(null, toName), route(null, fromName))
        assert.ok(guarded instanceof Promise)
        guarded.then(() => { passed = true })
        await Promise.resolve()
        assert.equal(passed, false)
        assert.deepEqual(calls, [{ kind: 'session', settle: true, updateTimeoutMs: 800 }])
        const update = release()
        await guarded
        router.guards.after[0]()
        await update
    })
}

for (const name of [
    'project', 'project-files', 'project-git', 'project-terminal',
    'projects-all', 'projects-files', 'projects-git', 'projects-terminal',
    'session', 'session-subagent', 'session-files', 'session-artifacts', 'session-git',
    'session-terminal', 'session-orchestration', 'session-plan', 'session-tasks',
    'session-workflows', 'session-browser',
    'projects-session', 'projects-session-subagent', 'projects-session-files', 'projects-session-artifacts',
    'projects-session-git', 'projects-session-terminal', 'projects-session-orchestration',
    'projects-session-plan', 'projects-session-tasks', 'projects-session-workflows', 'projects-session-browser',
]) {
    test(`${name} belongs to Sessions mode, including in-session artifact panes`, async () => {
        const router = fakeRouter()
        let starts = 0
        installSessionSwitchTransition(router, { run: (update) => { starts++; update() } })
        await router.guards.resolve[0](route(null, name), route(null, 'home'))
        assert.equal(starts, 1)
        router.guards.after[0]()
        assert.equal(router.guards.resolve[0](route(null, name), route(null, 'projects-all')), undefined)
        assert.equal(starts, 1)
    })
}

test('empty artifact selections, login, first load, and no-op navigation run no transition', () => {
    const router = fakeRouter()
    let starts = 0
    installSessionSwitchTransition(router, { run: () => { starts++ } })
    const cases = [
        [route(null, 'project-artifacts'), route(null, 'projects-artifacts')],
        [route(null, 'home'), route(null, 'home')],
        [route(null, 'home'), route(null, 'login')],
        [route(null, 'login'), route(null, 'session')],
        [route(null, 'project-artifacts'), { name: undefined, params: {} }],
    ]
    for (const [to, from] of cases) assert.equal(router.guards.resolve[0](to, from), undefined)
    assert.equal(starts, 0)
})

for (const event of ['after', 'error']) {
    test(`mode navigation ${event} releases its pending update`, async () => {
        const router = fakeRouter()
        let release
        installSessionSwitchTransition(router, { run: (update) => { release = update } })
        const guarded = router.guards.resolve[0](route(null, 'home'), route(null, 'project-artifacts'))
        assert.ok(guarded instanceof Promise)
        const update = release()
        await guarded
        router.guards[event][0](new Error('navigation failed'))
        await update
    })
}

function memoryRouter() {
    const component = { render: () => null }
    return createRouter({
        history: createMemoryHistory(),
        routes: [
            { path: '/', name: 'home', component },
            { path: '/projects', name: 'projects-all', component },
            { path: '/projects/artifacts/:bookmarkId?', name: 'projects-artifacts', component },
            { path: '/projects/session/:sessionId', name: 'projects-session', component },
            { path: '/project/:projectId/artifacts/:bookmarkId?', name: 'project-artifacts', component },
        ],
    })
}

test('redirected entries use the final mode; push, replace, and history use one transition owner', async () => {
    const router = memoryRouter()
    let destination = 'projects-artifacts'
    router.beforeEach((to) => {
        if (to.name === 'projects-all') return { name: destination, params: destination === 'projects-session' ? { sessionId: 'a' } : {} }
    })
    const updates = []
    installSessionSwitchTransition(router, { run: (update) => { updates.push(update()) } })
    await router.push('/')
    assert.equal(updates.length, 0, 'first load has no transition')
    await router.push('/projects')
    await updates[0]
    assert.equal(router.currentRoute.value.name, 'projects-artifacts')
    assert.equal(updates.length, 1, 'redirect to Artifacts starts once')
    await router.push('/projects/artifacts/bookmark')
    assert.equal(updates.length, 1, 'selecting an artifact stays in Artifacts mode')
    destination = 'projects-session'
    await router.replace('/projects')
    await updates[1]
    assert.equal(router.currentRoute.value.name, 'projects-session')
    assert.equal(updates.length, 2, 'redirect from Artifacts to Sessions starts once')
    const landed = new Promise((resolve) => {
        const remove = router.afterEach(() => { remove(); resolve() })
    })
    router.back()
    await landed
    await updates[2]
    assert.equal(router.currentRoute.value.name, 'projects-artifacts')
    assert.equal(updates.length, 3, 'history starts the same transition')
    await router.push('/projects/artifacts')
    assert.equal(updates.length, 3, 'a no-op route starts no transition')
})

test('aborted and throwing mode navigation release updates and permit later navigation', async () => {
    const router = memoryRouter()
    const updates = []
    installSessionSwitchTransition(router, { run: (update) => { updates.push(update()) } })
    let failure = null
    router.beforeResolve(() => {
        if (failure === 'abort') return false
        if (failure === 'error') throw new Error('mode navigation failed')
    })
    router.onError(() => {})
    await router.push('/')
    failure = 'abort'
    assert.equal(isNavigationFailure(await router.push('/projects')), true)
    await updates[0]
    assert.equal(router.currentRoute.value.name, 'home')
    failure = 'error'
    await assert.rejects(router.push('/projects'), /mode navigation failed/)
    await updates[1]
    assert.equal(router.currentRoute.value.name, 'home')
    failure = null
    await router.push('/projects')
    await updates[2]
    assert.equal(router.currentRoute.value.name, 'projects-all')
    assert.equal(updates.length, 3)
})

test('mode navigation without the browser API uses the real immediate fallback', async () => {
    const router = fakeRouter()
    installSessionSwitchTransition(router)
    const guarded = router.guards.resolve[0](route(null, 'home'), route(null, 'project-artifacts'))
    assert.ok(guarded instanceof Promise)
    await guarded
    router.guards.after[0]()
})

const artifactRoute = (bookmarkId, name = 'project-artifacts', projectId = 'p') => ({
    name, params: { projectId, bookmarkId },
})

// Missing bookmark comparison prevents these navigations from capturing the old artifact.
for (const [fromName, toName] of [
    ['project-artifacts', 'project-artifacts'],
    ['projects-artifacts', 'projects-artifacts'],
    ['project-artifacts', 'projects-artifacts'],
    ['projects-artifacts', 'project-artifacts'],
]) {
    for (const [fromId, toId] of [['a', 'b'], ['b', 'a']]) {
        test(`artifact ${fromName}/${fromId} → ${toName}/${toId} holds navigation for one root fade`, async () => {
            const router = fakeRouter()
            const calls = []
            let release
            installSessionSwitchTransition(router, { run: (update, options) => { calls.push(options); release = update } })
            let passed = false
            const guarded = router.guards.resolve[0](artifactRoute(toId, toName, 'q'), artifactRoute(fromId, fromName))
            assert.ok(guarded instanceof Promise)
            guarded.then(() => { passed = true })
            await Promise.resolve()
            assert.equal(passed, false)
            assert.deepEqual(calls, [{ kind: 'session', settle: true, updateTimeoutMs: 800 }])
            let landed = false
            const update = release().then(() => { landed = true })
            await guarded
            assert.equal(landed, false)
            router.guards.after[0]()
            await update
            assert.equal(landed, true)
        })
    }
}

test('same bookmark, query changes, empty endpoints, and in-session artifact panes gain no fade', () => {
    const router = fakeRouter()
    let starts = 0
    installSessionSwitchTransition(router, { run: () => { starts++ } })
    const cases = [
        [artifactRoute('a'), artifactRoute('a')],
        [artifactRoute('a', 'projects-artifacts'), artifactRoute('a')],
        [{ ...artifactRoute('a'), query: { preview: '1' } }, artifactRoute('a')],
        [artifactRoute('a'), artifactRoute('')],
        [artifactRoute(''), artifactRoute('a')],
        [artifactRoute('a'), artifactRoute(undefined)],
        [artifactRoute(undefined), artifactRoute('a')],
        [artifactRoute('a'), { name: undefined, params: {} }],
        [{ ...route('s', 'session-artifacts'), params: { sessionId: 's', bookmarkId: 'b' } },
            { ...route('s', 'session-artifacts'), params: { sessionId: 's', bookmarkId: 'a' } }],
        [{ ...route('s', 'projects-session-artifacts'), params: { sessionId: 's', bookmarkId: 'b' } },
            { ...route('s', 'projects-session-artifacts'), params: { sessionId: 's', bookmarkId: 'a' } }],
    ]
    for (const [to, from] of cases) assert.equal(router.guards.resolve[0](to, from), undefined)
    assert.equal(starts, 0)
})

test('artifact push, redirected replace, and back/forward use the final bookmarks and one transition owner', async () => {
    const router = memoryRouter()
    router.beforeEach((to) => {
        if (to.name === 'projects-all') return { name: 'project-artifacts', params: { projectId: 'p', bookmarkId: 'c' } }
    })
    const updates = []
    installSessionSwitchTransition(router, { run: (update) => { updates.push(update()) } })
    await router.push('/projects/artifacts/a')
    assert.equal(updates.length, 0, 'initial selected artifact has no fade')
    await router.push('/projects/artifacts/b')
    await updates[0]
    assert.equal(updates.length, 1)
    await router.replace('/projects')
    await updates[1]
    assert.equal(router.currentRoute.value.params.bookmarkId, 'c')
    assert.equal(updates.length, 2, 'redirected cross-scope selection fades once')
    for (const [direction, bookmarkId, count] of [['back', 'a', 3], ['forward', 'c', 4]]) {
        const landed = new Promise((resolve) => {
            const remove = router.afterEach(() => { remove(); resolve() })
        })
        router[direction]()
        await landed
        await updates[count - 1]
        assert.equal(router.currentRoute.value.params.bookmarkId, bookmarkId)
        assert.equal(updates.length, count)
    }
    await router.push({ name: 'project-artifacts', params: { projectId: 'p', bookmarkId: 'c' }, query: { preview: '1' } })
    assert.equal(updates.length, 4, 'query-only navigation has no fade')
})

test('aborted and throwing artifact navigation release updates and permit later selection', async () => {
    const router = memoryRouter()
    const updates = []
    installSessionSwitchTransition(router, { run: (update) => { updates.push(update()) } })
    let failure = null
    router.beforeResolve(() => {
        if (failure === 'abort') return false
        if (failure === 'error') throw new Error('artifact navigation failed')
    })
    router.onError(() => {})
    await router.push('/projects/artifacts/a')
    failure = 'abort'
    assert.equal(isNavigationFailure(await router.push('/projects/artifacts/b')), true)
    await updates[0]
    assert.equal(router.currentRoute.value.params.bookmarkId, 'a')
    failure = 'error'
    await assert.rejects(router.push('/projects/artifacts/b'), /artifact navigation failed/)
    await updates[1]
    assert.equal(router.currentRoute.value.params.bookmarkId, 'a')
    failure = null
    await router.push('/projects/artifacts/b')
    await updates[2]
    assert.equal(router.currentRoute.value.params.bookmarkId, 'b')
    assert.equal(updates.length, 3)
})

test('artifact navigation without the browser API uses the real immediate fallback', async () => {
    const router = fakeRouter()
    installSessionSwitchTransition(router)
    const guarded = router.guards.resolve[0](artifactRoute('b'), artifactRoute('a'))
    assert.ok(guarded instanceof Promise)
    await guarded
    router.guards.after[0]()
})
