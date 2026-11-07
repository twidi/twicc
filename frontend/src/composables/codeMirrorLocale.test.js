import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, rm, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { build } from 'vite'
import { i18n, loadLocaleMessages } from '../i18n/index.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

test('locale reconfiguration renews folded accessibility without losing folds, document, selection or history', async context => {
    const root = fileURLToPath(new URL('../../', import.meta.url))
    const directory = await mkdtemp(join(tmpdir(), 'twicc-editor-locale-'))
    context.after(() => rm(directory, { recursive: true, force: true }))
    const fixture = '\0editor-locale-fixture'
    const { output } = await build({ configFile: false, root, logLevel: 'silent',
        plugins: [{ name: 'editor-locale-fixture', resolveId: id => id === fixture ? id : null,
            load: id => id === fixture ? `
                export {useCodeMirrorExtensions} from ${JSON.stringify(join(root, 'src/composables/useCodeMirror.js'))};
                export {i18n,loadLocaleMessages} from ${JSON.stringify(join(root, 'src/i18n/index.js'))};
                export {EditorState} from '@codemirror/state';
                export {foldEffect,foldedRanges} from '@codemirror/language';
                export {undoDepth} from '@codemirror/commands';` : null }],
        build: { write: false, minify: false, rollupOptions: { input: fixture,
            external: [/\/stores\/settings$/], preserveEntrySignatures: 'strict' },
            modulePreload: { resolveDependencies: () => [] } },
    })
    await writeFile(join(directory, 'package.json'), '{"type":"module"}')
    for (const item of output) {
        const path = join(directory, item.fileName)
        await mkdir(dirname(path), { recursive: true })
        await writeFile(path, item.type === 'chunk' ? item.code : item.source)
    }
    const entry = output.find(item => item.type === 'chunk' && item.isEntry)
    const mod = await import(pathToFileURL(join(directory, entry.fileName)))
    await mod.loadLocaleMessages('pt-BR')
    const cm = mod.useCodeMirrorExtensions({})
    let state = mod.EditorState.create({ doc: 'start\nfolded content\nend', extensions: cm.extensions })
    state = state.update({ changes: { from: state.doc.length, insert: '!' } }).state
    state = state.update({ effects: mod.foldEffect.of({ from: 5, to: 20 }), selection: { anchor: 0, head: 2 } }).state
    const ranges = () => { const result = []; mod.foldedRanges(state).between(0, state.doc.length, (from, to) => result.push([from, to])); return result }
    const before = { doc: state.doc.toString(), selection: state.selection.toJSON(), history: mod.undoDepth(state), folds: ranges() }
    const oldWidget = mod.foldedRanges(state).iter().value.spec.widget
    const view = { get state() { return state }, dispatch(spec) { state = state.update(spec).state } }
    mod.i18n.global.locale.value = 'pt-BR'
    cm.reconfigure(view, 'locale')
    assert.deepEqual({ doc: state.doc.toString(), selection: state.selection.toJSON(), history: mod.undoDepth(state), folds: ranges() }, before)
    const widget = mod.foldedRanges(state).iter().value.spec.widget
    assert.notEqual(widget, oldWidget)
    const previousDocument = globalThis.document
    globalThis.document = { createElement: () => ({ attributes: {}, setAttribute(name, value) { this.attributes[name] = value } }) }
    try {
        const marker = widget.toDOM(view)
        assert.equal(marker.attributes['aria-label'], mod.i18n.global.t('session.editor.phrases.foldedCode'))
        assert.equal(marker.title, mod.i18n.global.t('session.editor.phrases.unfold'))
    } finally {
        if (previousDocument === undefined) delete globalThis.document
        else globalThis.document = previousDocument
    }
})

test('open native search and dialog fields retain values and focus across a locale update', async context => {
    context.after(() => { i18n.global.locale.value = 'en' })
    const source = await readFile(new URL('./useCodeMirror.js', import.meta.url), 'utf8')
    const start = source.indexOf('const stops = [watch(i18n.global.locale,')
    const watcher = source.slice(start, source.indexOf('\n    let cancelled', start))
    let onLocale, query = { search: 'original query', replace: 'replacement', caseSensitive: true }
    const savedQuery = query, selection = []
    const oldInput = { name: 'search', selectionStart: 2, selectionEnd: 6, selectionDirection: 'backward', closest: () => true }
    const newInput = { name: 'search', isConnected: true, focus: () => selection.push('focus'), setSelectionRange: (...args) => selection.push(args) }
    const label = { firstChild: { nodeType: 3, nodeValue: 'Go to line: ' } }, submit = {}, close = { setAttribute: (...args) => selection.push(args) }
    const completion = { setAttribute: (...args) => selection.push(args) }
    const special = { getAttribute: () => 'Control character Nul', setAttribute: (...args) => selection.push(args) }
    const line = { querySelector: selector => selector === 'label' ? label : selector === 'button[type=submit]' ? submit : close }
    const view = { state: { phrase: phrase => ({ 'Go to line': 'Ir para linha', go: 'ir', close: 'fechar', Completions: 'Conclusões', 'Control character': 'Control character' })[phrase] },
        dom: { ownerDocument: { activeElement: oldInput }, querySelector: () => line,
            querySelectorAll: selector => selector === '.cm-search input' ? [newInput] : selector.includes('listbox') ? [completion] : [special] },
        dispatch: spec => { query = spec.effects.query } }
    new Function('watch', 'i18n', 'getView', 'cmExtensions', 'searchPanelOpen', 'getSearchQuery', 'closeSearchPanel', 'openSearchPanel', 'setSearchQuery', watcher)(
        (_source, callback) => { onLocale = callback }, i18n, () => view, { reconfigure() {} }, () => true,
        () => query, () => {}, () => { query = { search: 'selected document text', replace: '' } }, { of: query => ({ query }) })
    onLocale()
    assert.equal(query, savedQuery)
    assert.deepEqual(selection.slice(-2), ['focus', [2, 6, 'backward']])
    assert.equal(label.firstChild.nodeValue, 'Ir para linha: ')
    assert.equal(submit.textContent, 'ir')
    assert.ok(selection.some(args => Array.isArray(args) && args[1] === 'fechar'))
})
