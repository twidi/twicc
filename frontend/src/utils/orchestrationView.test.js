import assert from 'node:assert/strict'
import test from 'node:test'

import {
    agentModelLabel, annotationEntries, annotationValueText, bucketOfProcessState, buildAnnotationTree,
    computeTimeline, countBuckets, cumulativeSeconds, findSubtree, flattenAnnotations, flattenTree, isoMs, parentOf,
    splitCommonPrefix,
} from './orchestrationView.js'

const tree = {
    id: 'root',
    children: [
        { id: 'a', children: [{ id: 'a1', children: [] }, { id: 'a2', children: [] }] },
        { id: 'b', children: [] },
    ],
}

// ── buckets ─────────────────────────────────────────────────────────────────
test('process states fall in four buckets; starting counts as working', () => {
    assert.equal(bucketOfProcessState('starting'), 'working')
    assert.equal(bucketOfProcessState('assistant_turn'), 'working')
    assert.equal(bucketOfProcessState('awaiting_user_input'), 'awaiting')
    assert.equal(bucketOfProcessState('user_turn'), 'idle')
    assert.equal(bucketOfProcessState('dead'), 'stopped')
    assert.equal(bucketOfProcessState(undefined), 'stopped')
})

test('countBuckets always returns the four buckets', () => {
    assert.deepEqual(countBuckets(['working', 'idle', 'idle']), { working: 1, awaiting: 0, idle: 2, stopped: 0 })
    assert.deepEqual(countBuckets([]), { working: 0, awaiting: 0, idle: 0, stopped: 0 })
})

// ── tree helpers ────────────────────────────────────────────────────────────
test('findSubtree finds a node at any depth, null when absent or without a tree', () => {
    assert.equal(findSubtree(tree, 'a').id, 'a')
    assert.equal(findSubtree(tree, 'a2').id, 'a2')
    assert.equal(findSubtree(tree, 'root'), tree)
    assert.equal(findSubtree(tree, 'nope'), null)
    assert.equal(findSubtree(null, 'a'), null)
})

test('flattenTree lists the node first, then its descendants, depth first', () => {
    assert.deepEqual(flattenTree(tree).map(n => n.id), ['root', 'a', 'a1', 'a2', 'b'])
})

test('parentOf resolves the spawner, null when unknown, absent or missing from the payload', () => {
    const nodesById = { c: { id: 'c', session: { spawned_by: 'p' } }, p: { id: 'p', session: {} }, d: { id: 'd', session: { spawned_by: 'gone' } }, e: { id: 'e', session: {} } }
    assert.equal(parentOf(nodesById, 'c').id, 'p')
    assert.equal(parentOf(nodesById, 'd'), null)
    assert.equal(parentOf(nodesById, 'e'), null)
    assert.equal(parentOf(nodesById, 'missing'), null)
})

test('isoMs parses ISO strings and rejects empty or invalid input', () => {
    assert.equal(isoMs('2026-10-05T10:00:00Z'), Date.parse('2026-10-05T10:00:00Z'))
    assert.equal(isoMs(null), null)
    assert.equal(isoMs('not a date'), null)
})

// ── timeline ────────────────────────────────────────────────────────────────
const T = (minute) => Date.UTC(2026, 9, 5, 10, minute)

test('a stopped tree has a fixed range and a bar per node, scaled to the range', () => {
    const items = [
        { id: 'r', start: T(0), end: T(60), working: false },
        { id: 'c', start: T(30), end: T(45), working: false },
    ]
    const early = computeTimeline(items, T(90))
    const late = computeTimeline(items, T(500))
    assert.equal(early.range.spanSeconds, 3600)
    assert.deepEqual(late.range, early.range, 'now never enters a tree with no working node')
    assert.deepEqual(early.geometry.r, { left: 0, width: 100, live: false })
    assert.equal(early.geometry.c.left, 50)
    assert.equal(early.geometry.c.width, 25)
})

test('a working node ends at the range end, which includes now', () => {
    const items = [
        { id: 'r', start: T(0), end: T(10), working: false },
        { id: 'w', start: T(20), end: null, working: true },
    ]
    const { range, geometry } = computeTimeline(items, T(60))
    assert.equal(range.end, T(60))
    assert.equal(geometry.w.live, true)
    assert.ok(Math.abs(geometry.w.left + geometry.w.width - 100) < 1e-9, 'reaches the right end of the track')
})

test('a server timestamp later than the client clock still ends the working bar at the track end', () => {
    const items = [
        { id: 'r', start: T(0), end: T(90), working: false },
        { id: 'w', start: T(80), end: null, working: true },
    ]
    const { range, geometry } = computeTimeline(items, T(60))
    assert.equal(range.end, T(90))
    assert.ok(Math.abs(geometry.w.left + geometry.w.width - 100) < 1e-9)
})

test('a node with no start has no bar and is left out of the range', () => {
    const { range, geometry } = computeTimeline([
        { id: 'a', start: null, end: T(5), working: false },
        { id: 'b', start: T(10), end: T(20), working: false },
    ], T(30))
    assert.equal(geometry.a, undefined)
    assert.equal(range.start, T(10))
    assert.equal(range.spanSeconds, 600)
})

test('no node with a start gives no range', () => {
    assert.deepEqual(computeTimeline([{ id: 'a', start: null, end: null, working: false }], T(1)), { range: null, geometry: {} })
    assert.deepEqual(computeTimeline([], T(1)), { range: null, geometry: {} })
})

test('an end before the start is raised to the start and gets the minimum width', () => {
    const { geometry } = computeTimeline([
        { id: 'r', start: T(0), end: T(100), working: false },
        { id: 'x', start: T(50), end: T(10), working: false },
    ], T(200))
    assert.equal(geometry.x.width, 1.5)
    assert.equal(geometry.x.left, 50)
})

test('a zero range gives every node the full track', () => {
    const { geometry } = computeTimeline([{ id: 'a', start: T(5), end: T(5), working: false }], T(100))
    assert.deepEqual(geometry.a, { left: 0, width: 100, live: false })
})

test('the bar never leaves the track', () => {
    const { geometry } = computeTimeline([
        { id: 'r', start: T(0), end: T(100), working: false },
        { id: 'z', start: T(100), end: T(100), working: false },
    ], T(200))
    assert.ok(geometry.z.left + geometry.z.width <= 100 + 1e-9)
})

// ── annotations ─────────────────────────────────────────────────────────────
test('annotationEntries sorts by key', () => {
    assert.deepEqual(annotationEntries({ b: 1, a: 2, 'a.x': 3 }).map(([k]) => k), ['a', 'a.x', 'b'])
    assert.deepEqual(annotationEntries(null), [])
})

test('annotationValueText formats strings, numbers, booleans, null and objects', () => {
    assert.equal(annotationValueText('x'), 'x')
    assert.equal(annotationValueText(3), '3')
    assert.equal(annotationValueText(false), 'false')
    assert.equal(annotationValueText(null), 'null')
    assert.equal(annotationValueText([1, 'a']), '[1,"a"]')
})

test('dotted keys share parent levels, sorted by segment name', () => {
    const t = buildAnnotationTree({ 'team.name': 'core', 'team.lead': 'alice', 'run.limits.tokens': 200000, role: 'worker' })
    assert.deepEqual(t.map(n => n.name), ['role', 'run', 'team'])
    const team = t.find(n => n.name === 'team')
    assert.deepEqual(team.children.map(n => [n.name, n.values]), [['lead', ['alice']], ['name', ['core']]])
    const run = t.find(n => n.name === 'run')
    assert.equal(run.children[0].name, 'limits')
    assert.deepEqual(run.children[0].children[0], { name: 'tokens', values: ['200000'], children: [] })
})

test('a key with an empty segment is not split, and an object value there stays compact JSON', () => {
    const t = buildAnnotationTree({ 'a..b': 1, '.a': 2, 'a.': 3, 'c..d': { x: 1 } })
    assert.deepEqual(t.map(n => n.name).sort(), ['.a', 'a.', 'a..b', 'c..d'])
    assert.deepEqual(t.find(n => n.name === 'c..d'), { name: 'c..d', values: ['{"x":1}'], children: [] })
})

test('a scalar a and a.b merge into one level that shows both', () => {
    const [a] = buildAnnotationTree({ a: 'x', 'a.b': 'y' })
    assert.deepEqual(a.values, ['x'])
    assert.deepEqual(a.children.map(n => [n.name, n.values]), [['b', ['y']]])
})

test('an object value expands like dotted levels and merges with a dotted key', () => {
    const [a] = buildAnnotationTree({ a: { b: 1 }, 'a.b': 2 })
    assert.equal(a.name, 'a')
    assert.deepEqual(a.values, [])
    assert.deepEqual(a.children[0].values.sort(), ['1', '2'])
})

test('arrays and empty objects stay leaves as JSON text', () => {
    const t = buildAnnotationTree({ list: [1, 2], empty: {} })
    assert.deepEqual(t.map(n => [n.name, n.values]), [['empty', ['{}']], ['list', ['[1,2]']]])
})

// ── card tags: flatten + common prefix ──────────────────────────────────────
const keysOf = entries => entries.map(e => e.key)

test('flattenAnnotations decomposes nested objects into leaf entries (2 and 3 levels, siblings)', () => {
    const flat = flattenAnnotations({ fou: { bar1: 1, cux2: 2 }, run: { limits: { tokens: 5, calls: 3 }, id: 'x' } })
    assert.deepEqual(flat.map(e => [e.key, e.value]), [
        ['fou.bar1', 1], ['fou.cux2', 2], ['run.id', 'x'], ['run.limits.calls', 3], ['run.limits.tokens', 5],
    ])
    assert.deepEqual(flat[3].path, ['run', 'limits', 'calls'])
})

test('flattenAnnotations keeps arrays, null and empty objects as leaves', () => {
    const flat = flattenAnnotations({ list: [1, 2], nothing: null, empty: {}, deep: { empty: {} } })
    assert.deepEqual(flat.map(e => [e.key, e.value]), [['deep.empty', {}], ['empty', {}], ['list', [1, 2]], ['nothing', null]])
})

test('flattenAnnotations: a dotted key and a nested object give the same path', () => {
    const flat = flattenAnnotations({ 'a.b': 1, a: { c: 2 } })
    assert.deepEqual(flat.map(e => [e.path, e.value]), [[['a', 'b'], 1], [['a', 'c'], 2]])
})

test('flattenAnnotations does not split keys with an empty segment, top level or nested', () => {
    const flat = flattenAnnotations({ 'a..b': 1, '.a': 2, 'a.': 3, n: { 'x..y': 4, '.z': 5 } })
    assert.deepEqual(flat.map(e => e.path), [['.a'], ['a.'], ['a..b'], ['n', '.z'], ['n', 'x..y']])
})

test('flattenAnnotations sorts by full key and tolerates invalid input', () => {
    assert.deepEqual(keysOf(flattenAnnotations({ b: 1, a: { z: 1, y: 1 }, 'a.x': 1 })), ['a.x', 'a.y', 'a.z', 'b'])
    assert.deepEqual(flattenAnnotations(null), [])
    assert.deepEqual(flattenAnnotations([1]), [])
})

test('splitCommonPrefix: prefix shared by all entries is removed from each key', () => {
    const r = splitCommonPrefix(flattenAnnotations({ multi_review: { schema: 1, skill: 's', job: 'j' } }))
    assert.equal(r.prefix, 'multi_review')
    assert.deepEqual(r.entries.map(e => [e.key, e.fullKey]), [
        ['job', 'multi_review.job'], ['schema', 'multi_review.schema'], ['skill', 'multi_review.skill'],
    ])
    assert.equal(splitCommonPrefix(flattenAnnotations({ fou: { bar1: 1, cux2: 2 } })).prefix, 'fou')
})

test('splitCommonPrefix: a partially shared or absent prefix gives none', () => {
    assert.equal(splitCommonPrefix(flattenAnnotations({ 'a.x': 1, 'b.y': 2 })).prefix, null)
    assert.equal(splitCommonPrefix(flattenAnnotations({ 'a.x': 1, 'a.y': 2, b: 3 })).prefix, null)
})

test('splitCommonPrefix: the prefix always leaves one segment to every entry', () => {
    const single = splitCommonPrefix(flattenAnnotations({ 'team.lead': 'alice' }))
    assert.equal(single.prefix, 'team')
    assert.deepEqual(single.entries.map(e => [e.key, e.value]), [['lead', 'alice']])
    assert.equal(splitCommonPrefix(flattenAnnotations({ role: 'worker' })).prefix, null)
    const mixed = splitCommonPrefix(flattenAnnotations({ 'a.b': 1, 'a.b.c': 2 }))
    assert.equal(mixed.prefix, 'a')
    assert.deepEqual(keysOf(mixed.entries), ['b', 'b.c'])
})

test('splitCommonPrefix: empty input gives no entries and no prefix', () => {
    assert.deepEqual(splitCommonPrefix([]), { prefix: null, entries: [] })
    assert.deepEqual(splitCommonPrefix(flattenAnnotations(undefined)), { prefix: null, entries: [] })
})

// ── model label ─────────────────────────────────────────────────────────────
test('agentModelLabel reads family and version, as the session header does', () => {
    assert.equal(agentModelLabel({ raw: 'x', family: 'opus', version: '4.5' }), 'opus 4.5')
})

test('agentModelLabel is null without a model, or without family or version', () => {
    assert.equal(agentModelLabel(null), null)
    assert.equal(agentModelLabel({ raw: 'gpt-x', family: null, version: null }), null)
})

// ── cumulativeSeconds ───────────────────────────────────────────────────────
test('cumulativeSeconds sums the durations of stopped nodes', () => {
    assert.equal(cumulativeSeconds([
        { id: 'a', start: T(0), end: T(10), working: false },
        { id: 'b', start: T(20), end: T(25), working: false },
    ], T(100)), 900)
})

test('cumulativeSeconds counts a working node up to the range end', () => {
    assert.equal(cumulativeSeconds([
        { id: 'a', start: T(0), end: null, working: true },
        { id: 'b', start: T(0), end: T(1), working: false },
    ], T(10)), 660)
})

test('cumulativeSeconds skips a node with no start and the excluded id', () => {
    assert.equal(cumulativeSeconds([
        { id: 'a', start: null, end: T(5), working: false },
        { id: 'cur', start: T(0), end: T(50), working: false },
        { id: 'b', start: T(0), end: T(1), working: false },
    ], T(100), 'cur'), 60)
})

test('cumulativeSeconds raises an end before the start to the start and sums overlaps', () => {
    assert.equal(cumulativeSeconds([
        { id: 'a', start: T(5), end: T(1), working: false },
        { id: 'b', start: T(0), end: T(2), working: false },
        { id: 'c', start: T(1), end: T(3), working: false },
    ], T(10)), 240)
})

test('cumulativeSeconds is null when no node qualifies', () => {
    assert.equal(cumulativeSeconds([], T(1)), null)
    assert.equal(cumulativeSeconds([{ id: 'a', start: null, end: null, working: false }], T(1)), null)
    assert.equal(cumulativeSeconds([{ id: 'a', start: T(0), end: T(1), working: false }], T(1), 'a'), null)
})
