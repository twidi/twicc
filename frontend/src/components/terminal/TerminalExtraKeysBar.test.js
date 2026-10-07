import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse } from '@vue/compiler-sfc'

const source = readFileSync(new URL('./TerminalExtraKeysBar.vue', import.meta.url), 'utf8')

function fixture() {
    const emitted = []
    let closures = 0
    const { descriptor } = parse(source)
    const setup = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
    const api = runInNewContext(`(function() { ${setup}\nreturn {
        handleComboPointerDown, handleComboClick, handleSnippetPointerDown, handleSnippetClick,
        groupedCombos, groupedSnippets,
    } })`, {
        defineProps: () => ({ isTouchDevice: true }),
        defineEmits: () => (...args) => emitted.push(args),
        ref: value => ({ value }),
        computed: callback => ({ get value() { return callback() } }),
        watch: () => {},
        useDataStore: () => ({}),
        useWorkspacesStore: () => ({}),
    })()
    api.groupedCombos.value = { close: () => closures++ }
    api.groupedSnippets.value = { close: () => closures++ }
    return { api, emitted, get closures() { return closures } }
}

function click(detail) {
    return { detail, pointerType: 'mouse', button: 0, isPrimary: true, prevented: false, preventDefault() { this.prevented = true } }
}

test('terminal combo keyboard activation applies once and closes groups', () => {
    const f = fixture()
    const combo = { steps: [{ key: 'Escape' }] }
    const event = click(0)
    f.api.handleComboClick(event, combo)
    assert.equal(event.prevented, true)
    assert.deepEqual(f.emitted, [['combo-press', combo]])
    assert.equal(f.closures, 2)
})

test('terminal pointer activation ignores its following click', () => {
    for (const [pointer, followup, eventName] of [
        ['handleComboPointerDown', 'handleComboClick', 'combo-press'],
        ['handleSnippetPointerDown', 'handleSnippetClick', 'snippet-press'],
    ]) {
        const f = fixture()
        const item = {}
        f.api[pointer](click(1), item)
        const event = click(1)
        f.api[followup](event, item)
        assert.equal(event.prevented, false)
        assert.deepEqual(f.emitted, [[eventName, item]])
        assert.equal(f.closures, 2)
    }
})

test('terminal snippet keyboard activation retains disabled explanations', () => {
    for (const disabled of [false, true]) {
        const f = fixture()
        const snippet = { _disabled: disabled, _disabledReason: 'Not available: Session ID' }
        const event = click(0)
        f.api.handleSnippetClick(event, snippet)
        assert.equal(event.prevented, true)
        assert.deepEqual(f.emitted, [[disabled ? 'snippet-disabled-press' : 'snippet-press', snippet]])
        assert.equal(f.closures, 2)
    }
})
