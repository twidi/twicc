import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { createPinia, defineStore } from 'pinia'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import { buildProjectActivityIndex, createProjectActivityComparator } from '../../utils/projectActivity.js'
import { buildProjectTree, flattenProjectTree } from '../../utils/projectTree.js'
import { splitProjectsByPriority } from '../../utils/projectSort.js'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../utils/workspaceIds.js'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
// Execute production getters with real Pinia reactivity. The full store imports browser dependencies.
const dataSource = read('../../stores/data.js')
const getters = new Function('buildProjectActivityIndex', 'createProjectActivityComparator',
    `return { ${dataSource.slice(dataSource.indexOf('        // Data getters'), dataSource.indexOf('        // Session scope of a project:'))} }`,
)(buildProjectActivityIndex, createProjectActivityComparator)
const useProjects = defineStore('activity-ui-test', { state: () => ({ projects: {}, weeklyActivity: {} }), getters })
const project = (id, fields = {}) => ({ id, name: id, directory: `/dev/${id}`, mtime: 10, archived: false, stale: false, ...fields })
function fixture() {
    const data = useProjects(createPinia())
    const projects = [project('parent'), project('other', { mtime: 20 }),
        project('child', { mtime: 40, worktree_of: 'parent', archived: true }),
        project('z-tree', { name: null, mtime: 60 }), project('a-tree', { name: null, mtime: 5 })]
    data.projects = Object.fromEntries(projects.map(p => [p.id, p]))
    data.getProject = id => data.projects[id]
    const settings = Vue.reactive({ isShowArchivedProjects: false })
    const workspaces = { getSelectableWorkspaces: [{ id: 'ws-z', name: 'Z' }, { id: 'ws-a', name: 'A' }],
        getVisibleProjectIds: () => ['other', 'parent', 'child'],
        getWorkspaceById: () => ({ id: 'ws-z', name: 'Z', projectIds: ['other', 'parent'] }) }
    return { data, settings, workspaces }
}
function setup(file, fixture, bindings, props = {}, extras = {}) {
    const { descriptor } = parse(read(file))
    return runInNewContext(`${descriptor.scriptSetup.content.replace(/^import .*$/gm, '')}; ({ ${bindings} })`, {
        ...Vue, buildProjectTree, flattenProjectTree, splitProjectsByPriority, isWorkspaceProjectId, extractWorkspaceId,
        ALL_PROJECTS_ID: '__all__', defineProps: () => props, defineEmits: () => () => {}, defineOptions: () => {},
        useDataStore: () => fixture.data, useSettingsStore: () => fixture.settings,
        useWorkspacesStore: () => fixture.workspaces, useRoute: () => ({ query: {} }), ...extras,
    })
}
const ids = rows => Array.from(rows, p => p.id)
const treeIds = rows => Array.from(rows).filter(p => !p.isFolder).map(p => p.project.id)

// A local raw-mtime sort in navigation would undo parent promotion and fail this test.
test('global detail navigation inherits parent activity and preserves groups and workspace order', () => {
    const f = fixture()
    const { items } = setup('./ProjectDetailNavList.vue', f, 'items', { projectId: '__all__' })
    assert.deepEqual(ids(items.value.filter(p => p.type === 'workspace')), ['ws-z', 'ws-a'])
    assert.deepEqual(ids(items.value.filter(p => p.type === 'project')), ['parent', 'other', 'z-tree', 'a-tree'])
    f.data.projects.child.mtime = 15
    assert.deepEqual(ids(items.value.filter(p => p.type === 'project')), ['other', 'parent', 'z-tree', 'a-tree'])
})

test('workspace detail navigation retains member manual order despite newer parent worktree', () => {
    const { items } = setup('./ProjectDetailNavList.vue', fixture(), 'items', { projectId: 'ws:ws-z' })
    assert.deepEqual(ids(items.value.filter(p => p.type === 'project')), ['other', 'parent'])
})

test('home project list promotes named parent and keeps unnamed directory tree alphabetical', () => {
    const { namedProjects, treeRoots } = setup('./ProjectList.vue', fixture(), 'namedProjects, treeRoots')
    assert.deepEqual(ids(namedProjects.value), ['parent', 'other'])
    assert.deepEqual(treeIds(flattenProjectTree(treeRoots.value)), ['a-tree', 'z-tree'])
})

test('project options retain workspace priority and alphabetical trees over effective activity order', () => {
    const f = fixture()
    const props = Vue.reactive({ projects: f.data.getListableProjects, priorityProjectIds: null })
    const state = setup('./ProjectSelectOptions.vue', f, 'namedProjects, flatTree, prioritySplit', props)
    assert.deepEqual(ids(state.namedProjects.value), ['parent', 'other'])
    assert.deepEqual(treeIds(state.flatTree.value), ['a-tree', 'z-tree'])
    props.priorityProjectIds = ['other', 'parent']
    assert.deepEqual(ids(state.prioritySplit.value.prioritized), ['other', 'parent'])
})

test('new session picker inherits activity while retaining workspace priority and visibility filters', () => {
    const f = fixture()
    f.data.projects.stale = project('stale', { mtime: 100, stale: true })
    const state = setup('./NewSessionProjectPicker.vue', f, 'splitNamedProjects, splitFlatTree, pickableWorktreesOf')
    assert.deepEqual(ids(state.splitNamedProjects.value.others), ['parent', 'other'])
    assert.deepEqual(treeIds(state.splitFlatTree.value.others), ['a-tree', 'z-tree'])
    assert.deepEqual(ids(state.pickableWorktreesOf('parent')), [])
    f.settings.isShowArchivedProjects = true
    assert.deepEqual(ids(state.pickableWorktreesOf('parent')), ['child'])
    const prioritized = setup('./NewSessionProjectPicker.vue', f, 'splitNamedProjects', {},
        { useRoute: () => ({ query: { workspace: 'ws-z' } }) })
    assert.deepEqual(ids(prioritized.splitNamedProjects.value.prioritized), ['other', 'parent'])
})

test('palette project entries promote parents while retaining mixed worktrees and archive visibility', () => {
    const f = fixture()
    const source = read('../../commands/staticCommands.js')
    const start = source.indexOf('    function pickerEntries()')
    const end = source.indexOf('\n    }', start) + 6
    const entries = runInNewContext(`${source.slice(start, end)}; pickerEntries`, { data: f.data, settings: f.settings })
    assert.deepEqual(ids(entries()), ['z-tree', 'parent', 'other', 'a-tree'])
    f.settings.isShowArchivedProjects = true
    assert.deepEqual(ids(entries()), ['z-tree', 'child', 'parent', 'other', 'a-tree'])
})

// Raw project.mtime in any date binding would fail these rendered-card assertions.
test('project card renders effective last activity in absolute date, relative date and tooltip', async () => {
    const f = fixture()
    f.settings.getSessionTimeFormat = 'absolute'
    const props = { project: f.data.projects.parent }
    const { descriptor } = parse(read('./ProjectCard.vue'))
    const bindings = [...descriptor.scriptSetup.content.matchAll(/^(?:const|function) (\w+)/gm)].map(match => match[1]).join(', ')
    const state = setup('./ProjectCard.vue', f, bindings, props, {
        useHomeCardEntrance: () => {}, useProjectMark: () => ({ dotColor: Vue.ref(null) }),
        formatDate: timestamp => `date:${timestamp}`,
        SESSION_TIME_FORMAT: { RELATIVE_SHORT: 'short', RELATIVE_NARROW: 'narrow' },
    })
    const compiled = compileTemplate({ source: descriptor.template.content, filename: 'ProjectCard.vue', id: 'activity-ui',
        compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') } })
    assert.deepEqual(compiled.errors, [])
    const render = new Function('Vue', compiled.code)(Vue)
    let date
    const visit = node => {
        if (!node || typeof node !== 'object') return
        if (node.type === 'wa-relative-time') date = node.props['.date'] ?? node.props.date
        if (Array.isArray(node.children)) node.children.forEach(visit)
    }
    const renderCard = async () => {
        const app = Vue.createSSRApp({ render() {
            const node = render.call(this, Vue.proxyRefs({ ...state, $slots: {}, project: props.project, formatDate: n => `date:${n}` }), [])
            visit(node)
            return node
        } })
        for (const name of ['ProjectBadge', 'ProjectDirectoryPath', 'ProjectMissingDirectoryNote', 'AggregatedProcessIndicator',
            'CodeCommentsIndicator', 'ActivitySparkline', 'CostDisplay', 'AppTooltip']) {
            app.component(name, { render() { return Vue.h('span', this.$slots.default?.()) } })
        }
        return renderToString(app)
    }
    assert.match(await renderCard(), /date:40/)
    f.settings.getSessionTimeFormat = 'short'
    assert.match(await renderCard(), /Last activity: date:40/)
    assert.equal(date.getTime(), 40000)
    f.data.projects.child.mtime = 50
    assert.match(await renderCard(), /Last activity: date:50/)
    assert.equal(date.getTime(), 50000)
    assert.equal(props.project.mtime, 10)
})
