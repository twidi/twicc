import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'

const source = readFileSync(new URL('./SessionListItem.vue', import.meta.url), 'utf8')

test('row namespaces every tooltip target and preserves default IDs', () => {
    const body = source.match(/function rowId\(name\) \{([\s\S]*?)\n\}/)[1]
    const rowId = prefix => runInNewContext(`(name => {${body}})`, { props: { idPrefix: prefix, session: { id: 'same-session' } } })
    assert.equal(rowId('')('session-button'), 'session-button-same-session')
    assert.equal(rowId('rail-preview-')('session-button'), 'rail-preview-session-button-same-session')
    const targets = [...source.matchAll(/:for="(rowId\('[^']+'\))"/g)].map(match => match[1])
    const ids = [...source.matchAll(/:id="(rowId\('[^']+'\))"/g)].map(match => match[1])
    assert.ok(targets.length > 10)
    for (const target of targets) assert.ok(ids.includes(target), target)
    assert.doesNotMatch(source, /:id="`|:for="`/)
    assert.match(source, /<wa-dropdown\s+v-if="showMenu"/)
    assert.match(source, /<AppTooltip v-if="showMenu"[^>]*session-menu-trigger/)
    assert.match(source, /inject\('openRenameDialog', null\)/)
})

test('preview interaction isolation keeps defaults and blocks selection and drag paths', () => {
    assert.match(source, /showMenu: \{ type: Boolean, default: true \}/)
    assert.match(source, /selectionEnabled: \{ type: Boolean, default: true \}/)
    assert.match(source, /props.selectionEnabled && selectionStore.active && modifier/)
    assert.match(source, /props.selectionEnabled && event.shiftKey && route.params.sessionId/)
    assert.match(source, /props.selectionEnabled && selectionStore.active && selectionStore.selectedIds/)
    for (const event of ['dragenter', 'dragleave', 'dragover', 'drop']) {
        assert.match(source, new RegExp(`@${event}="selectionEnabled && `))
    }
})

test('reused session row compiles all SFC sections', () => {
    const filename = 'SessionListItem.vue'
    const id = 'data-v-row-test'
    const { descriptor, errors } = parse(source, { filename })
    assert.deepEqual(errors, [])
    const script = compileScript(descriptor, { id })
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename, id,
        compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
    for (const style of descriptor.styles) {
        assert.deepEqual(compileStyle({ source: style.content, filename, id, scoped: style.scoped }).errors, [])
    }
})


test('preview modifier clicks never enter multi-selection or emit drag selection', () => {
    const { descriptor } = parse(source, { filename: 'SessionListItem.vue' })
    const compiled = compileScript(descriptor, { id: 'row-interaction-test' }).content
        .replace(/import[\s\S]*?from ['"][^'"]+['"]\s*;?/g, '')
        .replace('export default', 'const component =')
    let entered = 0
    const events = []
    const selectionStore = { active: true, selectedIds: new Set(['same-session']), enter() { entered++ } }
    let dragOptions
    const context = {
        computed: getter => ({ get value() { return getter() } }),
        watch() {}, inject: () => null,
        useRoute: () => ({ params: { sessionId: 'open-session' }, query: {} }),
        useRouter: () => ({ resolve: () => ({ href: '/session' }) }),
        useDataStore: () => ({}), useSettingsStore: () => ({}), useCodeCommentsStore: () => ({}),
        useSessionSelectionStore: () => selectionStore,
        useDragHover: options => { dragOptions = options; return { cancel() {} } },
    }
    for (const match of source.matchAll(/import ([\s\S]*?) from ['"][^'"]+['"]/g)) {
        for (const name of match[1].replace(/[{}]/g, '').split(',').map(value => value.trim()).filter(Boolean)) {
            if (!(name in context)) context[name] = () => {}
        }
    }
    const component = runInNewContext(`${compiled}; component`, context)
    const row = component.setup({ session: { id: 'same-session' }, idPrefix: 'rail-',
        showMenu: false, selectionEnabled: false, active: false }, { expose() {}, emit: (...args) => events.push(args) })
    let prevented = 0
    row.handleClick({ button: 0, shiftKey: true, preventDefault() { prevented++ } })
    assert.equal(entered, 0)
    assert.equal(prevented, 0)
    assert.deepEqual(events, [])
    assert.equal(row.selected.value, false)
    assert.equal(dragOptions.shouldActivate(), false)
    row.handleClick({ button: 0, preventDefault() { prevented++ } })
    assert.equal(prevented, 1)
    assert.equal(events[0][0], 'select')
    assert.equal(events[0][1].id, 'same-session')
    const sidebarRow = component.setup({ session: { id: 'same-session' }, idPrefix: '',
        showMenu: true, selectionEnabled: true, active: false }, { expose() {}, emit: (...args) => events.push(args) })
    sidebarRow.handleClick({ button: 0, shiftKey: true, preventDefault() { prevented++ } })
    assert.equal(events[1][0], 'selection-click')
    assert.equal(sidebarRow.selected.value, true)
    assert.equal(dragOptions.shouldActivate(), true)
})


test('active preview keeps unread suppression without active row styling', () => {
    const { descriptor } = parse(source, { filename: 'SessionListItem.vue' })
    const compiled = compileScript(descriptor, { id: 'row-active-style-test' }).content
        .replace(/import[\s\S]*?from ['"][^'"]+['"]\s*;?/g, '')
        .replace('export default', 'const component =')
    const context = {
        computed: getter => ({ get value() { return getter() } }),
        watch() {}, inject: () => null,
        useRoute: () => ({ params: {}, query: {} }), useRouter: () => ({}),
        useDataStore: () => ({ getProcessState: () => null }),
        useSettingsStore: () => ({}), useCodeCommentsStore: () => ({}),
        useSessionSelectionStore: () => ({}), useDragHover: () => ({ cancel() {} }),
        isSessionUnread: () => true,
    }
    for (const match of source.matchAll(/import ([\s\S]*?) from ['"][^'"]+['"]/g)) {
        for (const name of match[1].replace(/[{}]/g, '').split(',').map(value => value.trim()).filter(Boolean)) {
            if (!(name in context)) context[name] = () => {}
        }
    }
    const component = runInNewContext(`${compiled}; component`, context)
    const props = { session: { id: 'same-session' }, active: true, highlightActive: false,
        showMenu: false, highlighted: false, compactView: false, selectionEnabled: false }
    const row = component.setup(props, { expose() {}, emit() {} })
    assert.equal(row.showActiveStyle?.value, false)
    assert.equal(row.hasUnread.value, false)
    const element = descriptor.template.ast.children.find(node => node.type === 1)
    const button = element.children.find(node => node.type === 1 && node.tag === 'wa-button')
    const evaluate = (node, name) => {
        const binding = node.props.find(prop => prop.type === 7 && prop.arg?.content === name)
        return runInNewContext(`(${binding.exp.content})`, {
            ...props, showActiveStyle: row.showActiveStyle.value, selected: false, isDragPending: false,
        })
    }
    const wrapperClasses = evaluate(element, 'class')
    assert.equal(wrapperClasses['session-item-wrapper--active'], false)
    assert.equal(wrapperClasses['sidebar-row-wrapper--active'], false)
    const buttonClasses = evaluate(button, 'class')
    assert.equal(buttonClasses['session-item--active'], false)
    assert.equal(buttonClasses['sidebar-row--active'], false)
    assert.equal(evaluate(button, 'appearance'), 'plain')
    assert.equal(evaluate(button, 'variant'), 'neutral')
    assert.equal(component.props.highlightActive.default, true)
    props.highlightActive = component.props.highlightActive.default
    assert.equal(row.showActiveStyle.value, true)
    assert.equal(evaluate(button, 'appearance'), 'outlined')
    assert.equal(evaluate(button, 'variant'), 'brand')
    props.active = false
    assert.equal(row.showActiveStyle.value, false)
    assert.equal(row.hasUnread.value, true)
})
