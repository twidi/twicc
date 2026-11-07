import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { useI18n } from 'vue-i18n'
import { renderToString } from '@vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createAppI18n } from '../../i18n/index.js'
import { localUiMessage, resolveUiMessage } from '../../i18n/uiMessages.js'
import { isSnippetGroup, getGroupItems, flattenGroupedEntries } from '../../utils/snippetGroups.js'
import { extractPlaceholders } from '../../utils/snippetPlaceholders.js'

const sourceOf = file => readFileSync(new URL(`../${file}`, import.meta.url), 'utf8')
async function fixture(file, options = {}) {
    const compiled = compileScript(parse(sourceOf(file)).descriptor, { id: 'locale-review-test' }).content
    const dependencies = { ...Vue, useI18n, localUiMessage, resolveUiMessage, isSnippetGroup, getGroupItems, flattenGroupedEntries, extractPlaceholders,
        apiFetch: options.fetch ?? (async () => { throw Error('Unexpected request') }),
        useTerminalConfigStore: () => options.store ?? { combos: [], snippets: {} },
        useRoute: () => ({ query: {} }), useDataStore: () => ({}), useWorkspacesStore: () => ({}),
        usePanZoom: () => ({ reset() {} }), usePopupMotion() {},
    }
    const script = compiled.replace(/^import (.+) from ['"][^'"]+['"]\n/gm, (_, names) => {
        for (const name of names.replace(/[{}]/g, '').split(',').map(value => value.trim())) {
            if (!Object.hasOwn(dependencies, name)) dependencies[name] = () => { throw Error(`Unexpected dependency: ${name}`) }
        }
        return ''
    }).replace('export default', 'return')
    const component = new Function(...Object.keys(dependencies), script)(...Object.values(dependencies))
    let state
    const i18n = createAppI18n()
    await renderToString(Vue.createSSRApp({ setup() {
        state = component.setup(options.props ?? { apiPrefix: '/api/test' }, { expose() {}, emit() {} })
        return () => null
    } }).use(i18n))
    const text = value => resolveUiMessage(value, i18n.global)
    const assertSwitch = (message, key) => {
        assert.equal(text(message), i18n.global.t(key, message.params ?? {}))
        const english = text(message)
        i18n.global.locale.value = 'pt-BR'
        assert.equal(text(message), i18n.global.t(key, message.params ?? {}))
        assert.notEqual(text(message), english)
        i18n.global.locale.value = 'en'
    }
    return { state, i18n, text, assertSwitch }
}

for (const file of ['FileCreateDialog', 'FileRenameDialog']) {
    test(`${file} retains reactive local validation and literal server errors`, async () => {
        const f = await fixture(`files/${file}.vue`, { fetch: async () => ({ ok: false, json: async () => ({ error: 'Server literal' }) }) })
        await f.state.handleSave()
        f.assertSwitch(f.state.errorMessage.value, 'files.errors.nameEmpty')
        f.state.localName.value = 'valid.txt'
        await f.state.handleSave()
        f.i18n.global.locale.value = 'pt-BR'
        assert.equal(f.text(f.state.errorMessage.value), 'Server literal')
    })
}

for (const [file, action] of [['FileDeleteDialog', 'handleDelete'], ['FileMoveDialog', 'handleMove']]) {
    test(`${file} keeps network fallback reactive`, async () => {
        const f = await fixture(`files/${file}.vue`, { fetch: async () => { throw Error('Network detail') } })
        if (f.state.selectedDir) f.state.selectedDir.value = '/target'
        await f.state[action]()
        f.assertSwitch(f.state.errorMessage.value, 'files.errors.networkRetry')
    })
}

test('directory picker carries fallback descriptors through fetch errors and preserves native errors', async () => {
    let native = false
    const f = await fixture('files/DirectoryPickerPopup.vue', { fetch: async () => {
        if (native) throw Error('Native detail')
        return { ok: false, json: async () => ({}) }
    } })
    f.state.rootPath.value = '/parent/child'
    await f.state.navigateUp()
    f.assertSwitch(f.state.error.value, 'files.errors.loadDirectory')
    native = true
    await f.state.navigateUp()
    f.i18n.global.locale.value = 'pt-BR'
    assert.equal(f.text(f.state.error.value), 'Native detail')
})

test('combo validation and duplicate warnings stay reactive', async () => {
    const f = await fixture('terminal/TerminalCombosDialog.vue', { store: { combos: [{ steps: [{ key: 'a', modifiers: [] }] }] } })
    f.state.openAddForm()
    f.state.handleSave()
    f.assertSwitch(f.state.errorMessage.value, 'terminal.combos.errors.keyRequired')
    f.state.formData.value.steps[0].key = 'a'
    f.state.handleSave()
    f.assertSwitch(f.state.warningMessage.value, 'terminal.combos.duplicateWarning')
})

test('snippet validation and duplicate warnings stay reactive', async () => {
    const f = await fixture('terminal/TerminalSnippetsDialog.vue', { store: { snippets: { global: [{ label: 'Label', snippet: 'same' }] } } })
    f.state.openAddForm()
    f.state.handleSave()
    f.assertSwitch(f.state.errorMessage.value, 'terminal.snippets.errors.labelRequired')
    f.state.formData.value.label = 'Label'
    f.state.formData.value.snippet = 'body'
    f.state.handleSave()
    f.assertSwitch(f.state.warningMessage.value, 'terminal.snippets.duplicateWarning')
})

test('terminal rename placeholder follows the locale while custom input stays literal', async () => {
    const f = await fixture('terminal/TerminalRenameDialog.vue')
    f.state.open(2, 'Custom terminal', 'Term 3')
    assert.equal(f.state.currentDefaultLabel.value, f.i18n.global.t('terminal.tabs.numbered', { number: 3 }))
    f.i18n.global.locale.value = 'pt-BR'
    assert.equal(f.state.currentDefaultLabel.value, f.i18n.global.t('terminal.tabs.numbered', { number: 3 }))
    assert.equal(f.state.localLabel.value, 'Custom terminal')
})

test('media generated names follow the locale while user alt text stays literal', async () => {
    const props = Vue.reactive({ items: [{ type: 'image', src: '/image', nameKey: 'markdown.media.mermaid' }] })
    const f = await fixture('media/MediaPreviewDialog.vue', { props })
    const english = f.state.currentItemName.value
    f.i18n.global.locale.value = 'pt-BR'
    assert.equal(f.state.currentItemName.value, f.i18n.global.t('markdown.media.mermaid'))
    assert.equal(f.state.dialogTitle.value, f.state.currentItemName.value)
    assert.notEqual(f.state.currentItemName.value, english)
    props.items[0].name = 'User alt text'
    assert.equal(f.state.currentItemName.value, 'User alt text')
})

function extractedFunction(file, name, dependencies) {
    const source = sourceOf(file)
    const start = source.indexOf(`async function ${name}(`)
    const end = source.indexOf('\n}', start) + 2
    assert.ok(start >= 0 && end > start)
    return new Function(...Object.keys(dependencies), `${source.slice(start, end)}; return ${name}`)(...Object.values(dependencies))
}

test('file and Git request fallbacks stay reactive and preserve server prose', async () => {
    const i18n = createAppI18n()
    const error = Vue.ref(null), diffError = Vue.ref(null)
    let serverError
    const dependencies = { localUiMessage, error, diffError,
        apiFetch: async () => ({ ok: false, status: 500, json: async () => ({ error: serverError }) }),
        isBinaryMediaFile: Vue.ref(false), switching: Vue.ref(false), loading: Vue.ref(false),
        saveError: Vue.ref(null), isWritable: Vue.ref(false), isBinary: Vue.ref(false), tooLarge: Vue.ref(false),
        imageSrc: Vue.ref(null), currentContent: Vue.ref(''), resolvedApiPrefix: Vue.ref('/api'), props: {},
        diffData: Vue.ref(null), diffLoading: Vue.ref(false), diffTooLarge: Vue.ref(false),
        isViewingIndex: Vue.ref(true), appendGitDir: url => url, apiPrefix: Vue.ref('/api'),
    }
    const fetchFile = extractedFunction('files/FilePane.vue', 'fetchFileContent', dependencies)
    const fetchDiff = extractedFunction('git/GitPanel.vue', 'fetchDiff', dependencies)
    await fetchFile('/file')
    await fetchDiff('/file')
    const english = [error.value, diffError.value].map(message => resolveUiMessage(message, i18n.global))
    i18n.global.locale.value = 'pt-BR'
    assert.notEqual(resolveUiMessage(error.value, i18n.global), english[0])
    assert.notEqual(resolveUiMessage(diffError.value, i18n.global), english[1])
    serverError = 'External server detail'
    await fetchFile('/file')
    await fetchDiff('/file')
    assert.equal(error.value, serverError)
    assert.equal(diffError.value, serverError)
})

test('Markdown media items carry fallback keys while image alt text stays user content', () => {
    const source = sourceOf('ui/MarkdownContent.vue')
    const extract = name => {
        const start = source.indexOf(`function ${name}(`)
        return source.slice(start, source.indexOf('\n}', start) + 2)
    }
    const { buildImgItem, buildSvgItem } = new Function('svgToDataUrl',
        `${extract('buildImgItem')}\n${extract('buildSvgItem')}; return { buildImgItem, buildSvgItem }`)(() => 'data:image/svg+xml,diagram')
    const image = alt => ({ src: '/raw/image', closest: () => null, getAttribute: () => alt })
    assert.deepEqual(buildImgItem(image('')), {
        type: 'image', src: '/raw/image', name: '', nameKey: 'markdown.media.image', link: null,
    })
    assert.deepEqual(buildImgItem(image('User alt text')), {
        type: 'image', src: '/raw/image', name: 'User alt text', nameKey: null, link: null,
    })
    assert.deepEqual(buildSvgItem({}), {
        type: 'image', src: 'data:image/svg+xml,diagram', nameKey: 'markdown.media.mermaid', link: null,
    })
})
