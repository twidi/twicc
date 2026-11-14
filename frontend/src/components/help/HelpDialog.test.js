import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import { createRenderer, nextTick, reactive } from 'vue'
import { i18n, setLocale, loadLocaleMessages } from '../../i18n/index.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

// Mount the real SFC. Only browser Markdown rendering and the backend store are
// substituted; the locale plugin, loader, watcher and component lifecycle are real.
const stubs = {
    '../../stores/help': 'export function useHelpStore() { return globalThis.__helpDialogTestStore }',
    '../../utils/markdown': 'export async function renderMarkdown(body) { return `<p>${body}</p>` }',
    '../../utils/contentAssets': 'export function rewriteAssetUrls(html) { return html }',
    '../../utils/helpLinks': 'export function handleHelpLinkClick() {}',
}
const sourceUrl = new URL('./HelpDialog.vue', import.meta.url)
const { descriptor } = parse(readFileSync(sourceUrl, 'utf8'), {
    templateParseOptions: { isCustomElement: tag => tag.startsWith('wa-') },
})
const compiled = compileScript(descriptor, {
    id: 'help-dialog-test', inlineTemplate: true,
    templateOptions: { compilerOptions: { isCustomElement: tag => tag.startsWith('wa-') } },
}).content.replace(/from\s+(['"])([^'"]+)\1/g, (_, quote, specifier) => {
    const target = stubs[specifier]
        ? `data:text/javascript,${encodeURIComponent(stubs[specifier])}`
        : specifier.startsWith('.') ? new URL(specifier, sourceUrl).href : import.meta.resolve(specifier)
    return `from ${JSON.stringify(target)}`
})
const { default: HelpDialog } = await import(`data:text/javascript,${encodeURIComponent(compiled)}`)

function element(type, text = '') { return { type, text, children: [], parent: null } }
const renderer = createRenderer({
    createElement: element,
    createText: text => element('#text', text),
    createComment: text => element('#comment', text),
    setText(node, text) { node.text = text },
    setElementText(node, text) { node.text = text; node.children = [] },
    patchProp(node, key, _, value) { node[key] = value },
    insert(node, parent, anchor) {
        node.parent = parent
        const index = anchor ? parent.children.indexOf(anchor) : -1
        if (index < 0) parent.children.push(node)
        else parent.children.splice(index, 0, node)
    },
    remove(node) { node.parent?.children.splice(node.parent.children.indexOf(node), 1) },
    parentNode: node => node.parent,
    nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1],
})

function find(node, predicate) {
    if (predicate(node)) return node
    for (const child of node.children) {
        const found = find(child, predicate)
        if (found) return found
    }
}

async function settle() {
    // Fetch Response.text() uses an event-loop task, not only Vue microtasks.
    await new Promise(resolve => setImmediate(resolve))
    await nextTick()
}

test('opens retained help after mount, switches its body live, and preserves dismissal state', async () => {
    const originalFetch = globalThis.fetch
    setLocale('en', null)
    let seenWrites = 0
    const store = reactive({
        currentHelpKey: 'projects', currentHelpShowSwitch: true,
        manifest: { projects: { title: 'Projects' } },
        markSeen() { seenWrites++ }, unmarkSeen() { seenWrites++ },
        close() { this.currentHelpKey = null },
    })
    globalThis.__helpDialogTestStore = store
    globalThis.fetch = async url => {
        if (url === '/help/projects.md') return new Response('English projects.')
        if (url === '/help/pt-BR/projects.md') return new Response('Projetos em português.')
        throw new Error(`Unexpected content URL: ${url}`)
    }
    const root = element('root')
    const app = renderer.createApp(HelpDialog)
    app.use(i18n)
    try {
        app.mount(root)
        await settle()
        const dialog = find(root, node => node.type === 'wa-dialog')
        assert.equal(dialog.open, true, 'retained help must open after its dialog ref mounts')
        assert.equal(find(root, node => node.class === 'help-body').innerHTML, '<p>English projects.</p>')
        const toggle = find(root, node => node.type === 'wa-switch')
        toggle.onChange({ target: { checked: false } })
        await nextTick()
        setLocale('pt-BR', null)
        await settle()
        assert.equal(find(root, node => node.class === 'help-body').innerHTML, '<p>Projetos em português.</p>')
        assert.equal(find(root, node => node.class === 'help-body').lang, 'pt-BR')
        assert.equal(toggle.checked, false, 'locale change must not reset the dismissal switch')
        assert.equal(dialog.open, true)
        assert.equal(seenWrites, 0, 'locale change must not mark content seen')
        setLocale('en', null)
        await settle()
        assert.equal(find(root, node => node.class === 'help-body').innerHTML, '<p>English projects.</p>')
    } finally {
        app.unmount()
        setLocale('en', null)
        globalThis.fetch = originalFetch
        delete globalThis.__helpDialogTestStore
    }
})

test('a click in the help never reaches the document, so the popover it was opened from stays open', () => {
    const source = readFileSync(sourceUrl, 'utf8')
    // `.stop` on the root wa-dialog: Close, backdrop and links are all inside it.
    assert.match(source, /<wa-dialog\s+ref="dialogRef"[^>]*@click\.stop[^>]*>/)
})

test('the Settings help list opens the help without closing the Settings popover', () => {
    const list = readFileSync(new URL('../settings/HelpSettings.vue', import.meta.url), 'utf8')
    assert.doesNotMatch(list, /close-settings-popover/)
    assert.match(list, /function onClickHelp\(key\) \{[^}]*showHelp\(key, \{ showDontShowAgain: false \}\)/)
})
