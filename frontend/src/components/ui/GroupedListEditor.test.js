import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'

const filename = new URL('./GroupedListEditor.vue', import.meta.url).pathname
const source = readFileSync(filename, 'utf8')

function fixture() {
    const { descriptor } = parse(source)
    let onHoverGroup
    const setup = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
    const api = runInNewContext(`(function(){ ${setup}; return {expandedGroups, toggleGroup, drag} })()`, {
        defineProps: () => ({ entries: [], itemName: 'snippet' }),
        defineEmits: () => () => {},
        ref: value => ({ value }),
        startGroupedReorder: (...args) => { onHoverGroup = args[5]; return () => {} },
    })
    api.drag({}, {}, () => {}, () => {})
    return { api, hover: id => onHoverGroup(id) }
}

test('groups start collapsed and toggle independently without changing membership', () => {
    const { api } = fixture()
    assert.equal(api.expandedGroups.value.size, 0)
    api.toggleGroup('first')
    api.toggleGroup('second')
    assert.equal(api.expandedGroups.value.has('first'), true)
    assert.equal(api.expandedGroups.value.has('second'), true)
    api.toggleGroup('first')
    assert.equal(api.expandedGroups.value.has('first'), false)
    assert.equal(api.expandedGroups.value.has('second'), true)
})

test('drag hover expands a collapsed group and does not toggle it closed on repeated frames', () => {
    const { api, hover } = fixture()
    hover('first')
    hover('first')
    assert.equal(api.expandedGroups.value.has('first'), true)
    assert.equal(api.expandedGroups.value.size, 1)
})

test('group editor compiles its toggle, item counts, tools, and hidden child lists', () => {
    const { descriptor, errors } = parse(source, { filename })
    assert.deepEqual(errors, [])
    const script = compileScript(descriptor, { id: 'group-editor' })
    const template = compileTemplate({ source: descriptor.template.content, filename, id: 'group-editor',
        compilerOptions: { bindingMetadata: script.bindings } })
    assert.deepEqual(template.errors, [])
    for (const style of descriptor.styles) {
        assert.deepEqual(compileStyle({ source: style.content, filename, id: 'group-editor', scoped: true }).errors, [])
    }
})
