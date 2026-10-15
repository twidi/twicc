import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { runInNewContext } from 'node:vm'
import { createMemoryHistory, createRouter } from 'vue-router'

import { createRailScopeNavigation } from './railScopeNavigation.js'
import { PROJECT_TABS, routeNameForTab, tabForRouteName } from './projectTabs.js'
import { registerScopeMemory } from './scopeMemory.js'
import { PROJECT_DETAIL_ROUTES } from './tabRoutes.js'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const routerSource = read('../router.js')
const routeNamesInRouter = new Set([...routerSource.matchAll(/name: '([^']+)'/g)].map((match) => match[1]))

// ── The route table ────────────────────────────────────────────────────────────────────────────────────

test('router.js has a /stats route per scope, next to the bare routes', () => {
    assert.match(routerSource, /\{ path: 'stats', name: 'project-stats', component: \{ render: \(\) => null \} \}/)
    assert.match(routerSource, /\{ path: 'stats', name: 'projects-stats', component: \{ render: \(\) => null \} \}/)
    assert.match(routerSource, /\{ path: '', name: 'project', component/)
    assert.match(routerSource, /\{ path: '', name: 'projects-all', component/)
})

test('every route name the tabs can open exists in the router, and the scope page set matches them', () => {
    for (const isAllProjectsMode of [false, true]) {
        for (const tab of PROJECT_TABS) {
            const name = routeNameForTab(tab.id, { isAllProjectsMode })
            assert.ok(routeNamesInRouter.has(name), name)
            assert.ok(PROJECT_DETAIL_ROUTES.has(name), `${name} is a scope page route`)
        }
    }
    assert.equal(PROJECT_DETAIL_ROUTES.size, 10)
    for (const name of PROJECT_DETAIL_ROUTES) assert.ok(routeNamesInRouter.has(name), name)
})

// ── Shared sets, shortcuts, palette, settings text ─────────────────────────────────────────────────────────

test('App.vue and the command registry use the shared set of scope page routes, not their own copy', () => {
    for (const file of ['../App.vue', '../commands/staticCommands.js']) {
        const source = read(file)
        assert.match(source, /import \{[^}]*PROJECT_DETAIL_ROUTES[^}]*\} from '(\.\.?)\/utils\/tabRoutes'/, file)
        assert.ok(!/const PROJECT_DETAIL_ROUTES = new Set/.test(source), `${file} has a private copy`)
    }
})

test('the palette: a Timeline command on the bare route and a Stats command on /stats, with the same gating', () => {
    const source = read('../commands/staticCommands.js')
    const block = (id) => source.slice(source.indexOf(`id: '${id}'`), source.indexOf('},\n        {', source.indexOf(`id: '${id}'`)))
    const timeline = block('nav.project-tab.timeline')
    const stats = block('nav.project-tab.stats')
    assert.match(timeline, /label: 'Switch to Timeline Tab'/)
    assert.match(timeline, /when: \(\) => isOnProjectDetail\(\)/)
    assert.match(timeline, /isAllProjectsMode\(\) \? 'projects-all' : 'project'/)
    assert.match(stats, /label: 'Switch to Stats Tab'/)
    assert.match(stats, /when: \(\) => isOnProjectDetail\(\)/)
    assert.match(stats, /isAllProjectsMode\(\) \? 'projects-stats' : 'project-stats'/)
    assert.ok(source.indexOf("id: 'nav.project-tab.timeline'") < source.indexOf("id: 'nav.project-tab.stats'"))
})

test('the settings panel text follows the fixed map', () => {
    const source = read('../components/app/SettingsPopover.vue')
    assert.match(source, /label: 'Project home tabs'/)
    assert.match(source, /keys: \['Alt', 'Shift', '1–5'\], description: 'Jump to tab \(Timeline, Stats, Files, Git, Terminal\)'/)
    assert.ok(!source.includes("'1–4'"))
})

test('the scope page mounts the Timeline first, takes its tabs, numbering and routes from projectTabs.js', () => {
    const source = read('../components/project/ProjectDetailPanel.vue')
    assert.ok(source.indexOf('<wa-tab-panel name="timeline">') < source.indexOf('<wa-tab-panel name="stats">'))
    assert.match(source, /<TimelinePanel :project-id="projectId" \/>/)
    assert.match(source, /visibleProjectTabs\(\{ hasGitRepo: hasGitRepo\.value \}\)/)
    assert.match(source, /directTab\(index, \{ hasGitRepo: hasGitRepo\.value \}\)/)
    assert.match(source, /tabForRouteName\(route\.name\)/)
    assert.match(source, /routeNameForTab\(tabId, \{ isAllProjectsMode: isAllProjectsMode\.value \}\)/)
    assert.ok(!source.includes('DIRECT_TAB_MAP'), 'the numbering lives in projectTabs.js only')
    assert.match(source, /wa-tab-panel\[name="timeline"\]\)::part\(base\),/)
})

test('the rail keeps a scope selected on its Stats tab as well as on its Timeline', () => {
    const source = read('../components/sidebar/SidebarRail.vue')
    const body = source.slice(source.indexOf('function scopeSelected'), source.indexOf('function scopeClick'))
    const selected = (route) => runInNewContext(`${body}; scopeSelected`, { route })
    const project = (name, query = {}) => selected({ name, params: { projectId: 'p' }, query })('project', 'p')
    assert.deepEqual(['project', 'project-stats', 'project-files'].map((name) => project(name)), [true, true, false])
    assert.equal(selected({ name: 'project-stats', params: { projectId: 'p' }, query: { workspace: 'w' } })('project', 'p'), false)
    const workspace = (name) => selected({ name, params: {}, query: { workspace: 'w' } })('workspace', 'w')
    assert.deepEqual(['projects-all', 'projects-stats', 'projects-files'].map(workspace), [true, true, false])
})

test('a scope page without a git root leaves the Git tab for the scope home, which is the Timeline', () => {
    const source = read('../components/project/ProjectDetailPanel.vue')
    const watcher = source.slice(source.indexOf("if (tabId === 'git' && !hasGit)"))
    assert.match(watcher.slice(0, 700), /router\.replace\(\{\s*name: buildProjectBaseRouteName\(isAllProjectsMode\.value\)/)
    assert.equal(routeNameForTab('timeline', { isAllProjectsMode: false }), 'project')
    assert.equal(tabForRouteName('project'), 'timeline')
    assert.ok(source.includes('which is now the Timeline'))
})

// ── Scope memory and the rail ──────────────────────────────────────────────────────────────────────────────
// The memory is module state shared by the whole file: every test uses its own project id.

async function scopeFixture() {
    const router = createRouter({ history: createMemoryHistory(), routes: [
        { path: '/', name: 'home', component: {} },
        { path: '/project/:projectId', name: 'project', component: {} },
        { path: '/project/:projectId/stats', name: 'project-stats', component: {} },
        { path: '/project/:projectId/files', name: 'project-files', component: {} },
        { path: '/projects', name: 'projects-all', component: {} },
        { path: '/projects/stats', name: 'projects-stats', component: {} },
    ] })
    await router.push('/')
    return router
}

async function withScopeMemory(run) {
    const router = await scopeFixture()
    const originalWindow = globalThis.window
    globalThis.window = { history: { state: { position: 0 } } }
    try {
        registerScopeMemory(router)
        await run(router)
    } finally {
        globalThis.window = originalWindow
    }
}

test('scope memory restores a remembered Stats location as Stats, for a project and for a workspace', async () => {
    await withScopeMemory(async (router) => {
        await router.push({ name: 'project-stats', params: { projectId: 'p' } })
        await router.push('/')
        await router.push({ name: 'project', params: { projectId: 'p' } })
        assert.equal(router.currentRoute.value.name, 'project-stats')
        await router.push('/')
        await router.push({ name: 'projects-stats', query: { workspace: 'w' } })
        await router.push('/')
        await router.push({ name: 'projects-all', query: { workspace: 'w' } })
        assert.deepEqual([router.currentRoute.value.name, router.currentRoute.value.query.workspace], ['projects-stats', 'w'])
    })
})

test('a remembered Timeline (the bare route) is not restored as anything else, and tool tabs still are', async () => {
    await withScopeMemory(async (router) => {
        await router.push({ name: 'project', params: { projectId: 'q' } })
        await router.push('/')
        await router.push({ name: 'project', params: { projectId: 'q' } })
        assert.equal(router.currentRoute.value.name, 'project')
        await router.push({ name: 'project-files', params: { projectId: 'q' } })
        await router.push('/')
        await router.push({ name: 'project', params: { projectId: 'q' } })
        assert.equal(router.currentRoute.value.name, 'project-files')
    })
})

test('switching between Timeline and Stats inside a scope is not a re-entry: the memory leaves it alone', async () => {
    await withScopeMemory(async (router) => {
        await router.push({ name: 'project-stats', params: { projectId: 'r' } })
        await router.push({ name: 'project', params: { projectId: 'r' } })
        assert.equal(router.currentRoute.value.name, 'project')
    })
})

test('the rail keeps pointing to the bare route, which is the Timeline, even after Stats was remembered', async () => {
    await withScopeMemory(async (router) => {
        await router.push({ name: 'project-stats', params: { projectId: 's' } })
        await router.push('/')
        await createRailScopeNavigation(router, () => {})('project', 's')
        assert.equal(router.currentRoute.value.name, 'project')
    })
})
