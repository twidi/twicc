import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildSessionTree, treeActivity, treeGuides, flattenTree, extendTreeCandidates, findMissingAncestorIds, sharesProjectWithParent, treeParentId, TREE_MAX_INDENT_LEVEL } from './sessionTree.js'

const s = (id, o = {}) => ({ id, project_id: 'p', mtime: 100, spawned_by: null, ...o })
const lookupOf = list => id => list.find(x => x.id === id) ?? null
const build = (candidates, extra = []) =>
    buildSessionTree(candidates, { lookup: lookupOf([...candidates, ...extra]) })
const parent = (t, id) => t.nodes.get(id).parentId
const kids = (t, id) => t.nodes.get(id).childIds
const hint = (t, id) => t.nodes.get(id).hintParent?.id ?? null

test('child nests under a present parent', () => {
    const t = build([s('a'), s('b', { spawned_by: 'a' })])
    assert.deepEqual(t.rootIds, ['a'])
    assert.equal(parent(t, 'b'), 'a')
    assert.deepEqual(kids(t, 'a'), ['b'])
    assert.equal(hint(t, 'b'), null)
})

test('grandparent adopts when the parent is loaded but not a candidate', () => {
    const t = build([s('g'), s('c', { spawned_by: 'p1' })], [s('p1', { spawned_by: 'g' })])
    assert.deepEqual(t.rootIds, ['g'])
    assert.equal(parent(t, 'c'), 'g')
    assert.deepEqual(kids(t, 'g'), ['c'])
    assert.equal(hint(t, 'c'), null)
})

test('root with a loaded out-of-candidates parent carries hintParent', () => {
    const t = build([s('c', { spawned_by: 'p1' })], [s('p1')])
    assert.deepEqual(t.rootIds, ['c'])
    assert.equal(parent(t, 'c'), null)
    assert.equal(hint(t, 'c'), 'p1')
})

test('hidden, sub-agent, ephemeral and draft parents give no hint and end the chain', () => {
    const cases = [
        { hidden: true },
        { parent_session_id: 'x' },
        { ephemeral: true },
        { draft: true },
    ]
    for (const flags of cases) {
        const t = build([s('c', { spawned_by: 'p1' })], [s('p1', flags)])
        assert.deepEqual(t.rootIds, ['c'], JSON.stringify(flags))
        assert.equal(parent(t, 'c'), null)
        assert.equal(t.nodes.get('c').hintParent, null)
    }
})

test('unknown parent gives a root without hint', () => {
    const t = build([s('c', { spawned_by: 'nope' })])
    assert.deepEqual(t.rootIds, ['c'])
    assert.equal(parent(t, 'c'), null)
    assert.equal(t.nodes.get('c').hintParent, null)
})

test('a spawned_by cycle yields roots, never an empty forest', () => {
    const two = build([s('a', { spawned_by: 'b' }), s('b', { spawned_by: 'a' })])
    assert.deepEqual(two.rootIds, ['a', 'b'])
    for (const id of ['a', 'b']) {
        assert.equal(parent(two, id), null)
        assert.equal(hint(two, id), null)
        assert.deepEqual(kids(two, id), [])
    }

    const three = build([
        s('a', { spawned_by: 'c' }), s('b', { spawned_by: 'a' }), s('c', { spawned_by: 'b' }),
    ])
    assert.deepEqual(three.rootIds, ['a', 'b', 'c'])
    for (const id of ['a', 'b', 'c']) {
        assert.equal(parent(three, id), null)
        assert.equal(hint(three, id), null)
    }

    const self = build([s('a', { spawned_by: 'a' })])
    assert.deepEqual(self.rootIds, ['a'])
    assert.equal(parent(self, 'a'), null)
    assert.equal(hint(self, 'a'), null)

    const tail = build([
        s('a', { spawned_by: 'b' }), s('b', { spawned_by: 'a' }), s('t', { spawned_by: 'a' }),
    ])
    assert.deepEqual(tail.rootIds, ['a', 'b'])
    assert.equal(parent(tail, 't'), 'a')
    assert.deepEqual(kids(tail, 'a'), ['t'])
    assert.equal(hint(tail, 'a'), null)
    assert.equal(hint(tail, 'b'), null)
})

test('ephemeral and draft candidates are roots with no children even when a session points at them', () => {
    for (const flags of [{ ephemeral: true }, { draft: true }]) {
        const t = build([s('e', flags), s('c', { spawned_by: 'e' })])
        assert.deepEqual(t.rootIds, ['e', 'c'], JSON.stringify(flags))
        assert.deepEqual(kids(t, 'e'), [])
        assert.equal(hint(t, 'e'), null)
        assert.equal(parent(t, 'c'), null)
        assert.equal(hint(t, 'c'), null)
    }
})

// --- Task 3: effective state, ordering, triggers, summary ---

const run = (id, started_at) => ({ [id]: { started_at } })
const buildWith = (candidates, opts = {}) =>
    buildSessionTree(candidates, { lookup: lookupOf(candidates), ...opts })

test('idle root with an active child sorts before a newer idle root', () => {
    const t = buildWith(
        [s('old', { mtime: 10 }), s('kid', { mtime: 5, spawned_by: 'old' }), s('new', { mtime: 500 })],
        { processStates: run('kid', 50) },
    )
    assert.deepEqual(t.rootIds, ['old', 'new'])
    assert.equal(t.nodes.get('old').effective.active, true)
    assert.equal(t.nodes.get('new').effective.active, false)
})

test('a pinned descendant makes the root pinned and sorts it first', () => {
    const t = buildWith([
        s('a', { mtime: 10 }), s('k', { mtime: 5, spawned_by: 'a', pinned: 'all' }), s('b', { mtime: 500 }),
    ])
    assert.deepEqual(t.rootIds, ['a', 'b'])
    assert.equal(t.nodes.get('a').effective.pinned, 'all')
    assert.equal(t.nodes.get('b').effective.pinned, null)
})

test('activeKey is the highest started_at of the subtree; two active subtrees order by it', () => {
    const t = buildWith(
        [
            s('a'), s('a1', { spawned_by: 'a' }), s('a2', { spawned_by: 'a' }),
            s('b'), s('b1', { spawned_by: 'b' }),
        ],
        { processStates: { a1: { started_at: 10 }, a2: { started_at: 90 }, b1: { started_at: 50 } } },
    )
    assert.equal(t.nodes.get('a').effective.activeKey, 90)
    assert.equal(t.nodes.get('b').effective.activeKey, 50)
    assert.deepEqual(t.rootIds, ['a', 'b'])
    assert.deepEqual(kids(t, 'a'), ['a2', 'a1'])
    assert.equal(buildWith([s('x')]).nodes.get('x').effective.activeKey, 0)
})

test('a write to an active node (its mtime grows) does not reorder the siblings', () => {
    const make = mtime => [
        s('a'), s('a1', { spawned_by: 'a', mtime }), s('b'), s('b1', { spawned_by: 'b' }),
    ]
    const ps = { a1: { started_at: 20 }, b1: { started_at: 10 } }
    const before = buildWith(make(100), { processStates: ps })
    const after = buildWith(make(9999), { processStates: ps })
    assert.deepEqual(before.rootIds, ['a', 'b'])
    assert.deepEqual(after.rootIds, before.rootIds)
})

test('idle subtrees order by the highest mtime of the subtree', () => {
    const t = buildWith([
        s('a', { mtime: 10 }), s('a1', { spawned_by: 'a', mtime: 900 }), s('b', { mtime: 500 }),
    ])
    assert.equal(t.nodes.get('a').effective.dateKey, 900)
    assert.deepEqual(t.rootIds, ['a', 'b'])
})

test('children under one parent use the same effective ordering', () => {
    const t = buildWith(
        [
            s('p'), s('idleNew', { spawned_by: 'p', mtime: 300 }), s('idleOld', { spawned_by: 'p', mtime: 100 }),
            s('act', { spawned_by: 'p', mtime: 1 }), s('pin', { spawned_by: 'p', mtime: 2, pinned: 'project' }),
        ],
        { processStates: run('act', 5) },
    )
    assert.deepEqual(kids(t, 'p'), ['pin', 'act', 'idleNew', 'idleOld'])
})

test('triggerIds exclude the node itself and the viewed session counts as not unread', () => {
    const t = buildWith(
        [
            s('p', { pinned: 'all' }), s('u', { spawned_by: 'p' }), s('v', { spawned_by: 'p' }),
            s('act', { spawned_by: 'p' }), s('quiet', { spawned_by: 'p' }),
        ],
        {
            processStates: run('act', 5),
            viewedId: 'v',
            isUnread: session => session.id === 'u' || session.id === 'v',
        },
    )
    const node = t.nodes.get('p')
    assert.deepEqual([...node.triggerIds].sort(), ['act', 'u'])
    assert.deepEqual(t.nodes.get('quiet').triggerIds, [])
})

test('descendantIds lists descendants in display order, depth-first', () => {
    const t = build([
        s('a', { mtime: 500 }), s('b', { spawned_by: 'a', mtime: 400 }),
        s('c', { spawned_by: 'a', mtime: 300 }), s('d', { spawned_by: 'b', mtime: 200 }),
    ])
    assert.deepEqual(t.nodes.get('a').descendantIds, ['b', 'd', 'c'])
    assert.deepEqual(t.nodes.get('b').descendantIds, ['d'])
    assert.deepEqual(t.nodes.get('d').descendantIds, [])
})

test('tree nodes no longer carry a summary', () => {
    const t = build([s('a'), s('k', { spawned_by: 'a' })])
    assert.equal('summary' in t.nodes.get('a'), false)
})

const sessionsMap = list => id => list.find(x => x.id === id) ?? null
const act = (ids, o = {}) => treeActivity(ids, {
    processStates: {},
    sessions: sessionsMap([]),
    viewedId: null,
    isUnread: () => false,
    ...o,
})

test('treeActivity counts the live processes of every id given, the node itself included', () => {
    const processStates = { p: { state: 'assistant_turn' }, k1: { state: 'user_turn' }, k2: { state: 'user_turn' } }
    const sessions = sessionsMap([s('p'), s('k1'), s('k2')])
    const r = act(['p', 'k1', 'k2'], { processStates, sessions })
    assert.equal(r.processCount, 3)
    assert.equal(r.hasAssistantTurn, true)
    assert.equal(act(['k1', 'k2'], { processStates, sessions }).hasAssistantTurn, false)
})

test('treeActivity skips synthetic, ephemeral and hidden sessions', () => {
    const processStates = {
        syn: { state: 'user_turn', synthetic: true },
        eph: { state: 'user_turn', extra: { ephemeral: true } },
        hid: { state: 'user_turn' },
        ok: { state: 'user_turn' },
    }
    const sessions = sessionsMap([s('syn'), s('eph'), s('hid', { hidden: true }), s('ok')])
    const r = act(['syn', 'eph', 'hid', 'ok'], { processStates, sessions, isUnread: () => true })
    assert.equal(r.processCount, 1)
    assert.equal(r.unreadCount, 1)
})

test('treeActivity does not count the viewed session as unread', () => {
    const sessions = sessionsMap([s('u'), s('v')])
    const r = act(['u', 'v'], { sessions, viewedId: 'v', isUnread: () => true })
    assert.equal(r.unreadCount, 1)
})

test('treeActivity counts unread through isUnread and gives the process state to it', () => {
    const seen = []
    const sessions = sessionsMap([s('u'), s('q')])
    const processStates = { u: { state: 'user_turn' } }
    const r = act(['u', 'q'], {
        sessions, processStates,
        isUnread: (session, ps) => { seen.push([session.id, ps]); return session.id === 'u' },
    })
    assert.equal(r.unreadCount, 1)
    assert.equal(r.processCount, 1)
    assert.deepEqual(seen[0], ['u', processStates.u])
})

const withShell = { state: 'user_turn', background_work_in_progress: { subagents: 0, shells: 1, monitors: 0, scheduled_wakeup_at: null, goal: false } }
const withCron = { state: 'user_turn', active_crons: [{ id: 'c0' }] }

test('treeActivity counts an unread session with a shell as unread background work', () => {
    const r = act(['a'], { sessions: sessionsMap([s('a')]), processStates: { a: withShell }, isUnread: () => true })
    assert.equal(r.unreadBackgroundCount, 1)
    assert.equal(r.unreadShellCount, 1)
})

test('treeActivity counts an unread cron-only session without a shell', () => {
    const r = act(['a'], { sessions: sessionsMap([s('a')]), processStates: { a: withCron }, isUnread: () => true })
    assert.equal(r.unreadBackgroundCount, 1)
    assert.equal(r.unreadShellCount, 0)
})

test('treeActivity does not count an unread session without process as background work', () => {
    const r = act(['a'], { sessions: sessionsMap([s('a')]), isUnread: () => true })
    assert.equal(r.unreadCount, 1)
    assert.equal(r.unreadBackgroundCount, 0)
})

test('treeActivity keeps the unread background figures apart from a read session with a shell', () => {
    const r = act(['u', 'r'], {
        sessions: sessionsMap([s('u'), s('r')]),
        processStates: { u: withCron, r: withShell },
        isUnread: session => session.id === 'u',
    })
    assert.equal(r.unreadBackgroundCount, 1)
    assert.equal(r.unreadShellCount, 0)
    assert.equal(r.backgroundShellCount, 1)
})

test('treeActivity excludes the viewed session from the unread background figures', () => {
    const r = act(['v'], { sessions: sessionsMap([s('v')]), processStates: { v: withShell }, viewedId: 'v', isUnread: () => true })
    assert.equal(r.unreadBackgroundCount, 0)
    assert.equal(r.unreadShellCount, 0)
})

test('treeActivity with no live process and no unread yields two zero counts', () => {
    const r = act(['a', 'b'], { sessions: sessionsMap([s('a'), s('b')]) })
    assert.equal(r.processCount, 0)
    assert.equal(r.unreadCount, 0)
})

test('treeActivity over a built node folds the node and its descendants', () => {
    const list = [s('a', { mtime: 500 }), s('b', { spawned_by: 'a', mtime: 400 }), s('d', { spawned_by: 'b', mtime: 300 })]
    const t = build(list)
    const node = t.nodes.get('a')
    const r = act([node.id, ...node.descendantIds], {
        sessions: sessionsMap(list),
        processStates: { d: { state: 'assistant_turn', pending_requests: [{}] } },
    })
    assert.equal(r.processCount, 1)
    assert.equal(r.pendingRequestCount, 1)
})

// A(B(D), C) and a second root E(F).
const flatForest = () => build([
    s('a', { mtime: 500 }),
    s('b', { spawned_by: 'a', mtime: 400 }),
    s('c', { spawned_by: 'a', mtime: 300 }),
    s('d', { spawned_by: 'b', mtime: 200 }),
    s('e', { mtime: 100 }),
    s('f', { spawned_by: 'e', mtime: 50 }),
])

test('collapsed nodes hide their children', () => {
    const rows = flattenTree(flatForest(), () => false)
    assert.deepEqual(rows, [
        { id: 'a', depth: 0, isLast: true, through: [] },
        { id: 'e', depth: 0, isLast: true, through: [] },
    ])
})

test('expanded nodes list children right after the parent with depth + 1', () => {
    const rows = flattenTree(flatForest(), id => id === 'a' || id === 'b')
    assert.deepEqual(rows, [
        { id: 'a', depth: 0, isLast: true, through: [] },
        { id: 'b', depth: 1, isLast: false, through: [] },
        { id: 'd', depth: 2, isLast: true, through: [true] },
        { id: 'c', depth: 1, isLast: true, through: [] },
        { id: 'e', depth: 0, isLast: true, through: [] },
    ])
})

test('isExpanded = () => true lists the whole forest in display order', () => {
    const rows = flattenTree(flatForest(), () => true)
    assert.deepEqual(rows.map(r => r.id), ['a', 'b', 'd', 'c', 'e', 'f'])
    assert.deepEqual(rows.map(r => r.depth), [0, 1, 2, 1, 0, 1])
})

// Chain r > k (last) > m, and r > j (first sibling): deeper rows of a last branch stop their line.
test('through is false under a last-child ancestor and true under a non-last one', () => {
    const tree = build([
        s('r', { mtime: 500 }),
        s('k', { spawned_by: 'r', mtime: 400 }),
        s('j', { spawned_by: 'r', mtime: 300 }),
        s('m', { spawned_by: 'k', mtime: 200 }),
        s('n', { spawned_by: 'j', mtime: 100 }),
    ])
    const rows = flattenTree(tree, () => true)
    assert.deepEqual(rows.map(r => r.id), ['r', 'k', 'm', 'j', 'n'])
    assert.deepEqual(rows.find(r => r.id === 'm').through, [true])
    assert.deepEqual(rows.find(r => r.id === 'n').through, [false])
})

// r > a (not last) > b (not last) > c (last) > d, plus siblings below a and b.
test('through mixes true and false at depth 3 and 4', () => {
    const list = [
        s('r', { mtime: 900 }),
        s('a', { spawned_by: 'r', mtime: 800 }),
        s('a2', { spawned_by: 'r', mtime: 700 }),
        s('b', { spawned_by: 'a', mtime: 600 }),
        s('b2', { spawned_by: 'a', mtime: 500 }),
        s('c', { spawned_by: 'b', mtime: 400 }),
        s('c2', { spawned_by: 'b', mtime: 300 }),
        s('d', { spawned_by: 'c2', mtime: 200 }),
    ]
    const rows = flattenTree(build(list), () => true)
    assert.deepEqual(rows.map(r => r.id), ['r', 'a', 'b', 'c', 'c2', 'd', 'b2', 'a2'])
    const by = id => rows.find(r => r.id === id)
    assert.deepEqual(by('c').through, [true, true])
    assert.equal(by('c').isLast, false)
    assert.deepEqual(by('c2').through, [true, true])
    assert.equal(by('c2').isLast, true)
    assert.deepEqual(by('d').through, [true, true, false])
    assert.equal(by('d').depth, 4)
    assert.deepEqual(by('b2').through, [true])
    assert.deepEqual(by('a2').through, [])
})

test('isLast is about visible siblings of the parent, collapsed siblings included', () => {
    const rows = flattenTree(flatForest(), id => id === 'a')
    assert.deepEqual(rows.map(r => [r.id, r.isLast]), [['a', true], ['b', false], ['c', true], ['e', true]])
})

test('treeGuides: none for roots, one own column for depth 1', () => {
    assert.equal(treeGuides({ depth: 0, isLast: true, through: [] }), null)
    assert.deepEqual(treeGuides({ depth: 1, isLast: false, through: [] }), { columns: [], ownColumn: 0, ownEnds: false })
    assert.deepEqual(treeGuides({ depth: 1, isLast: true, through: [] }), { columns: [], ownColumn: 0, ownEnds: true })
})

test('treeGuides: continuing ancestor columns are the indexes of the true through entries', () => {
    assert.deepEqual(treeGuides({ depth: 3, isLast: true, through: [true, false] }), { columns: [0], ownColumn: 2, ownEnds: true })
    assert.deepEqual(treeGuides({ depth: 3, isLast: false, through: [false, true] }), { columns: [1], ownColumn: 2, ownEnds: false })
})

test('treeGuides: levels past the cap keep the third level width and its lines', () => {
    // depth 4: columns 0 and 1 come from through[0..1]; the own line (column 2) is shared by
    // the whole depth >= 3 band, so it continues while any band ancestor has later siblings.
    assert.deepEqual(treeGuides({ depth: 4, isLast: true, through: [true, false, false] }), { columns: [0], ownColumn: 2, ownEnds: true })
    assert.deepEqual(treeGuides({ depth: 4, isLast: true, through: [false, true, true] }), { columns: [1], ownColumn: 2, ownEnds: false })
    assert.deepEqual(treeGuides({ depth: 5, isLast: true, through: [false, false, false, true] }), { columns: [], ownColumn: 2, ownEnds: false })
    assert.deepEqual(treeGuides({ depth: 5, isLast: false, through: [false, false, false, false] }), { columns: [], ownColumn: 2, ownEnds: false })
})

test('TREE_MAX_INDENT_LEVEL is 3', () => {
    assert.equal(TREE_MAX_INDENT_LEVEL, 3)
})

const rootsOf = list => list.filter(x => !x.parent_session_id)
const extend = (natural, all, { inScope = () => true, passes = () => true } = {}) =>
    extendTreeCandidates(natural, {
        roots: rootsOf(all),
        lookup: lookupOf(all),
        isInScope: inScope,
        passesFilter: passes,
    }).map(x => x.id).sort()

test('extension adds an old in-scope ancestor', () => {
    const root = s('root'), mid = s('mid', { spawned_by: 'root' }), leaf = s('leaf', { spawned_by: 'mid' })
    assert.deepEqual(extend([leaf], [root, mid, leaf]), ['mid', 'root'])
})

test('extension adds an old in-scope descendant', () => {
    const root = s('root'), kid = s('kid', { spawned_by: 'root' }), grand = s('grand', { spawned_by: 'kid' })
    assert.deepEqual(extend([root], [root, kid, grand]), ['grand', 'kid'])
})

test('extension skips out-of-scope, hidden, sub-agent and draft sessions', () => {
    const root = s('root')
    const all = [
        root,
        s('out', { spawned_by: 'root', project_id: 'other' }),
        s('hid', { spawned_by: 'root', hidden: true }),
        s('sub', { spawned_by: 'root', parent_session_id: 'x' }),
        s('dra', { spawned_by: 'root', draft: true }),
        s('arc', { spawned_by: 'root', archived: true }),
        s('ok', { spawned_by: 'root' }),
    ]
    assert.deepEqual(
        extend([root], all, { inScope: x => x.project_id === 'p', passes: x => !x.archived }),
        ['ok'])
})

test('extension links through an out-of-scope middle session', () => {
    const root = s('root'), mid = s('mid', { spawned_by: 'root', project_id: 'other' })
    const leaf = s('leaf', { spawned_by: 'mid' })
    const inScope = x => x.project_id === 'p'
    assert.deepEqual(extend([leaf], [root, mid, leaf], { inScope }), ['root'])
    assert.deepEqual(extend([root], [root, mid, leaf], { inScope }), ['leaf'])
})

test('extension does not cross a hidden middle session', () => {
    const root = s('root'), mid = s('mid', { spawned_by: 'root', hidden: true })
    const leaf = s('leaf', { spawned_by: 'mid' })
    assert.deepEqual(extend([leaf], [root, mid, leaf]), [])
    assert.deepEqual(extend([root], [root, mid, leaf]), [])
})

test('extension does not cross a draft or ephemeral middle session', () => {
    for (const flag of [{ draft: true }, { ephemeral: true }]) {
        const root = s('root'), mid = s('mid', { spawned_by: 'root', ...flag })
        const leaf = s('leaf', { spawned_by: 'mid' })
        assert.deepEqual(extend([leaf], [root, mid, leaf]), [], JSON.stringify(flag))
        assert.deepEqual(extend([root], [root, mid, leaf]), [], JSON.stringify(flag))
    }
})

test('extension result does not depend on the order of the natural members', () => {
    const m = s('M'), c = s('C', { spawned_by: 'M' })
    const n = s('N', { spawned_by: 'C' }), d = s('D', { spawned_by: 'C' })
    const all = [m, c, n, d]
    assert.deepEqual(extend([m, n], all), ['C', 'D'])
    assert.deepEqual(extend([n, m], all), ['C', 'D'])
})

test('extension has no duplicate when two members share an ancestor', () => {
    const root = s('root'), a = s('a', { spawned_by: 'root' }), b = s('b', { spawned_by: 'root' })
    const out = extendTreeCandidates([a, b], {
        roots: [root, a, b], lookup: lookupOf([root, a, b]), isInScope: () => true, passesFilter: () => true,
    })
    assert.deepEqual(out.map(x => x.id), ['root'])
})

test('extension terminates on a spawned_by cycle among loaded sessions', () => {
    const x = s('x', { spawned_by: 'z' }), y = s('y', { spawned_by: 'x' }), z = s('z', { spawned_by: 'y' })
    const w = s('w', { spawned_by: 'x' })
    assert.deepEqual(extend([w], [x, y, z, w]), ['x', 'y', 'z'])
    assert.deepEqual(extend([x], [x, y, z, w]), ['w', 'y', 'z'])
})

test('extension does not cross a sub-agent middle session', () => {
    const root = s('root'), mid = s('mid', { spawned_by: 'root', parent_session_id: 'p' })
    const leaf = s('leaf', { spawned_by: 'mid' })
    assert.deepEqual(extend([leaf], [root, mid, leaf]), [])
    assert.deepEqual(extend([root], [root, mid, leaf]), [])
})

test('a draft or ephemeral member does not start a walk', () => {
    for (const flag of [{ draft: true }, { ephemeral: true }]) {
        const root = s('root'), member = s('m', { spawned_by: 'root', ...flag })
        const kid = s('kid', { spawned_by: 'm' })
        assert.deepEqual(extend([member], [root, member, kid]), [], JSON.stringify(flag))
    }
})

const missing = (candidates, loaded = [], skip = []) =>
    findMissingAncestorIds(candidates, lookupOf([...candidates, ...loaded]), new Set(skip))

test('returns the unloaded parent id', () => {
    assert.deepEqual(missing([s('c', { spawned_by: 'gone' })]), ['gone'])
    assert.deepEqual(missing([s('c')]), [])
})

test('follows loaded out-of-scope ancestors upward until an unloaded one', () => {
    const mid = s('mid', { spawned_by: 'top' })
    assert.deepEqual(missing([s('c', { spawned_by: 'mid' })], [mid]), ['top'])
})

test('skips ids in skipIds', () => {
    assert.deepEqual(missing([s('c', { spawned_by: 'gone' })], [], ['gone']), [])
})

test('stops at a hidden, ephemeral or draft loaded ancestor', () => {
    for (const flag of [{ hidden: true }, { ephemeral: true }, { draft: true }, { parent_session_id: 'p' }]) {
        const mid = s('mid', { spawned_by: 'top', ...flag })
        assert.deepEqual(missing([s('c', { spawned_by: 'mid' })], [mid]), [], JSON.stringify(flag))
    }
})

test('an ephemeral or draft candidate does not start a walk', () => {
    for (const flag of [{ draft: true }, { ephemeral: true }]) {
        assert.deepEqual(missing([s('c', { spawned_by: 'gone', ...flag })]), [], JSON.stringify(flag))
    }
})

test('terminates on a cycle', () => {
    const x = s('x', { spawned_by: 'z' }), y = s('y', { spawned_by: 'x' }), z = s('z', { spawned_by: 'y' })
    assert.deepEqual(missing([x], [y, z]), [])
})

test('deduplicates ids shared by several candidates', () => {
    assert.deepEqual(missing([s('a', { spawned_by: 'g' }), s('b', { spawned_by: 'g' })]), ['g'])
})

test('sharesProjectWithParent: child of the same project', () => {
    const t = build([s('a'), s('b', { spawned_by: 'a' })])
    assert.equal(sharesProjectWithParent(t, 'b'), true)
})

test('sharesProjectWithParent: a worktree (own project id) is different', () => {
    const t = build([s('a'), s('b', { spawned_by: 'a', project_id: 'p-worktree' })])
    assert.equal(sharesProjectWithParent(t, 'b'), false)
})

test('sharesProjectWithParent: a root never shares', () => {
    const t = build([s('a'), s('b')])
    assert.equal(sharesProjectWithParent(t, 'a'), false)
})

test('sharesProjectWithParent: the reference is the direct parent (A, A, A)', () => {
    const t = build([s('a'), s('b', { spawned_by: 'a' }), s('c', { spawned_by: 'b' })])
    assert.equal(sharesProjectWithParent(t, 'c'), true)
})

test('sharesProjectWithParent: the comparison restarts below another project (A, B, B)', () => {
    const t = build([
        s('a'),
        s('b', { spawned_by: 'a', project_id: 'q' }),
        s('c', { spawned_by: 'b', project_id: 'q' }),
    ])
    assert.equal(sharesProjectWithParent(t, 'b'), false)
    assert.equal(sharesProjectWithParent(t, 'c'), true)
})

test('sharesProjectWithParent: back to the first project (A, B, A) shows again', () => {
    const t = build([
        s('a'),
        s('b', { spawned_by: 'a', project_id: 'q' }),
        s('c', { spawned_by: 'b' }),
    ])
    assert.equal(sharesProjectWithParent(t, 'c'), false)
})

test('treeParentId: attached, detached and no spawner', () => {
    assert.equal(treeParentId(s('b', { spawned_by: 'a' })), 'a')
    assert.equal(treeParentId(s('b', { spawned_by: 'a', tree_detached: true })), null)
    assert.equal(treeParentId(s('b')), null)
})

test('detached child is a root with no hint and its spawner has no child', () => {
    const t = build([s('a'), s('b', { spawned_by: 'a', tree_detached: true })])
    assert.deepEqual([...t.rootIds].sort(), ['a', 'b'])
    assert.equal(parent(t, 'b'), null)
    assert.equal(hint(t, 'b'), null)
    assert.deepEqual(kids(t, 'a'), [])
    // Spawner loaded but not a candidate: still no hint.
    const t2 = build([s('b', { spawned_by: 'a', tree_detached: true })], [s('a')])
    assert.equal(hint(t2, 'b'), null)
})

test('chain A -> B(detached) -> C: C never resolves to A', () => {
    const a = s('a')
    const b = s('b', { spawned_by: 'a', tree_detached: true })
    const c = s('c', { spawned_by: 'b' })
    const t = build([a, b, c])
    assert.equal(parent(t, 'b'), null)
    assert.equal(parent(t, 'c'), 'b')
    // B is not a candidate: C is a root and does not climb to A.
    const t2 = build([a, c], [b])
    assert.equal(parent(t2, 'c'), null)
    assert.deepEqual(kids(t2, 'a'), [])
    assert.equal(hint(t2, 'c'), 'b')
})

test('detached session keeps its attached children', () => {
    const t = build([s('a'), s('b', { spawned_by: 'a', tree_detached: true }), s('c', { spawned_by: 'b' }), s('d', { spawned_by: 'b' })])
    assert.deepEqual([...kids(t, 'b')].sort(), ['c', 'd'])
    assert.deepEqual(kids(t, 'a'), [])
})

test('extendTreeCandidates and findMissingAncestorIds stop at a detached session', () => {
    const a = s('a')
    const b = s('b', { spawned_by: 'a', tree_detached: true })
    const c = s('c', { spawned_by: 'b' })
    const opts = { roots: [a, b, c], lookup: lookupOf([a, b, c]), isInScope: () => true, passesFilter: () => true }
    // Up: from C, B is added, A is not.
    assert.deepEqual(extendTreeCandidates([c], opts).map(x => x.id), ['b'])
    // Down: from A, the detached B (and its subtree) is not collected.
    assert.deepEqual(extendTreeCandidates([a], opts).map(x => x.id), [])
    // Up from B itself: nothing above.
    assert.deepEqual(extendTreeCandidates([b], opts).map(x => x.id).sort(), ['c'])
    // Missing ancestors: the walk does not go above the detached B.
    assert.deepEqual(findMissingAncestorIds([b], () => null, new Set()), [])
    assert.deepEqual(findMissingAncestorIds([c], id => ({ b })[id] ?? null, new Set()), [])
})
