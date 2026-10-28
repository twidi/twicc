import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const sfc = read('./SessionList.vue')
const dataStore = read('../../../stores/data.js')

test('declares the showAsTree prop and passes the active flag to the blocks helper', () => {
    assert.match(sfc, /showAsTree:\s*\{\s*type: Boolean,\s*default: false\s*\}/)
    assert.match(sfc, /showAsTree: props.showAsTree,/)
    assert.ok(sfc.includes('const treeActive = computed(() => props.showAsTree && !props.searchQuery.trim())'))
})

test('imports the tree building blocks', () => {
    assert.match(sfc, /import \{[^}]*buildSessionTree[^}]*\} from '..\/..\/..\/utils\/sessionTree\.js'/)
    assert.match(sfc, /import \{[^}]*flattenTree[^}]*\} from '..\/..\/..\/utils\/sessionTree\.js'/)
    assert.ok(sfc.includes('useSessionTreeExpansionStore'))
    assert.ok(sfc.includes('useSessionTreeAncestors(natural, treeActive)'))
})

test('binds the tree row to the list item and handles its toggle', () => {
    assert.ok(sfc.includes(':tree-row='))
    assert.ok(sfc.includes('@toggle-tree='))
    assert.ok(sfc.includes('expansion.toggle(session.id, tree.value)'))
})

test('the switcher publish offers every tree node, collapsed or not', () => {
    assert.ok(sfc.includes('flattenTree(tree.value, () => true)'))
    assert.ok(sfc.includes('const switcherIds = computed('))
})

test('reconcile is gated on settled, which reads the fetched and processes-received flags', () => {
    assert.ok(sfc.includes('activeProcessesReceived'))
    assert.ok(sfc.includes('expansion.reconcile(t, ok, props.projectId, seedTriggerIds)'))
})

test('the reveal mark is keyed on the ancestor path', () => {
    assert.ok(sfc.includes('expansion.reveal(t, id, previousPath)'))
    assert.match(sfc, /ancestorPath\(/)
    assert.match(sfc, /flush: 'post'[\s\S]*scrollToSession\(/)
    const postWatchers = sfc.match(/cascade\.holdTarget\(id, scrollToSession\(id\)\)/g) ?? []
    assert.equal(postWatchers.length, 1)
})

test('exits ignore tree nodes and the scope includes the mode', () => {
    assert.ok(sfc.includes('!store.sessions[s.id]?.archived') || sfc.includes('!!store.sessions[s.id]?.archived'))
    assert.ok(sfc.includes('!tree.value.nodes.has(s.id)'))
    assert.ok(sfc.includes('props.showAsTree'))
})

test('data store declares and sets activeProcessesReceived', () => {
    assert.ok(dataStore.includes('activeProcessesReceived: false,'))
    assert.ok(dataStore.includes('this.activeProcessesReceived = true'))
})

test('the reveal mark resets when the tree is toggled or the scope changes', () => {
    assert.match(sfc, /watch\(\[treeActive, \(\) => props\.projectId\], \(\) => \{ lastRevealed = null \}\)/)
})

test('the parent hint falls back to the shared display name', () => {
    assert.ok(sfc.includes('getSessionDisplayName(node.hintParent)'))
    assert.ok(!sfc.includes('node.hintParent?.title'))
})

test('row metadata carries the guide data and the cumulative activity, compared field by field', () => {
    assert.ok(sfc.includes('treeActivity('))
    assert.ok(sfc.includes('[id, ...node.descendantIds]'))
    assert.match(sfc, /activity: hasChildren && !expanded\s*\? treeActivity\(/)
    assert.ok(sfc.includes('sameThrough(a.through, b.through)'))
    assert.ok(sfc.includes('sameActivity(a.activity, b.activity)'))
    assert.ok(!sfc.includes('sameSummary'))
    assert.ok(!sfc.includes('node.summary'))
})

test('row metadata carries sameProjectAsParent from the pure helper and compares it', () => {
    assert.ok(sfc.includes('sameProjectAsParent: sharesProjectWithParent(tree.value, id)'))
    assert.ok(sfc.includes('a.sameProjectAsParent === b.sameProjectAsParent'))
})
