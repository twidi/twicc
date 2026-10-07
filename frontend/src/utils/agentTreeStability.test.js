import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive, computed, watch, nextTick } from 'vue'
import { agentLinkState, setAgentLink, buildAgentTree, clearAgentLinks, markAgentStopped } from './agentLinkIndex.js'

function fixture() {
    const state = reactive(agentLinkState())
    const add = (id, owner = 'root', fields = {}) => setAgentLink(state, owner, `spawn-${id}`, {
        agentId: id, rootSessionId: 'root', startedAt: '2026-10-07T10:00:00Z', ...fields,
    })
    add('a'); add('b'); add('c', 'a')
    return { state, add }
}

test('unchanged agent forests retain their reference across unrelated roots', async () => {
    const { state } = fixture()
    const tree = computed(previous => buildAgentTree(state, 'root', previous)), before = tree.value
    let updates = 0
    const stop = watch(tree, () => updates++)
    setAgentLink(state, 'other', 'spawn-x', { agentId: 'x', rootSessionId: 'other' })
    await nextTick()
    assert.strictEqual(tree.value, before)
    assert.equal(updates, 0)
    stop()
})

test('changed nested metrics retain sibling nodes and unchanged descendants', () => {
    const { state, add } = fixture(), before = buildAgentTree(state, 'root')
    add('a', 'root', { metrics: { totalCost: 3 } })
    const after = buildAgentTree(state, 'root', before)
    assert.equal(after[0].entry.metrics.totalCost, 3)
    assert.notStrictEqual(after[0], before[0])
    assert.strictEqual(after[1], before[1])
    assert.strictEqual(after[0].children, before[0].children)
})

test('reparenting ordering and removal update the forest without retaining stale children', () => {
    const { state, add } = fixture()
    const before = buildAgentTree(state, 'root')
    add('c', 'b')
    const moved = buildAgentTree(state, 'root', before)
    assert.deepEqual(moved.map(n => [n.id, n.children.map(c => c.id)]), [['a', []], ['b', ['c']]])
    add('b', 'root', { startedAt: '2026-10-07T09:00:00Z' })
    const ordered = buildAgentTree(state, 'root', moved)
    assert.deepEqual(ordered.map(n => n.id), ['b', 'a'])
    clearAgentLinks(state, 'b')
    assert.deepEqual(buildAgentTree(state, 'root', ordered).map(n => [n.id, n.children]), [['b', []], ['a', []]])
})

test('in-place stop timestamps remain reactive through retained nodes', () => {
    const { state } = fixture(), before = buildAgentTree(state, 'root')
    const end = computed(() => before[0].entry.stoppedAt)
    assert.equal(end.value, null)
    markAgentStopped(state, 'a', '2026-10-07T10:01:00Z', 'root')
    assert.equal(end.value, '2026-10-07T10:01:00Z')
    assert.strictEqual(buildAgentTree(state, 'root', before), before)
})
