import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { useMessageSnippetsStore } from './messageSnippets.js'
import { useTerminalConfigStore } from './terminalConfig.js'

for (const [name, makeStore] of [['messages', useMessageSnippetsStore], ['terminal', useTerminalConfigStore]]) {
    test(`${name}: grouped mutations send full config and retain children when a group is deleted`, () => {
        setActivePinia(createPinia())
        const store = makeStore()
        store.applyConfig({ snippets: { global: [{ label: 'Legacy', text: 'legacy' }] } })
        const sent = []
        store._sendConfig = () => sent.push(JSON.parse(JSON.stringify(store.snippets)))
        store.addSnippetGroup('global', 'Tools')
        const id = store.snippets.global[1].id
        store.addSnippet('global', { label: 'Child', text: 'child' }, id)
        store.moveSnippet('global', { groupId: id, index: 0 }, { index: 0 })
        assert.deepEqual(store.snippets.global.map((entry) => entry.label), ['Child', 'Legacy', 'Tools'])
        store.moveSnippet('global', { index: 1 }, { groupId: id, index: 0 })
        store.renameSnippetGroup('global', id, 'Renamed')
        store.deleteSnippetGroup('global', id)
        assert.deepEqual(sent.at(-1), { global: [{ label: 'Child', text: 'child' }, { label: 'Legacy', text: 'legacy' }] })
        assert.equal(sent.length, 6)
    })

    test(`${name}: an edit can change group or scope without losing the source on invalid targets`, () => {
        setActivePinia(createPinia())
        const store = makeStore()
        const original = { label: 'Existing', text: 'keep' }
        store.applyConfig({ snippets: { global: [original], 'project:p': [{ type: 'group', id: 'target', label: 'Target', items: [] }] } })
        let sent = 0
        store._sendConfig = () => { sent++ }
        store.updateSnippet('global', 0, { label: 'Lost' }, 'project:p', null, 'missing')
        assert.deepEqual(store.snippets.global, [original])
        assert.equal(sent, 0)
        store.updateSnippet('global', 0, { label: 'Changed', text: 'new' }, 'project:p', null, 'target')
        assert.deepEqual(store.snippets.global, [])
        assert.deepEqual(store.snippets['project:p'][0].items, [{ label: 'Changed', text: 'new' }])
        assert.equal(sent, 1)
    })
}

test('terminal combos can enter and leave groups and keep step data', () => {
    setActivePinia(createPinia())
    const store = useTerminalConfigStore()
    const combo = { label: 'Save', steps: [{ key: 's', modifiers: ['ctrl'] }] }
    store.applyConfig({ combos: [combo] })
    let sent = 0
    store._sendConfig = () => { sent++ }
    store.addComboGroup('Editing')
    const id = store.combos[1].id
    store.moveCombo({ index: 0 }, { groupId: id, index: 0 })
    store.updateCombo(0, { ...combo, label: 'Save file' }, id, null)
    store.deleteComboGroup(id)
    assert.deepEqual(store.combos, [{ label: 'Save file', steps: [{ key: 's', modifiers: ['ctrl'] }] }])
    assert.equal(sent, 4)
})
