import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import { parse as parseTemplate } from '@vue/compiler-dom'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createAppI18n, loadLocaleMessages } from './index.js'
import { localUiMessage, resolveUiMessage } from './uiMessages.js'
import { remoteUiMessage, remoteUiErrors } from './remoteMessages.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

// Execute the production handlers and render their actual error callouts.
// Replacing descriptors with stored translations must break the locale-switch assertions.
function dialog(file, functions, state = {}) {
    const source = readFileSync(new URL(`../components/${file}`, import.meta.url), 'utf8')
    const { descriptor } = parse(source)
    const ast = ts.createSourceFile(file, descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true)
    const declarations = ast.statements.filter(node => functions.includes(node.name?.text))
    assert.equal(declarations.length, functions.length)
    const composer = createAppI18n('en').global
    const scope = {
        t: composer.t, localUiMessage, remoteUiMessage, remoteUiErrors, ...state,
        resolveUiMessage: message => resolveUiMessage(message, composer),
    }
    const handlers = runInNewContext(`${declarations.map(node => node.getText(ast)).join('\n')}; ({ ${functions.join(', ')} })`, scope)
    async function render(field) {
        let callout
        function visit(node) {
            if (!callout && node.tag === 'wa-callout' && node.children.some(child =>
                child.type === 5 && new RegExp(`\\b${field}\\b`).test(child.content.content))) callout = node
            for (const child of node.children ?? []) visit(child)
        }
        visit(parseTemplate(descriptor.template.content))
        assert.ok(callout, `${file}: ${field} callout`)
        const compiled = compileTemplate({
            source: callout.loc.source, filename: file, id: 'dialog-error-test',
            compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') },
        })
        assert.deepEqual(compiled.errors, [])
        const render = runInNewContext(`(function() { ${compiled.code} })()`, { Vue })
        const app = Vue.createSSRApp({ render, setup: () => ({ ...state, resolveUiMessage: scope.resolveUiMessage }) })
        return renderToString(app)
    }
    const escaped = text => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;')
    async function followsLocale(field, key, params = {}) {
        assert.ok((await render(field)).includes(escaped(composer.t(key, params))))
        composer.locale.value = 'pt-BR'
        assert.ok((await render(field)).includes(escaped(composer.t(key, params))), `${file}: stored ${field} follows locale`)
    }
    return { ...handlers, composer, render, followsLocale }
}

const field = value => Vue.ref(value)

test('session rename validation changes language while the error remains visible', async () => {
    const state = { props: { session: {} }, localTitle: field('  '), errorMessage: field('') }
    const view = dialog('session/detail/SessionRenameDialog.vue', ['handleSave'], state)
    await view.handleSave()
    await view.followsLocale('errorMessage', 'session.rename.errors.empty')
})

test('layout rename errors retain the user name and follow locale changes', async () => {
    const state = {
        l: { id: 'current' }, editingId: field('current'), editingName: field(' My layout '),
        namedLayouts: field([{ id: 'other', name: 'My layout' }]), editError: field(''),
    }
    const view = dialog('session/layout/LayoutManagerDialog.vue', ['commitRename'], state)
    view.commitRename()
    await view.followsLocale('editError', 'session.layout.errors.duplicateName', { name: 'My layout' })
})

test('layout save validation follows locale changes', async () => {
    const state = { isSaving: field(false), savedId: field(null), isNew: field(true), target: field('__new__'), newName: field(''), errorMessage: field('') }
    const view = dialog('session/layout/LayoutSaveDialog.vue', ['handleSave'], state)
    await view.handleSave()
    await view.followsLocale('errorMessage', 'session.layout.errors.emptyName')
})

test('share validation follows locale changes', async () => {
    const state = { error: field(''), sharingEnabled: field(false) }
    const view = dialog('share/ShareDialog.vue', ['handleSave'], state)
    await view.handleSave()
    await view.followsLocale('error', 'sharing.edit.hostRequired')
})

test('artifact bookmark validation follows locale changes', async () => {
    const state = { isSaving: field(false), localName: field(''), errorMessage: field('') }
    const view = dialog('artifacts/ArtifactBookmarkDialog.vue', ['handleSave'], state)
    await view.handleSave()
    await view.followsLocale('errorMessage', 'artifacts.bookmarks.errors.nameRequired')
})

test('peer compose validation follows locale changes', async () => {
    const state = { error: field(''), title: field('subject'), text: field('text'), selectedPeerId: field('') }
    const view = dialog('peer/PeerComposeDialog.vue', ['handleSend'], state)
    await view.handleSend()
    await view.followsLocale('error', 'peer.compose.errors.peerRequired')
})

test('peer manager validation follows locale changes', async () => {
    const state = { addError: field(''), addName: field(''), addUrl: field('') }
    const view = dialog('peer/PeersManagerDialog.vue', ['addPeer'], state)
    await view.addPeer()
    await view.followsLocale('addError', 'peer.manager.errors.nameAddressRequired')
})

test('peer review delivery errors retain the peer name and follow locale changes', async () => {
    const state = { actionError: field(''), peerName: field('Remote peer'), mode: field('new'), existingPickerMounted: field(false) }
    const view = dialog('peer/PeerMessageReviewDialog.vue', ['setDeliveryFailure'], state)
    view.setDeliveryFailure()
    await view.followsLocale('actionError', 'peer.review.errors.delivery', { peer: 'Remote peer' })
})

for (const file of ['peer/PeerComposeDialog.vue', 'peer/PeersManagerDialog.vue', 'peer/PeerMessageReviewDialog.vue']) {
    test(`${file} translates request fallback while preserving backend errors`, () => {
        const view = dialog(file, ['errorText'])
        const message = view.errorText(null)
        assert.equal(resolveUiMessage(message, view.composer), view.composer.t('peer.compose.errors.request'))
        view.composer.locale.value = 'pt-BR'
        assert.equal(resolveUiMessage(message, view.composer), view.composer.t('peer.compose.errors.request'))
        assert.equal(view.errorText({ error: 'Backend prose' }), 'Backend prose')
        assert.equal(resolveUiMessage(view.errorText({ errors: [{ message: 'Backend message' }, { code: 'provider_code' }] }), view.composer), 'Backend message — provider_code')
    })
}

test('MCP clipboard errors retain a descriptor for the public store getter', async () => {
    const state = { store: { error: '' }, endpoint: field('https://mcp.example/mcp'), navigator: { clipboard: { writeText: async () => { throw new Error('Denied') } } } }
    const view = dialog('mcp/McpSettings.vue', ['copyEndpoint'], state)
    await view.copyEndpoint()
    assert.equal(resolveUiMessage(state.store.error, view.composer), view.composer.t('mcp.settings.copyError'))
    view.composer.locale.value = 'pt-BR'
    assert.equal(resolveUiMessage(state.store.error, view.composer), view.composer.t('mcp.settings.copyError'))
})

test('peer compose request errors retain backend text after a locale change', async () => {
    const state = {
        error: field(''), title: field('subject'), text: field('text'), selectedPeerId: field('peer'), busy: field(false),
        props: {}, selectedProjectId: field(''), isReply: field(false), canResolveAnswered: field(false),
        apiFetch: async () => ({ ok: false, json: async () => ({ error: 'External backend prose' }) }),
    }
    const view = dialog('peer/PeerComposeDialog.vue', ['handleSend', 'errorText'], state)
    await view.handleSend()
    assert.match(await view.render('error'), /External backend prose/)
    view.composer.locale.value = 'pt-BR'
    assert.match(await view.render('error'), /External backend prose/)
})

test('share host promotion errors retain the host and follow locale changes', async () => {
    const state = {
        error: field(''), sharingEnabled: field(true), props: { bookmarkId: 1 },
        promotableHosts: field([['api.example', { kind: 'public' }]]), promote: { 'api.example': true },
        apiFetch: async () => ({ ok: false, json: async () => { throw new Error('Invalid JSON') } }),
    }
    const view = dialog('share/ShareDialog.vue', ['handleSave'], state)
    await view.handleSave()
    await view.followsLocale('error', 'sharing.edit.allowHostFailed', { host: 'api.example' })
})

test('artifact host errors retain the HTTP status and follow locale changes', async () => {
    const state = {
        sectionError: field(''), existingId: field(1),
        apiFetch: async () => ({ ok: false, status: 503, json: async () => ({}) }),
    }
    const view = dialog('artifacts/ArtifactBookmarkDialog.vue', ['postHost'], state)
    await assert.rejects(view.postHost('allowed-hosts', 'POST', {}))
    await view.followsLocale('sectionError', 'artifacts.network.errors.request', { status: 503 })
})

test('peer review timeout and network errors follow locale changes', async () => {
    const state = { actionError: field(''), PEER_RESOLUTION_TIMEOUT_MESSAGE: 'peer.review.errors.timeout' }
    const view = dialog('peer/PeerMessageReviewDialog.vue', ['PeerResolutionTimeoutError', 'setActionFailure'], state)
    view.setActionFailure(new view.PeerResolutionTimeoutError())
    view.composer.locale.value = 'pt-BR'
    assert.equal(resolveUiMessage(state.actionError.value, view.composer), view.composer.t('peer.review.errors.timeout'))
    view.setActionFailure(new Error('Network detail'))
    view.composer.locale.value = 'en'
    assert.equal(resolveUiMessage(state.actionError.value, view.composer), view.composer.t('peer.review.errors.network'))
})

for (const [handler, method, key] of [
    ['handleSave', 'updateArtifactBookmark', 'artifacts.bookmarks.errors.save'],
    ['handleRemove', 'deleteArtifactBookmark', 'artifacts.bookmarks.errors.remove'],
]) {
    test(`artifact ${handler} preserves the store local error descriptor`, async () => {
        const error = Object.assign(new Error('Stored English fallback'), { uiMessage: localUiMessage(key) })
        const state = {
            isSaving: field(false), isEditMode: field(true), existingId: field(1), localName: field('Bookmark'),
            localScope: field('project'), errorMessage: field(''), store: { [method]: async () => { throw error } },
        }
        const view = dialog('artifacts/ArtifactBookmarkDialog.vue', [handler], state)
        await view[handler]()
        await view.followsLocale('errorMessage', key)
    })
}
