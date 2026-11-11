import test from 'node:test'
import assert from 'node:assert/strict'
import * as Vue from 'vue'
import { compileComponent, makeRenderer, descendants, flush, row } from '../../../tests/helpers/scrollerComponentHarness.js'
import { loadLocaleMessages } from '../../i18n/index.js'
import { resolveUiMessage } from '../../i18n/uiMessages.js'
import { formatAttachmentSize } from '../../utils/composerAttachments.js'
import { formatFullDateTime } from '../../utils/date.js'
import { isRenderableArtifactPath } from '../../utils/artifactBookmark.js'
import { collapseLeadingSlashes } from '../../utils/uploads/paths.js'
import { takePickedFiles } from '../../utils/uploads/display.js'
import * as downloads from '../../utils/download.js'

// Keep Vue's real Teleport lifecycle, with the renderer container as its body target.
const FixtureTeleport = {
    ...Vue.Teleport,
    process(previous, next, container, ...args) {
        next.props = { ...next.props, to: container }
        Vue.Teleport.process(previous, next, container, ...args)
    },
}
const Menu = compileComponent(new URL('./FileTreeContextMenu.vue', import.meta.url), {
    vue: { ...Vue, Teleport: FixtureTeleport },
    '../../utils/composerAttachments': { formatAttachmentSize },
    '../../utils/date': { formatFullDateTime },
})

function mount(component, props = {}, locale = 'en') {
    const { renderer, root } = makeRenderer(140, node => { node.style = {} })
    const app = renderer.createApp(component, props)
    app.config.globalProperties.$i18n.locale = locale
    app.mount(root)
    return { root, app, close: () => app.unmount() }
}

function actions(root, value) {
    return descendants(root, node => node.type === 'wa-dropdown-item' && node.props.value === value)
}

function text(node) {
    return [node.text, ...node.children.map(text)].join('')
}

function select(root, value) {
    const [item] = actions(root, value)
    assert.ok(item, `Missing ${value} action`)
    const [dropdown] = descendants(root, node => node.type === 'wa-dropdown' && node.props.class === 'context-menu-dropdown')
    dropdown.props.onWaSelect({ detail: { item: { value: item.props.value } } })
}

test('real directory action is independent of write access', () => {
    for (const permissions of [
        { writable: true, writableLoading: false },
        { writable: false, writableLoading: false },
        { writable: false, writableLoading: true },
    ]) {
        const fixture = mount(Menu, { nodeType: 'directory', nodeName: 'folder', fullPath: '/repo/folder', ...permissions })
        try {
            const items = actions(fixture.root, 'download')
            assert.equal(items.length, 1)
            assert.equal(Boolean(items[0].props.disabled), false)
            assert.equal(text(items[0]).trim(), 'Download folder (.zip)')
        } finally { fixture.close() }
    }
})

test('excluded directory nodes have no ZIP action', () => {
    // FileTree emits file/directory types. Bookmark groups and root selectors have
    // separate markup and do not emit its context-menu event.
    for (const props of [
        { mode: 'git-index', nodeType: 'directory', fullPath: '/repo/folder' },
        { mode: 'git-commit', nodeType: 'directory', fullPath: '/repo/folder' },
        { nodeType: 'directory', fullPath: '/artifacts/present', createOnly: true },
        { nodeType: 'directory', fullPath: '/artifacts/missing', createOnly: true },
        { nodeType: 'directory', fullPath: '' },
        { nodeType: 'group', nodeName: '/looks/like/a/folder', fullPath: '/repo/folder' },
        { nodeType: 'root-selector', nodeName: '/repo', fullPath: '' },
        { nodeType: 'synthetic', nodeName: '/repo', fullPath: '' },
    ]) {
        const fixture = mount(Menu, props)
        try {
            assert.equal(actions(fixture.root, 'download').length, 0, JSON.stringify(props))
            assert.equal(actions(fixture.root, 'download-diff').length, 0)
        } finally { fixture.close() }
    }
})

test('existing file and Git diff menu actions remain available', () => {
    for (const [props, label, diff] of [
        [{ mode: 'files' }, 'Download', false],
        [{ mode: 'git-index', unstagedStatus: 'modified' }, 'Download file', true],
        [{ mode: 'git-index', stagedStatus: 'modified' }, 'Download file', true],
        [{ mode: 'git-index', unstagedStatus: 'deleted' }, 'Download file', false],
        [{ mode: 'git-index', unstagedStatus: 'untracked' }, 'Download file', false],
        [{ mode: 'git-commit', status: 'modified' }, 'Download file', true],
        [{ mode: 'git-commit', status: 'added' }, 'Download file', false],
        [{ mode: 'git-commit', status: 'deleted' }, 'Download file', false],
    ]) {
        const events = []
        const fixture = mount(Menu, { nodeType: 'file', fullPath: '/repo/file.txt', ...props, onDownloadDiff: () => events.push('diff') })
        try {
            assert.equal(text(actions(fixture.root, 'download')[0]).trim(), label)
            assert.equal(actions(fixture.root, 'download-diff').length, diff ? 1 : 0)
            if (diff) {
                select(fixture.root, 'download-diff')
                assert.deepEqual(events, ['diff'])
            }
        } finally { fixture.close() }
    }
})

for (const [locale, label] of [
    ['en', 'Download folder (.zip)'],
    ['fr', 'Télécharger le dossier (.zip)'],
    ['de', 'Ordner herunterladen (.zip)'],
    ['es', 'Descargar carpeta (.zip)'],
    ['pt-BR', 'Baixar pasta (.zip)'],
]) {
    test(`ZIP action translates and emits the existing download event in ${locale}`, async () => {
        await loadLocaleMessages(locale)
        const events = []
        const fixture = mount(Menu, {
            nodeType: 'directory', fullPath: '/repo/folder',
            onDownload: (...args) => events.push(['download', ...args]), onClose: () => events.push(['close']),
        }, locale)
        try {
            assert.equal(text(actions(fixture.root, 'download')[0] || { text: '', children: [] }).trim(), label)
            select(fixture.root, 'download')
            assert.deepEqual(events, [['download'], ['close']])
        } finally { fixture.close() }
    })
}

const Tree = {
    props: ['node', 'path'],
    emits: ['context-menu'],
    setup(props, { emit }) {
        return () => Vue.h('tree-fixture', { onOpenMenu: data => emit('context-menu', data) })
    },
}

function panel() {
    return compileComponent(new URL('./FileTreePanel.vue', import.meta.url), {
        '../../i18n/uiMessages.js': { resolveUiMessage },
        // Only metadata lookup is external. Downloads use the real URL and anchor code.
        '../../utils/api': { apiFetch: async () => ({ ok: true, json: async () => ({ writable: false, type: 'directory' }) }) },
        '../../utils/download': downloads,
        './FileTree.vue': Tree,
        './FileTreeContextMenu.vue': Menu,
        './FileRenameDialog.vue': row,
        './FileDeleteDialog.vue': row,
        './FileCreateDialog.vue': row,
        './FileMoveDialog.vue': row,
        '../ui/AppTooltip.vue': row,
        '../ui/GitStatusBadge.vue': row,
        '../artifacts/ArtifactBookmarkButton.vue': row,
        '../../stores/data': { useDataStore: () => ({}) },
        '../../utils/artifactBookmark': { isRenderableArtifactPath },
        '../../composables/useFocusRetry': { useFocusRetry: () => () => {} },
        '../../stores/uploads': { useUploadsStore: () => { throw new Error('Downloads must not use uploads') } },
        '../../utils/uploads/paths': { collapseLeadingSlashes },
        '../../utils/uploads/display': { takePickedFiles },
    })
}

test('panel dispatch selects file or directory download synchronously', async (context) => {
    const navigation = []
    context.mock.method(globalThis, 'fetch', () => { throw new Error('Downloads must not fetch bytes') })
    context.mock.method(URL, 'createObjectURL', () => { throw new Error('Downloads must not create blobs') })
    const previousDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
    context.after(() => {
        if (previousDocument) Object.defineProperty(globalThis, 'document', previousDocument)
        else delete globalThis.document
    })
    globalThis.document = {
        createElement(tag) {
            assert.equal(tag, 'a')
            return { click() { navigation.push(this.href) }, remove() {} }
        },
        body: { appendChild() {} },
    }
    const Panel = panel()
    for (const [props, data, expected] of [
        [{ projectId: 'p', sessionId: 's' }, { path: '/repo/folder #', type: 'directory' }, '/api/projects/p/sessions/s/directory-download/?path=%2Frepo%2Ffolder+%23'],
        [{ projectId: 'p', sessionId: 's', isDraft: true }, { path: '/repo', type: 'directory', isRoot: true }, '/api/projects/p/directory-download/?path=%2Frepo'],
        [{ projectId: 'p' }, { path: '/repo/folder', type: 'directory' }, '/api/projects/p/directory-download/?path=%2Frepo%2Ffolder'],
        [{ rootRestriction: '/artifacts/雪', rootPath: '/artifacts/雪/nested', lazyRoot: true }, { path: '/artifacts/雪/nested/child', type: 'directory' }, '/api/directory-download/?path=%2Fartifacts%2F%E9%9B%AA%2Fnested%2Fchild&root=%2Fartifacts%2F%E9%9B%AA'],
        [{ rootPath: '/standalone' }, { path: '/standalone/empty', type: 'directory' }, '/api/directory-download/?path=%2Fstandalone%2Fempty&root=%2Fstandalone'],
        [{ projectId: 'p', sessionId: 's' }, { path: '/repo/file #.txt', type: 'file' }, '/api/projects/p/sessions/s/file-raw/repo/file%20%23.txt?download=1'],
        [{ rootRestriction: '/repo' }, { path: '/repo/file.txt', type: 'file' }, '/api/file-raw/L3JlcG8/repo/file.txt?download=1'],
    ]) {
        const fixture = mount(Panel, { rootPath: '/repo', tree: { name: 'repo', type: 'directory', loaded: true, children: [] }, enableContextMenu: true, ...props })
        try {
            descendants(fixture.root, node => node.type === 'tree-fixture')[0].props.onOpenMenu({ name: 'selected', x: 1, y: 2, isRoot: false, ...data })
            await flush()
            const before = navigation.length
            select(fixture.root, 'download')
            assert.equal(navigation.length, before + 1, 'Anchor navigation stays inside the menu gesture')
            assert.equal(navigation.at(-1), expected)
        } finally { fixture.close() }
    }
    for (const contextMenuMode of ['git-index', 'git-commit']) {
        const events = []
        const fixture = mount(Panel, {
            rootPath: 'repo', gitDirectory: '/repo', contextMenuMode, mode: 'git',
            tree: { name: 'repo', type: 'directory', children: [] }, enableContextMenu: true,
            onDownload: value => events.push(['file', value]), onDownloadDiff: value => events.push(['diff', value]),
        })
        try {
            descendants(fixture.root, node => node.type === 'tree-fixture')[0].props.onOpenMenu({ path: 'repo/file.txt', type: 'file', name: 'file.txt', x: 1, y: 2, status: 'modified', unstagedStatus: 'modified' })
            await flush()
            const before = navigation.length
            select(fixture.root, 'download')
            select(fixture.root, 'download-diff')
            assert.deepEqual(events, [['file', { path: 'file.txt' }], ['diff', { path: 'file.txt' }]])
            assert.equal(navigation.length, before)
        } finally { fixture.close() }
    }
})

test('lazy Artifacts root stays create-only whether present or missing', async () => {
    const Panel = panel()
    for (const loaded of [true, false]) {
        const fixture = mount(Panel, { rootPath: '/artifacts', lazyRoot: true, tree: { name: 'artifacts', type: 'directory', loaded, children: [] }, enableContextMenu: true })
        try {
            descendants(fixture.root, node => node.type === 'tree-fixture')[0].props.onOpenMenu({ path: '/artifacts', name: 'artifacts', type: 'directory', isRoot: true, x: 0, y: 0 })
            await flush()
            assert.equal(actions(fixture.root, 'download').length, 0)
            assert.equal(actions(fixture.root, 'create-file').length, 1)
        } finally { fixture.close() }
    }
})
