import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { computed, ref, reactive } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'
import { splitProjectsByPriority } from '../../utils/projectSort.js'
import { buildProjectTree, flattenProjectTree } from '../../utils/projectTree.js'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const source = read('./NewSessionProjectPicker.vue')
const { descriptor } = parse(source)
const project = (id, fields = {}) => ({ id, name: id, directory: `/dev/${id}`, archived: false, stale: false, ...fields })

function setup({ workspace = null, projects = [], worktrees = [] } = {}) {
    const settings = reactive({ isShowArchivedProjects: false })
    const emitted = []
    const state = runInNewContext(`${descriptor.scriptSetup.content.replace(/^import .*$/gm, '')}; ({
        splitNamedProjects, splitFlatTree, activeWsLabel, pickableWorktreesOf,
        isNewSessionWorktreesExpanded, handleNewSessionSelect, handleProjectResolved,
        openWorktreeDialog, dropdownRef, createProjectDialogRef, worktreeDialogRef,
    })`, {
        computed, ref, splitProjectsByPriority, buildProjectTree, flattenProjectTree,
        defineOptions: () => {}, defineEmits: () => (...args) => emitted.push(args),
        useDataStore: () => ({ getListableProjects: projects, getWorktreesOf: () => worktrees }),
        useSettingsStore: () => settings,
        useRoute: () => ({ query: workspace ? { workspace: workspace.id } : {} }),
        useWorkspacesStore: () => ({ getWorkspaceById: () => workspace, getVisibleProjectIds: () => workspace.visibleIds }),
    })
    return { state, settings, emitted }
}
const ids = projects => Array.from(projects, p => p.id)
const leaves = tree => Array.from(tree).filter(row => !row.isFolder).map(row => row.project.id)

test('project picker preserves workspace priority, named rows, and unnamed hierarchy', () => {
    const workspace = { id: 'ws', name: 'Workspace', projectIds: ['second', 'first', 'tree'], visibleIds: ['first', 'second', 'tree'] }
    const { state } = setup({ workspace, projects: [
        project('first'), project('outside'), project('second'),
        project('tree', { name: null, directory: '/dev/work/tree' }),
        project('other-tree', { name: null, directory: '/dev/other/tree' }),
    ] })
    assert.deepEqual(ids(state.splitNamedProjects.value.prioritized), ['second', 'first'])
    assert.deepEqual(ids(state.splitNamedProjects.value.others), ['outside'])
    assert.deepEqual(leaves(state.splitFlatTree.value.prioritized), ['tree'])
    assert.deepEqual(leaves(state.splitFlatTree.value.others), ['other-tree'])
    assert.equal(state.activeWsLabel.value, 'Workspace projects')
    assert.ok(state.splitFlatTree.value.prioritized.some(row => row.isFolder))
})

test('archived visibility applies to projects and worktrees while stale entries remain excluded', () => {
    const { state, settings } = setup({ projects: [project('live'), project('archived', { archived: true }), project('stale', { stale: true })],
        worktrees: [project('worktree'), project('archived-wt', { archived: true }), project('stale-wt', { stale: true })] })
    assert.deepEqual(ids(state.splitNamedProjects.value.others), ['live'])
    assert.deepEqual(ids(state.pickableWorktreesOf('live')), ['worktree'])
    settings.isShowArchivedProjects = true
    assert.deepEqual(ids(state.splitNamedProjects.value.others), ['live', 'archived'])
    assert.deepEqual(ids(state.pickableWorktreesOf('live')), ['worktree', 'archived-wt'])
})

test('without a workspace the picker preserves source ordering and hierarchy', () => {
    const { state } = setup({ projects: [project('second'), project('first'), project('unnamed', { name: null, directory: '/dev/parent/unnamed' })] })
    assert.deepEqual(ids(state.splitNamedProjects.value.prioritized), [])
    assert.deepEqual(ids(state.splitNamedProjects.value.others), ['second', 'first'])
    assert.deepEqual(leaves(state.splitFlatTree.value.others), ['unnamed'])
    assert.equal(state.activeWsLabel.value, null)
})

test('worktree expansion prevents closing and emits no creation; selection emits its own project', () => {
    const { state, emitted } = setup()
    let prevented = 0
    const toggle = { detail: { item: { value: 'worktrees-toggle:parent' } }, preventDefault: () => prevented++ }
    state.handleNewSessionSelect(toggle)
    assert.equal(state.isNewSessionWorktreesExpanded('parent'), true)
    state.handleNewSessionSelect(toggle)
    assert.equal(state.isNewSessionWorktreesExpanded('parent'), false)
    assert.equal(prevented, 2)
    assert.deepEqual(emitted, [])
    state.handleNewSessionSelect({ detail: { item: { value: 'worktree' } } })
    assert.deepEqual(emitted, [['select-project', 'worktree']])
})

test('New project and new worktree resolution feed the same selection flow', () => {
    const { state, emitted } = setup()
    let opened = 0
    state.createProjectDialogRef.value = { open: () => opened++ }
    state.handleNewSessionSelect({ detail: { item: { value: '__new_project__' } } })
    assert.equal(opened, 1)
    assert.deepEqual(emitted, [])
    state.dropdownRef.value = { open: true }
    const parent = project('parent')
    state.worktreeDialogRef.value = { open: p => { assert.equal(p, parent); assert.equal(state.dropdownRef.value.open, false) } }
    state.openWorktreeDialog(parent)
    state.handleProjectResolved(project('created-project'))
    state.handleProjectResolved(project('created-worktree'))
    assert.deepEqual(emitted, [['select-project', 'created-project'], ['select-project', 'created-worktree']])
})

test('shared menu preserves all row sections, worktree controls, and dialog events', () => {
    const content = descriptor.template.content
    assert.equal((content.match(/value="__new_project__"/g) ?? []).length, 1)
    for (const section of ['splitNamedProjects.prioritized', 'splitFlatTree.prioritized', 'splitNamedProjects.others', 'splitFlatTree.others']) {
        assert.ok(content.includes(section), section)
    }
    assert.equal((content.match(/<WorktreePickerRows/g) ?? []).length, 4)
    assert.equal((content.match(/<WorktreeButton/g) ?? []).length, 4)
    assert.match(content, /Other projects/)
    assert.match(content, /<slot name="trigger"/)
    assert.match(content, /<ProjectEditDialog[^>]*@saved="handleProjectResolved"/)
    assert.match(content, /<WorktreeDialog[^>]*@resolved="handleProjectResolved"/)
    const view = read('../../views/ProjectView.vue')
    assert.equal((view.match(/<NewSessionProjectPicker/g) ?? []).length, 2)
    assert.doesNotMatch(view, /<WorktreePickerRows|handleNewSessionSelect/)
    for (const file of ['../../views/ProjectView.vue', '../../views/HomeView.vue']) {
        assert.match(read(file), /@new-session="handleNewSession"/)
        assert.match(read(file), /useNewSessionCreation/)
    }
    const rail = read('../sidebar/SidebarRail.vue')
    assert.match(rail, /<NewSessionProjectPicker placement="right-end" @select-project="emit\('new-session', \$event\)"/)
    assert.match(rail, /<button id="sidebar-rail-new-session" slot="trigger" type="button" class="rail-button"/)
})

test('picker and both integrated views compile their SFC scripts, templates, and styles', () => {
    for (const file of ['./NewSessionProjectPicker.vue', '../../views/HomeView.vue', '../../views/ProjectView.vue']) {
        const { descriptor, errors } = parse(read(file), { filename: file })
        assert.deepEqual(errors, [], file)
        const id = 'data-v-picker'
        const script = compileScript(descriptor, { id })
        assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: file, id,
            compilerOptions: { bindingMetadata: script.bindings } }).errors, [], file)
        for (const style of descriptor.styles) {
            assert.deepEqual(compileStyle({ source: style.content, filename: file, id, scoped: style.scoped }).errors, [], file)
        }
    }
})

test('compiled picker keeps dialog controls outside the native Web Awesome split-button group', async () => {
    const Vue = await import('vue')
    const { state } = setup()
    const body = { children: [] }
    function node(kind, tagName = '') {
        const classes = new Set()
        return {
            kind, tagName: tagName.toUpperCase(), children: [], parent: null, props: {},
            classList: {
                add: value => classes.add(value),
                toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value),
                contains: value => classes.has(value),
            },
            closest(selector) {
                const tags = selector.split(',').map(value => value.trim().toUpperCase())
                if (tags.includes(this.tagName)) return this
                return this.parent?.closest?.(selector) ?? null
            },
            querySelector(selector) {
                const tags = selector.split(',').map(value => value.trim().toUpperCase())
                for (const child of this.children) {
                    if (tags.includes(child.tagName)) return child
                    const found = child.querySelector?.(selector)
                    if (found) return found
                }
                return null
            },
        }
    }
    const { createApp } = Vue.createRenderer({
        createElement: tag => node('element', tag),
        createText: text => ({ ...node('text'), text }),
        createComment: text => ({ ...node('comment'), text }),
        setText: (el, text) => { el.text = text },
        setElementText: (el, text) => { el.text = text },
        parentNode: el => el.parent,
        nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] ?? null,
        querySelector: selector => selector === 'body' ? body : null,
        patchProp: (el, key, previous, value) => { el.props[key] = value },
        insert(el, parent, anchor = null) {
            if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1)
            const index = anchor ? parent.children.indexOf(anchor) : parent.children.length
            parent.children.splice(index, 0, el)
            el.parent = parent
        },
        remove(el) {
            el.parent.children.splice(el.parent.children.indexOf(el), 1)
            el.parent = null
        },
    })
    const compiled = compileTemplate({ source: descriptor.template.content, filename: 'NewSessionProjectPicker.vue',
        id: 'data-v-picker-group', compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') } })
    assert.deepEqual(compiled.errors, [])
    const render = new Function('Vue', compiled.code)(Vue)
    const picker = { setup: () => state, render }
    const dialog = { render: () => Vue.h('wa-dialog', [Vue.h('wa-button')]) }
    const app = createApp({ render: () => Vue.h('wa-button-group', [
        Vue.h('wa-button', { id: 'main' }),
        Vue.h(picker, {}, { trigger: () => Vue.h('wa-button', { id: 'arrow', slot: 'trigger' }) }),
    ]) })
    for (const name of ['ProjectEditDialog', 'WorktreeDialog']) app.component(name, dialog)
    for (const name of ['ProjectBadge', 'WorktreeButton', 'WorktreePickerRows']) {
        app.component(name, { render: () => null })
    }
    const container = { children: [] }
    app.mount(container)
    const group = container.children.find(el => el.tagName === 'WA-BUTTON-GROUP')
    const assignedElements = group.children.filter(el => el.kind === 'element' && !el.props.slot)

    // Execute the installed Web Awesome class algorithm on the rendered host elements.
    const entry = read('../../../node_modules/@awesome.me/webawesome/dist/components/button-group/button-group.js')
    const chunk = entry.match(/from "(\.\.\/\.\.\/chunks\/[^"\n]+)"/)[1]
    const nativeSource = read(`../../../node_modules/@awesome.me/webawesome/dist/components/button-group/${chunk}`)
    const method = nativeSource.slice(nativeSource.indexOf('  updateClassNames() {'), nativeSource.indexOf('  render() {'))
    const findButton = nativeSource.slice(nativeSource.indexOf('function findButton(el) {'), nativeSource.indexOf('\nexport {'))
    const nativeGroup = runInNewContext(`${findButton}; ({ ${method} })`)
    nativeGroup.orientation = 'horizontal'
    nativeGroup.defaultSlot = { assignedElements: () => assignedElements }
    nativeGroup.updateClassNames()

    const arrow = group.querySelector('wa-dropdown').querySelector('wa-button')
    const dialogs = body.children.filter(el => el.tagName === 'WA-DIALOG')
    assert.ok(arrow.classList.contains('wa-button-group__button-last'), 'dropdown arrow retains its outer group corner')
    assert.equal(arrow.classList.contains('wa-button-group__button-inner'), false)
    assert.deepEqual(assignedElements.map(el => el.tagName), ['WA-BUTTON', 'WA-DROPDOWN'])
    assert.equal(dialogs.length, 2, 'both dialogs remain mounted outside the group')
    for (const el of dialogs) {
        assert.equal(el.querySelector('wa-button').classList.contains('wa-button-group__button'), false)
    }
    app.unmount()
})
