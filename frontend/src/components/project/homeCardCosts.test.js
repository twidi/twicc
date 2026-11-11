import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import * as Vue from 'vue'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import { createAppI18n } from '../../i18n/index.js'
import { createWorkspaceMembership, getWorktreeIdsByParent } from '../../utils/projectScopes.js'
import { isWorkspaceProjectId } from '../../utils/workspaceIds.js'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const source = read('../../stores/data.js')
const scopeGetter = runInNewContext(`({ ${source.slice(source.indexOf('        getProjectScopeIds:'),
    source.indexOf('        // Indicator/badge scope'))} })`, { ALL_PROJECTS_ID: '__all__', isWorkspaceProjectId })

function fixture() {
    const project = (id, total_cost, extra = {}) => ({ id, name: id, total_cost, archived: false, mtime: 1, ...extra })
    const rows = [project('main', 10), project('tree', 20, { worktree_of: 'main' }),
        project('old-tree', 30, { worktree_of: 'main', archived: true }),
        project('old-project', 40, { archived: true }),
        project('old-project-tree', 50, { worktree_of: 'old-project' }),
        project('unrelated', 999)]
    const data = Vue.reactive({ projects: Object.fromEntries(rows.map(p => [p.id, p])), weeklyActivity: {} })
    data.getProject = id => data.projects[id]
    data.getProjectScopeIds = scopeGetter.getProjectScopeIds(data)
    data.getProjectActivity = () => 1
    const settings = Vue.reactive({ areCostsShown: true, isShowArchivedProjects: false })
    const workspace = { id: 'ws', name: 'Workspace', projectIds: ['main', 'tree', 'old-project', 'unknown'] }
    const membership = createWorkspaceMembership({ getWorkspaceById: () => workspace,
        getProject: data.getProject, getWorktreeIdsByParent: () => getWorktreeIdsByParent(data),
        isShowArchived: () => settings.isShowArchivedProjects })
    const workspaces = { getVisibleProjectIds: membership.visible, getAllProjectIds: membership.all,
        isActivable: () => true }
    return { data, settings, workspace, workspaces }
}

function costCard(file, f, props) {
    const { descriptor } = parse(read(file))
    const bindings = [...descriptor.scriptSetup.content.matchAll(/^(?:const|function) (\w+)/gm)].map(m => m[1]).join(', ')
    const state = runInNewContext(`${descriptor.scriptSetup.content.replace(/^import .*$/gm, '')}; ({ ${bindings} })`, {
        ...Vue, defineProps: () => props, defineEmits: () => () => {},
        useDataStore: () => f.data, useSettingsStore: () => f.settings, useWorkspacesStore: () => f.workspaces,
        useHomeCardEntrance: () => {}, useProjectMark: () => ({ dotColor: Vue.ref(null) }),
        aggregateWeeklyActivity: () => [], SESSION_TIME_FORMAT: {}, formatDate: () => '',
    })
    const compiled = compileTemplate({ source: descriptor.template.content, filename: file, id: 'cost-test',
        compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') } })
    assert.deepEqual(compiled.errors, [])
    const render = new Function('Vue', compiled.code)(Vue)
    return async () => {
        const app = Vue.createSSRApp({ render() {
            return render.call(this, Vue.proxyRefs({ ...state, ...props, $t: createAppI18n().global.t, $slots: {}, formatDate: () => '' }), [])
        } })
        // Capture the card's public CostDisplay prop, before number formatting.
        app.component('CostDisplay', { props: ['cost'], render() { return Vue.h('output', { 'data-cost': this.cost }) } })
        for (const name of ['ProjectBadge', 'ProjectDirectoryPath', 'ProjectMissingDirectoryNote',
            'AggregatedProcessIndicator', 'CodeCommentsIndicator', 'ActivitySparkline', 'AppTooltip']) {
            app.component(name, { render: () => Vue.h('span') })
        }
        const html = await renderToString(app)
        return Number(html.match(/data-cost="([^"]+)"/)[1])
    }
}

test('home project cost includes archived worktrees and updates without changing its own cost', async () => {
    const f = fixture()
    const cost = costCard('./ProjectCard.vue', f, { project: f.data.projects.main })
    assert.equal(await cost(), 60)
    f.settings.isShowArchivedProjects = true
    assert.equal(await cost(), 60)
    f.data.projects['old-tree'].total_cost = 35
    assert.equal(await cost(), 65)
    assert.equal(f.data.projects.main.total_cost, 10)
    const treeCost = costCard('./ProjectCard.vue', f, { project: f.data.projects.tree })
    assert.equal(await treeCost(), 20)
})

test('home workspace cost includes archived members and worktrees exactly once regardless of visibility', async () => {
    const f = fixture()
    const cost = costCard('../workspace/WorkspaceCard.vue', f, { workspace: f.workspace })
    assert.equal(await cost(), 150)
    f.settings.isShowArchivedProjects = true
    assert.equal(await cost(), 150)
    f.data.projects['old-project'].total_cost = 45
    assert.equal(await cost(), 155)
})
