import test from 'node:test'
import assert from 'node:assert/strict'
import {
    createSnippetGroup, getGroupItems, flattenGroupedEntries, mapGroupedEntries,
    moveGroupedEntry, removeSnippetGroup, updateGroupedEntry,
} from './snippetGroups.js'

const fixture = () => [
    { label: 'A', text: 'one' },
    { type: 'group', id: 'first', label: 'First', items: [{ label: 'B', text: 'two' }] },
    { type: 'group', id: 'empty', label: 'Empty', items: [] },
    { label: 'C', text: 'three' },
]

test('new groups have distinct IDs and can remain empty', () => {
    const first = createSnippetGroup(' First ')
    const second = createSnippetGroup('First')
    assert.equal(first.label, 'First')
    assert.equal(first.type, 'group')
    assert.deepEqual(first.items, [])
    assert.notEqual(first.id, second.id)
})

test('legacy entries and grouped children are mapped with their scope and placeholder state', () => {
    const entries = fixture()
    const mapped = mapGroupedEntries(entries, (entry) => ({ ...entry, _scope: 'project:p', _disabled: entry.text === 'two' }))
    assert.equal(mapped[0]._scope, 'project:p')
    assert.equal(mapped[1]._scope, 'project:p')
    assert.equal(mapped[1].items[0]._scope, 'project:p')
    assert.equal(mapped[1].items[0]._disabled, true)
    assert.deepEqual(flattenGroupedEntries(mapped).map((entry) => entry.label), ['A', 'B', 'C'])
    assert.deepEqual(entries, fixture())
})

test('a leaf moves into an empty group and back to the root at a chosen position', () => {
    const entries = fixture()
    assert.equal(moveGroupedEntry(entries, { groupId: null, index: 0 }, { groupId: 'empty', index: 0 }), true)
    assert.deepEqual(getGroupItems(entries, 'empty'), [{ label: 'A', text: 'one' }])
    assert.equal(moveGroupedEntry(entries, { groupId: 'empty', index: 0 }, { groupId: null, index: 1 }), true)
    assert.deepEqual(entries.map((entry) => entry.label), ['First', 'A', 'Empty', 'C'])
    assert.deepEqual(getGroupItems(entries, 'empty'), [])
})

test('children move between groups without deleting the empty source group', () => {
    const entries = fixture()
    assert.equal(moveGroupedEntry(entries, { groupId: 'first', index: 0 }, { groupId: 'empty', index: 0 }), true)
    assert.deepEqual(getGroupItems(entries, 'first'), [])
    assert.deepEqual(getGroupItems(entries, 'empty'), [{ label: 'B', text: 'two' }])
})

test('same-list insertion coordinates work in both directions', () => {
    const entries = [{ label: 'A' }, { label: 'B' }, { label: 'C' }]
    assert.equal(moveGroupedEntry(entries, { index: 0 }, { index: 3 }), true)
    assert.deepEqual(entries.map((entry) => entry.label), ['B', 'C', 'A'])
    assert.equal(moveGroupedEntry(entries, { index: 2 }, { index: 0 }), true)
    assert.deepEqual(entries.map((entry) => entry.label), ['A', 'B', 'C'])
})

test('groups reorder alongside leaves but cannot become nested', () => {
    const entries = fixture()
    assert.equal(moveGroupedEntry(entries, { index: 1 }, { index: 4 }), true)
    assert.deepEqual(entries.map((entry) => entry.label), ['A', 'Empty', 'C', 'First'])
    const original = structuredClone(entries)
    assert.equal(moveGroupedEntry(entries, { index: 3 }, { groupId: 'empty', index: 0 }), false)
    assert.equal(moveGroupedEntry(entries, { index: 3 }, { groupId: 'first', index: 0 }), false)
    assert.deepEqual(entries, original)
})

test('missing groups and invalid insertion coordinates never remove an item', () => {
    for (const target of [{ groupId: 'missing', index: 0 }, { index: 99 }, { index: -1 }, { index: 0.5 }]) {
        const entries = fixture()
        assert.equal(moveGroupedEntry(entries, { index: 0 }, target), false)
        assert.deepEqual(entries, fixture())
    }
})

test('deleting a group preserves its items and position in the root list', () => {
    const entries = fixture()
    assert.equal(removeSnippetGroup(entries, 'first'), true)
    assert.deepEqual(entries.map((entry) => entry.label), ['A', 'B', 'Empty', 'C'])
    assert.equal(removeSnippetGroup(entries, 'empty'), true)
    assert.deepEqual(entries.map((entry) => entry.label), ['A', 'B', 'C'])
})

test('editing can change membership without losing other entries', () => {
    const entries = fixture()
    assert.equal(updateGroupedEntry(entries, { groupId: 'first', index: 0 }, { label: 'Updated' }, 'empty'), true)
    assert.deepEqual(getGroupItems(entries, 'first'), [])
    assert.deepEqual(getGroupItems(entries, 'empty'), [{ label: 'Updated' }])
    assert.equal(updateGroupedEntry(entries, { groupId: 'empty', index: 0 }, { label: 'Lost' }, 'missing'), false)
    assert.deepEqual(getGroupItems(entries, 'empty'), [{ label: 'Updated' }])
})
