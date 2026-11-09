import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    allChecked,
    archiveDialogButtons,
    buildArchiveTree,
    checkRow,
    initialChecked,
    isArchiveBarrier,
    isWorkInProgress,
    selectAll,
    uncheckRow,
    warningTotals,
} from './archiveTree.js'
import { treeGuides } from './sessionTree.js'

/** Spec of a topology node: `state`, `background` and `crons`, the rest is the session. */
const n = (id, options = {}, children = []) => ({ id, options, children })

/** Build the topology payload of a nested `n()` spec. */
function topologyOf(root) {
    const nodes = []
    const toTree = ({ id, options, children }) => {
        const { state = 'dead', background = null, crons = 0, ...session } = options
        nodes.push({
            id,
            session: { id, ...session },
            process: state === null ? null : { state, background_work_in_progress: background },
            active_cron_count: crons,
        })
        return { id, children: children.map(toTree) }
    }
    return { tree: toTree(root), nodes }
}

const rowsOf = (root, targetId = root.id) => buildArchiveTree(topologyOf(root), targetId)
const ids = (rows) => rows.map((row) => row.id)
const rowOf = (rows, id) => rows.find((row) => row.id === id)
const sorted = (set) => [...set].sort()

const RUNNING = { state: 'assistant_turn' }
const SHELL = { background: { shells: 1 } }

test('isWorkInProgress is true for the three live states', () => {
    for (const state of ['starting', 'assistant_turn', 'awaiting_user_input']) {
        assert.equal(isWorkInProgress({ state, backgroundWork: null }), true, state)
    }
})

test('isWorkInProgress is true for background work, whatever the state', () => {
    for (const state of ['user_turn', 'dead', undefined]) {
        assert.equal(isWorkInProgress({ state, backgroundWork: { shells: 1 } }), true, String(state))
    }
})

test('isWorkInProgress is false for an idle or a dead session', () => {
    assert.equal(isWorkInProgress({ state: 'user_turn', backgroundWork: null }), false)
    assert.equal(isWorkInProgress({ state: 'dead', backgroundWork: null }), false)
    assert.equal(isWorkInProgress({ state: undefined, backgroundWork: undefined }), false)
})

test('isArchiveBarrier covers work in progress, crons and pin', () => {
    const barrier = (options) => isArchiveBarrier(topologyOf(n('x', options)).nodes[0])
    assert.equal(barrier({ state: 'starting' }), true)
    assert.equal(barrier({ state: 'assistant_turn' }), true)
    assert.equal(barrier({ state: 'awaiting_user_input' }), true)
    assert.equal(barrier({ state: 'user_turn', background: { shells: 1 } }), true)
    assert.equal(barrier({ state: 'dead', crons: 1 }), true)
    assert.equal(barrier({ pinned: true }), true)
})

test('isArchiveBarrier is false for an idle, a dead and a processless session', () => {
    const barrier = (options) => isArchiveBarrier(topologyOf(n('x', options)).nodes[0])
    assert.equal(barrier({ state: 'user_turn' }), false)
    assert.equal(barrier({ state: 'dead' }), false)
    assert.equal(barrier({ state: null }), false)
    assert.equal(barrier({ state: null, pinned: false, crons: 0 }), false)
})

test('the walk starts at the target, anywhere in the tree', () => {
    const rows = rowsOf(
        n('root', {}, [
            n('other'),
            n('target', {}, [n('a', {}, [n('b')])]),
        ]),
        'target',
    )
    assert.deepEqual(ids(rows), ['a', 'b'])
    assert.equal(rowOf(rows, 'a').depth, 0)
    assert.equal(rowOf(rows, 'b').depth, 1)
})

test('a detached target still lists its descendants', () => {
    const rows = rowsOf(n('root', {}, [n('target', { tree_detached: true }, [n('a')])]), 'target')
    assert.deepEqual(ids(rows), ['a'])
})

test('an unknown target has no row', () => {
    assert.deepEqual(rowsOf(n('root', {}, [n('a')]), 'missing'), [])
})

test('a row carries its session', () => {
    const rows = rowsOf(n('root', {}, [n('a', { project_id: 'p' })]))
    assert.deepEqual(rowOf(rows, 'a').session, { id: 'a', project_id: 'p' })
})

test('a detached subtree is absent, hidden and archived sessions included', () => {
    const rows = rowsOf(n('root', {}, [
        n('kept'),
        n('detached', { tree_detached: true }, [n('below'), n('hiddenBelow', { hidden: true }, [n('deep')])]),
        n('detachedHidden', { tree_detached: true, hidden: true }, [n('underHidden')]),
        n('detachedArchived', { tree_detached: true, archived: true }, [n('underArchived')]),
    ]))
    assert.deepEqual(ids(rows), ['kept'])
})

test('a detached session ends the walk below a traversed session', () => {
    const rows = rowsOf(n('root', {}, [
        n('hidden', { hidden: true }, [n('detached', { tree_detached: true }, [n('below')]), n('kept')]),
    ]))
    assert.deepEqual(ids(rows), ['kept'])
})

test('archived and hidden sessions are traversed and their descendants reattach', () => {
    const rows = rowsOf(n('root', {}, [
        n('a', {}, [
            n('archived', { archived: true }, [n('b')]),
            n('hidden', { hidden: true }, [n('c', {}, [n('d')])]),
        ]),
        n('hiddenTop', { hidden: true }, [n('archivedDeep', { archived: true }, [n('e')])]),
    ]))
    assert.deepEqual(ids(rows), ['a', 'b', 'c', 'd', 'e'])
    assert.deepEqual(rowOf(rows, 'a').childIds, ['b', 'c'])
    assert.deepEqual(rowOf(rows, 'c').childIds, ['d'])
    assert.equal(rowOf(rows, 'b').depth, 1)
    assert.equal(rowOf(rows, 'c').depth, 1)
    assert.equal(rowOf(rows, 'd').depth, 2)
    assert.equal(rowOf(rows, 'e').depth, 0)
})

test('guides of a three-level tree have the sidebar shape', () => {
    const rows = rowsOf(n('root', {}, [
        n('a', {}, [
            n('b', {}, [n('c')]),
            n('d'),
        ]),
        n('e', {}, [n('f', {}, [n('g')])]),
    ]))
    const shape = (id) => {
        const { depth, isLast, through } = rowOf(rows, id)
        return { depth, isLast, through }
    }
    assert.deepEqual(ids(rows), ['a', 'b', 'c', 'd', 'e', 'f', 'g'])
    assert.deepEqual(shape('a'), { depth: 0, isLast: true, through: [] })
    assert.deepEqual(shape('e'), { depth: 0, isLast: true, through: [] })
    assert.deepEqual(shape('b'), { depth: 1, isLast: false, through: [] })
    assert.deepEqual(shape('c'), { depth: 2, isLast: true, through: [true] })
    assert.deepEqual(shape('d'), { depth: 1, isLast: true, through: [] })
    assert.deepEqual(shape('g'), { depth: 2, isLast: true, through: [false] })

    assert.equal(treeGuides(rowOf(rows, 'a')), null)
    assert.deepEqual(treeGuides(rowOf(rows, 'c')), { columns: [0], ownColumn: 1, ownEnds: true })
    assert.deepEqual(treeGuides(rowOf(rows, 'g')), { columns: [], ownColumn: 1, ownEnds: true })
})

test('isLast counts listed siblings only', () => {
    const rows = rowsOf(n('root', {}, [
        n('a', {}, [
            n('first'),
            n('emptyHidden', { hidden: true }),
            n('detached', { tree_detached: true }),
        ]),
    ]))
    assert.equal(rowOf(rows, 'first').isLast, true)
})

test('a reattached row is the last sibling when it comes last', () => {
    const rows = rowsOf(n('root', {}, [
        n('a', {}, [
            n('first'),
            n('hidden', { hidden: true }, [n('reattached')]),
        ]),
    ]))
    assert.equal(rowOf(rows, 'first').isLast, false)
    assert.equal(rowOf(rows, 'reattached').isLast, true)
})

test('a barrier and everything below it are unchecked at opening', () => {
    const rows = rowsOf(n('root', {}, [
        n('free', {}, [n('freeChild')]),
        n('running', RUNNING, [n('below', {}, [n('deep')])]),
        n('pinned', { pinned: true }),
        n('shell', { state: 'user_turn', ...SHELL }),
        n('cron', { crons: 2 }),
    ]))
    assert.deepEqual(sorted(initialChecked(rows)), ['free', 'freeChild'])
    assert.equal(rowOf(rows, 'running').barrier, true)
    assert.equal(rowOf(rows, 'running').stopsDescent, true)
    assert.equal(rowOf(rows, 'running').openChecked, false)
    assert.equal(rowOf(rows, 'below').barrier, false)
    assert.equal(rowOf(rows, 'below').stopsDescent, false)
    assert.equal(rowOf(rows, 'below').openChecked, false)
    assert.equal(rowOf(rows, 'deep').openChecked, false)
})

test('rows below a hidden barrier stop the descent and open unchecked', () => {
    const rows = rowsOf(n('root', {}, [
        n('a', {}, [
            n('hiddenBarrier', { hidden: true, crons: 1 }, [n('b', {}, [n('c')])]),
            n('sibling'),
        ]),
    ]))
    assert.deepEqual(sorted(initialChecked(rows)), ['a', 'sibling'])
    assert.equal(rowOf(rows, 'b').barrier, false)
    assert.equal(rowOf(rows, 'b').stopsDescent, true)
    assert.equal(rowOf(rows, 'b').openChecked, false)
    assert.equal(rowOf(rows, 'c').stopsDescent, false)
    assert.equal(rowOf(rows, 'c').openChecked, false)
})

test('a hidden session with no barrier does not change the rows below it', () => {
    const rows = rowsOf(n('root', {}, [n('hidden', { hidden: true }, [n('a')])]))
    assert.equal(rowOf(rows, 'a').stopsDescent, false)
    assert.equal(rowOf(rows, 'a').openChecked, true)
})

test('a hidden barrier is still crossed through an archived session', () => {
    const rows = rowsOf(n('root', {}, [
        n('hiddenBarrier', { hidden: true, ...RUNNING }, [
            n('archived', { archived: true }, [n('a')]),
        ]),
    ]))
    assert.equal(rowOf(rows, 'a').stopsDescent, true)
    assert.equal(rowOf(rows, 'a').openChecked, false)
})

test('an archived session is never a crossed barrier, even when hidden', () => {
    const rows = rowsOf(n('root', {}, [
        n('archivedHidden', { archived: true, hidden: true, ...RUNNING, pinned: true, crons: 1 }, [n('a')]),
        n('archivedRunning', { archived: true, ...RUNNING }, [n('b')]),
    ]))
    assert.deepEqual(ids(rows), ['a', 'b'])
    assert.equal(rowOf(rows, 'a').stopsDescent, false)
    assert.equal(rowOf(rows, 'a').openChecked, true)
    assert.equal(rowOf(rows, 'b').stopsDescent, false)
    assert.equal(rowOf(rows, 'b').openChecked, true)
})

test('a crossed hidden barrier does not reach past the next listed row', () => {
    const rows = rowsOf(n('root', {}, [
        n('hiddenBarrier', { hidden: true, ...RUNNING }, [n('a', {}, [n('b')])]),
    ]))
    assert.equal(rowOf(rows, 'a').stopsDescent, true)
    assert.equal(rowOf(rows, 'b').stopsDescent, false)
    assert.deepEqual(sorted(checkRow(rows, new Set(), 'a')), ['a', 'b'])
})

test('the check descent stops at a barrier', () => {
    const rows = rowsOf(n('root', {}, [
        n('a', {}, [
            n('free'),
            n('running', RUNNING, [n('below')]),
        ]),
    ]))
    assert.deepEqual(sorted(checkRow(rows, new Set(), 'a')), ['a', 'free'])
})

test('a barrier met by the descent and the rows below it stay as they were', () => {
    const rows = rowsOf(n('root', {}, [
        n('a', {}, [n('running', RUNNING, [n('below'), n('other')])]),
    ]))
    assert.deepEqual(sorted(checkRow(rows, new Set(['below']), 'a')), ['a', 'below'])
    assert.deepEqual(
        sorted(checkRow(rows, new Set(['running', 'other']), 'a')),
        ['a', 'other', 'running'],
    )
})

test('checking a barrier checks it and its descendants', () => {
    const rows = rowsOf(n('root', {}, [
        n('running', RUNNING, [n('below', {}, [n('deep')])]),
    ]))
    assert.deepEqual(sorted(checkRow(rows, new Set(), 'running')), ['below', 'deep', 'running'])
})

test('the check descent stops at a nested barrier below a checked barrier', () => {
    const rows = rowsOf(n('root', {}, [
        n('running', RUNNING, [n('nested', { pinned: true }, [n('below')])]),
    ]))
    assert.deepEqual(sorted(checkRow(rows, new Set(), 'running')), ['running'])
})

test('the check descent stops below a hidden barrier', () => {
    const rows = rowsOf(n('root', {}, [
        n('a', {}, [
            n('hiddenBarrier', { hidden: true, pinned: true }, [n('b', {}, [n('c')])]),
            n('free'),
        ]),
    ]))
    assert.deepEqual(sorted(checkRow(rows, new Set(), 'a')), ['a', 'free'])
    assert.deepEqual(sorted(checkRow(rows, new Set(), 'b')), ['b', 'c'])
})

test('uncheck removes the row and its whole subtree, barriers included', () => {
    const rows = rowsOf(n('root', {}, [
        n('a', {}, [
            n('free'),
            n('running', RUNNING, [n('below')]),
        ]),
        n('outside'),
    ]))
    const checked = new Set(['a', 'free', 'running', 'below', 'outside'])
    assert.deepEqual(sorted(uncheckRow(rows, checked, 'a')), ['outside'])
})

test('a child can stay checked with its parent unchecked', () => {
    const rows = rowsOf(n('root', {}, [n('a', {}, [n('b')])]))
    assert.deepEqual(sorted(checkRow(rows, new Set(), 'b')), ['b'])
})

test('a parent can stay checked with its child unchecked', () => {
    const rows = rowsOf(n('root', {}, [n('a', {}, [n('b')])]))
    assert.deepEqual(sorted(uncheckRow(rows, new Set(['a', 'b']), 'b')), ['a'])
})

test('checkRow and uncheckRow return a new set and leave the input as it was', () => {
    const rows = rowsOf(n('root', {}, [n('a', {}, [n('b')])]))
    const checked = new Set(['b'])
    const after = checkRow(rows, checked, 'a')
    assert.notEqual(after, checked)
    assert.deepEqual(sorted(checked), ['b'])
    const removed = uncheckRow(rows, after, 'a')
    assert.notEqual(removed, after)
    assert.deepEqual(sorted(after), ['a', 'b'])
})

test('selectAll includes barriers and rows below them', () => {
    const rows = rowsOf(n('root', {}, [
        n('free'),
        n('running', RUNNING, [n('below')]),
        n('hidden', { hidden: true, crons: 1 }, [n('underHidden')]),
    ]))
    assert.deepEqual(sorted(selectAll(rows)), ['below', 'free', 'running', 'underHidden'])
})

test('allChecked is true only when every row is checked', () => {
    const rows = rowsOf(n('root', {}, [n('a'), n('b')]))
    assert.equal(allChecked(rows, new Set(['a', 'b'])), true)
    assert.equal(allChecked(rows, new Set(['a'])), false)
    assert.equal(allChecked(rows, new Set()), false)
})

test('warningTotals sums the entries', () => {
    assert.deepEqual(
        warningTotals([
            { working: true, shells: 2, crons: 0 },
            { working: false, shells: 0, crons: 3 },
            { working: true, shells: 1, crons: 1 },
        ]),
        { processCount: 2, shellCount: 3, cronCount: 4 },
    )
    assert.deepEqual(warningTotals([]), { processCount: 0, shellCount: 0, cronCount: 0 })
})

const KEY = 'session.archiveDescendants'

test('buttons with zero checked rows and no warning', () => {
    assert.deepEqual(
        archiveDialogButtons({ checkedCount: 0, warningVisible: false, targetConcerned: false }),
        {
            primary: { key: `${KEY}.archiveOnly`, danger: false },
            secondary: null,
            cancel: { key: `${KEY}.cancel` },
        },
    )
})

test('buttons with zero checked rows and a warning on the target', () => {
    assert.deepEqual(
        archiveDialogButtons({ checkedCount: 0, warningVisible: true, targetConcerned: true }),
        {
            primary: { key: `${KEY}.stopArchiveOnly`, danger: true },
            secondary: null,
            cancel: { key: 'session.stopConfirmation.cancel' },
        },
    )
})

test('buttons with checked rows and no warning', () => {
    assert.deepEqual(
        archiveDialogButtons({ checkedCount: 3, warningVisible: false, targetConcerned: false }),
        {
            primary: { key: `${KEY}.archiveWith`, count: 3, danger: false },
            secondary: { key: `${KEY}.archiveOnly` },
            cancel: { key: `${KEY}.cancel` },
        },
    )
})

test('buttons with a warning on the target', () => {
    assert.deepEqual(
        archiveDialogButtons({ checkedCount: 2, warningVisible: true, targetConcerned: true }),
        {
            primary: { key: `${KEY}.stopArchiveWith`, count: 2, danger: true },
            secondary: { key: `${KEY}.stopArchiveOnly` },
            cancel: { key: 'session.stopConfirmation.cancel' },
        },
    )
})

test('buttons with a warning on a checked row only', () => {
    assert.deepEqual(
        archiveDialogButtons({ checkedCount: 1, warningVisible: true, targetConcerned: false }),
        {
            primary: { key: `${KEY}.stopArchiveWith`, count: 1, danger: true },
            secondary: { key: `${KEY}.archiveOnly` },
            cancel: { key: `${KEY}.cancel` },
        },
    )
})

test('without a visible warning, the target cannot be concerned', () => {
    assert.deepEqual(
        archiveDialogButtons({ checkedCount: 1, warningVisible: false, targetConcerned: true }),
        {
            primary: { key: `${KEY}.archiveWith`, count: 1, danger: false },
            secondary: { key: `${KEY}.archiveOnly` },
            cancel: { key: `${KEY}.cancel` },
        },
    )
})
