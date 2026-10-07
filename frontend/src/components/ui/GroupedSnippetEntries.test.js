import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { reactive, ref, watch, nextTick } from 'vue'
import { jsonValuesEqual } from '../../utils/jsonValuesEqual.js'

const source = readFileSync(new URL('./GroupedSnippetEntries.vue', import.meta.url), 'utf8')

function fixture() {
    const watchers = []
    const stops = []
    const props = reactive({ entries: [], context: null })
    const lifecycle = {}
    const { descriptor } = parse(source)
    const script = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
    const api = runInNewContext(`(function() { ${script}\nreturn { entryKey, close, onShow, setPopover, openGroup } })`, {
        defineProps: () => props,
        defineExpose: () => {},
        isSnippetGroup: entry => entry.type === 'group',
        ref,
        jsonValuesEqual,
        useId: () => 'test',
        watch: (read, callback, options) => { watchers.push(callback); stops.push(watch(read, callback, options)) },
        onBeforeUnmount: callback => { lifecycle.unmount = callback },
        onDeactivated: callback => { lifecycle.deactivate = callback },
    })()
    const first = { open: true }
    const second = { open: false }
    api.setPopover('first', first)
    api.setPopover('second', second)
    return { api, first, second, watchers, lifecycle, props, dispose: () => stops.forEach(stop => stop()) }
}

test('opening another group closes the previous group; leaf dismissal closes every group', () => {
    const { api, first, second } = fixture()
    api.onShow('first')
    assert.equal(api.openGroup.value, 'first')
    assert.equal(first.open, true)
    second.open = true
    api.onShow('second')
    assert.equal(first.open, false)
    assert.equal(second.open, true)
    assert.equal(api.openGroup.value, 'second')
    api.close()
    assert.equal(second.open, false)
    assert.equal(api.openGroup.value, null)
})

test('context changes, entry changes, deactivation, and unmount close active groups', () => {
    const { api, first, watchers, lifecycle } = fixture()
    for (const callback of [...watchers, lifecycle.deactivate, lifecycle.unmount]) {
        first.open = true
        api.onShow('first')
        callback('["changed"]', '["initial"]')
        assert.equal(first.open, false)
        assert.equal(api.openGroup.value, null)
    }
})

test('equivalent context and entries preserve an open group', async t => {
    const f = fixture(); t.after(f.dispose)
    f.props.context = ['session', 'project', 'claude_code', null, false]
    f.props.entries = [{ type: 'group', id: 'first', label: 'Group', items: [{ label: 'Leaf', text: 'Text' }] }]
    await nextTick()
    f.first.open = true; f.api.onShow('first')
    f.props.context = [...f.props.context]
    f.props.entries = JSON.parse(JSON.stringify(f.props.entries))
    await nextTick()
    assert.equal(f.first.open, true)
    assert.equal(f.api.openGroup.value, 'first')
})

test('equivalent entries with different object key order preserve the group', async t => {
    const f = fixture(); t.after(f.dispose)
    f.props.entries = [{ type: 'group', id: 'first', label: 'Group', items: [] }]
    await nextTick()
    f.first.open = true; f.api.onShow('first')
    f.props.entries = [{ items: [], label: 'Group', id: 'first', type: 'group' }]
    await nextTick()
    assert.equal(f.first.open, true)
})

test('actual context changes close the group', async t => {
    const f = fixture(); t.after(f.dispose)
    f.props.context = ['session', 'project', 'claude_code', null, false]
    await nextTick()
    f.first.open = true; f.api.onShow('first')
    f.props.context[0] = 'other-session'
    await nextTick()
    assert.equal(f.first.open, false)
})

test('in-place entry edits and group removal close the group', async t => {
    const f = fixture(); t.after(f.dispose)
    f.props.entries = [{ type: 'group', id: 'first', label: 'Group', items: [{ label: 'Leaf', text: 'Text' }] }]
    await nextTick()
    f.first.open = true; f.api.onShow('first')
    f.props.entries[0].items[0].text = 'Changed'
    await nextTick()
    assert.equal(f.first.open, false)
    f.first.open = true; f.api.onShow('first')
    f.props.entries = []
    await nextTick()
    assert.equal(f.first.open, false)
})

test('group keys include scope and group ID, and removed popovers leave no active reference', () => {
    const { api, first } = fixture()
    const group = { type: 'group', id: 'commands', _scope: 'project:a' }
    assert.equal(api.entryKey(group, 0), 'project:a:commands')
    assert.notEqual(api.entryKey(group, 0), api.entryKey({ ...group, _scope: 'project:b' }, 0))
    api.setPopover('first', null)
    api.close()
    assert.equal(first.open, true)
})

test('group display and consumer SFCs compile their slot and template bindings', () => {
    for (const name of ['./GroupedSnippetEntries.vue', '../message/MessageSnippetsBar.vue', '../terminal/TerminalExtraKeysBar.vue']) {
        const filename = new URL(name, import.meta.url).pathname
        const { descriptor, errors } = parse(readFileSync(filename, 'utf8'), { filename })
        assert.deepEqual(errors, [])
        const script = compileScript(descriptor, { id: name })
        const template = compileTemplate({ source: descriptor.template.content, filename, id: name,
            compilerOptions: { bindingMetadata: script.bindings } })
        assert.deepEqual(template.errors, [])
    }
})
