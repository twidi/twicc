import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createRailScopeNavigation, openSidebarCheckbox, isRailScopeRootNavigation } from './railScopeNavigation.js'

async function fixture() {
    const events = []
    const router = createRouter({ history: createMemoryHistory(), routes: [
        { path: '/', name: 'home', component: {} },
        { path: '/project/:projectId', name: 'project', component: {} },
        { path: '/projects', name: 'projects-all', component: {} },
    ] })
    await router.push('/')
    return { router, events, navigate: createRailScopeNavigation(router, () => events.push(router.currentRoute.value.fullPath)) }
}
test('cold project navigation opens destination after mounting; project clears workspace', async () => {
    const f = await fixture()
    await f.navigate('project', 'p')
    assert.equal(f.router.currentRoute.value.name, 'project')
    assert.equal(f.router.currentRoute.value.params.projectId, 'p')
    assert.equal(f.router.currentRoute.value.query.workspace, '')
    assert.equal(f.events.length, 1)
})
test('workspace and duplicate navigation open sidebar without a session', async () => {
    const f = await fixture()
    await f.navigate('workspace', 'w'); await f.navigate('workspace', 'w')
    assert.equal(f.router.currentRoute.value.query.workspace, 'w')
    assert.equal(f.router.currentRoute.value.params.sessionId, undefined)
    assert.equal(f.events.length, 2)
})
test('aborted, rejected, and superseded navigation never opens unrelated sidebar', async () => {
    const f = await fixture()
    f.router.beforeEach(to => to.params.projectId === 'abort' ? false : to.params.projectId === 'reject' ? Promise.reject(new Error('reject')) : true)
    const errors = []
    f.router.onError(error => errors.push(error.message))
    await f.navigate('project', 'abort'); await f.navigate('project', 'reject')
    assert.deepEqual(f.events, [])
    assert.deepEqual(errors, ['reject'], 'normal router error reporting remains active')
    assert.equal(isRailScopeRootNavigation(f.router, f.router.resolve({ name: 'project', params: { projectId: 'reject' }, query: { workspace: '' } })), false)
    await Promise.all([f.navigate('project', 'old'), f.navigate('workspace', 'new')])
    assert.deepEqual(f.events, ['/projects?workspace=new'])
})
for (const mobile of [false, true]) {
    test(`sidebar opening is idempotent with ${mobile ? 'mobile' : 'desktop'} checkbox polarity`, () => {
        let changes = 0
        const checkbox = { checked: !mobile, dispatchEvent: () => changes++ }
        openSidebarCheckbox(checkbox, mobile)
        openSidebarCheckbox(checkbox, mobile)
        assert.equal(checkbox.checked, mobile)
        assert.equal(changes, 1)
        openSidebarCheckbox(null, mobile)
    })
}

test('rail root navigation bypasses remembered tool tabs only during its push', async () => {
    const { registerScopeMemory } = await import('./scopeMemory.js')
    const f = await fixture()
    f.router.addRoute({ path: '/project/:projectId/files', name: 'project-files', component: {} })
    const originalWindow = globalThis.window
    globalThis.window = { history: { state: { position: 0 } } }
    try {
        registerScopeMemory(f.router)
        await f.router.push({ name: 'project-files', params: { projectId: 'p' } })
        await f.router.push('/')
        await f.navigate('project', 'p')
        assert.equal(f.router.currentRoute.value.name, 'project')
        assert.equal(f.events.length, 1)
    } finally { globalThis.window = originalWindow }
})

test('cold destination listener mounts through Vue before the open event', async () => {
    const { watch } = await import('vue')
    const f = await fixture()
    const events = new EventTarget()
    let opened = 0
    const stop = watch(f.router.currentRoute, () => {
        events.addEventListener('twicc:open-sidebar', () => opened++, { once: true })
    }, { flush: 'post' })
    try {
        const navigate = createRailScopeNavigation(f.router, () => events.dispatchEvent(new Event('twicc:open-sidebar')))
        await navigate('project', 'cold')
        assert.equal(opened, 1)
    } finally { stop() }
})

// Install the production workspace guards without loading the browser-only router.
function registerWorkspaceGuards(router, browser) {
    const source = readFileSync(new URL('../router.js', import.meta.url), 'utf8')
    const start = source.indexOf('// Propagate workspace query param')
    const end = source.indexOf('// Per-scope last-location memory', start)
    runInNewContext(source.slice(start, end), {
        router, window: browser, history: browser.history, URLSearchParams,
    })
}

test('All Projects root clears workspace and selection, bypasses remembered tools, and reopens on duplicate clicks', async () => {
    const { registerScopeMemory } = await import('./scopeMemory.js')
    const f = await fixture()
    f.router.addRoute({ path: '/projects/files', name: 'projects-files', component: {} })
    f.router.addRoute({ path: '/projects/:projectId/session/:sessionId', name: 'projects-session', component: {} })
    const originalWindow = globalThis.window
    const browser = { history: { state: { position: 0 }, replaceState: (_state, _title, url) => browser.url = url } }
    globalThis.window = browser
    try {
        registerWorkspaceGuards(f.router, browser)
        registerScopeMemory(f.router)
        await f.router.push({ name: 'projects-files' })
        const selectedSession = { name: 'projects-session', params: { projectId: 'p', sessionId: 's' }, query: { workspace: 'w' } }
        await f.router.push(selectedSession)
        await f.router.push({ name: 'projects-all', query: { workspace: '' } })
        assert.equal(f.router.currentRoute.value.name, 'projects-files', 'ordinary scope entry restores the remembered Files tab')
        await f.router.push(selectedSession)
        await f.navigate('all-projects')
        assert.equal(f.router.currentRoute.value.name, 'projects-all')
        assert.deepEqual(f.router.currentRoute.value.params, {})
        assert.deepEqual(f.router.currentRoute.value.query, { workspace: '' })
        assert.equal(browser.url, '/projects', 'production cleanup removes the clear signal from the browser URL')
        assert.deepEqual(f.events, ['/projects?workspace='])
        await f.navigate('all-projects')
        assert.deepEqual(f.events, ['/projects?workspace=', '/projects?workspace='], 'duplicate navigation opens again')
    } finally { globalThis.window = originalWindow }
})
