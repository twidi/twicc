import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'

const source = readFileSync(new URL('./SidebarRail.vue', import.meta.url), 'utf8')
async function render(rows = [], recentProjects = [], recentWorkspaces = []) {
    const { descriptor } = parse(source)
    const compiled = compileTemplate({ source: descriptor.template.content, filename: 'SidebarRail.vue', id: 'rail', compilerOptions: { mode: 'function' } })
    const render = runInNewContext(`(function() { ${compiled.code} })()`, { Vue })
    const ctx = { top: [], bottom: [], rows, recentProjects, recentWorkspaces,
        hasCentralContent: !!(rows.length || recentProjects.length || recentWorkspaces.length),
        currentSessionId: null, route: { name: 'project', params: { projectId: 'p' }, query: {} },
        store: { resolvedProjectIcons: {}, getProjectDisplayName: id => `Project ${id}` },
        workspacesStore: { getVisibleProjectIds: () => ['p', 'q'] },
        projectColor: () => 'red', scopeSelected: (kind, id) => kind === 'project' && id === 'p',
        sessionTooltipTrigger: 'manual', setSessionTooltip: () => {}, setEntryTooltip: () => {},
    }
    const app = Vue.createSSRApp({ render, data: () => ctx })
    for (const name of ['AppTooltip', 'ProjectMark', 'SessionListItem', 'ProcessIndicator', 'SettingsPopover', 'PeerInboxBadge', 'NewSessionProjectPicker', 'ProjectBadge', 'AggregatedProcessIndicator']) {
        app.component(name, { props: ['projectId', 'projectIds'], setup: (props, { slots }) => () => Vue.h('span', { 'data-component': name, 'data-project': props.projectId, 'data-projects': props.projectIds?.join(',') }, slots.default?.()) })
    }
    app.config.warnHandler = () => {}
    return renderToString(app)
}
test('compiled rail renders ordered recent groups and project/workspace tooltip scopes', async () => {
    const html = await render([], [{ id: 'p' }], [{ id: 'w', name: 'Workspace', color: 'blue' }])
    assert.ok(html.indexOf('sidebar-rail-project-p') >= 0)
    assert.ok(html.indexOf('sidebar-rail-project-p') < html.indexOf('sidebar-rail-workspace-w'))
    assert.match(html, /aria-label="Project p" aria-pressed="true"/)
    assert.match(html, /aria-label="Workspace"/)
    assert.match(html, /data-component="ProjectBadge" data-project="p"/)
    assert.match(html, /data-projects="p,q"/)
    assert.equal((html.match(/<wa-divider/g) || []).length, 3)
})
test('compiled rail only separates nonempty groups and retains every recent project', async () => {
    assert.equal(((await render()).match(/<wa-divider/g) || []).length, 0)
    const projects = Array.from({ length: 80 }, (_, i) => ({ id: `p${i}` }))
    const html = await render([], projects)
    assert.equal((html.match(/id="sidebar-rail-project-/g) || []).length, 80)
    assert.equal((html.match(/<wa-divider/g) || []).length, 2)
})

test('compiled center keeps session previews first and separates all three nonempty groups', async () => {
    const html = await render([{ session: { id: 's', title: 'Session', project_id: 'p' }, processState: { state: 'assistant_turn' } }],
        [{ id: 'p' }], [{ id: 'w', name: 'Workspace' }])
    assert.ok(html.indexOf('sidebar-rail-session-s') < html.indexOf('sidebar-rail-project-p'))
    assert.ok(html.indexOf('sidebar-rail-project-p') < html.indexOf('sidebar-rail-workspace-w'))
    assert.equal((html.match(/<wa-divider/g) || []).length, 4)
    assert.match(html, /data-component="SessionListItem"/)
})
