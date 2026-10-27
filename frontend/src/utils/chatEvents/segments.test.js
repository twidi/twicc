import { getParsedContent } from '../parsedContent.js'
import test from 'node:test'
import assert from 'node:assert/strict'
import { projectEventSegments, setEventSegmentExpanded } from './segments.js'
import { computeVisualItems } from '../visualItems.js'
const item = (line_num, display_level, group_head = null, group_tail = null, blocks = null) => ({ line_num, display_level, group_head, group_tail,
    kind: display_level === 1 ? 'assistant_message' : 'content_items', content: JSON.stringify({ message: { role: 'assistant', content: blocks ?? [{ type: 'thinking', thinking: 'work' }] } }) })
const cut = beforeLine => ({ beforeLine, events: [{ id: beforeLine }] })
const tools = () => [ { ...item(1, 1), kind: 'user_message' }, ...Array.from({ length: 20 }, (_, i) => item(i + 2, 2, 2, 21)), item(22, 1) ]
const project = (items, boundaries, previous = [], expandedGroups = [], extra = {}) => projectEventSegments({ items, boundaries, previous, expandedGroups, internalExpandedGroups: {}, ...extra })

test('twenty tools split into independent ten-item controls without mutating source groups', () => {
    const items = tools(); items.forEach(getParsedContent); items.forEach(Object.freeze); Object.freeze(items)
    const result = project(items, [cut(12)])
    assert.deepEqual(result.segments.map(s => [s.head, s.tail, s.members.length]), [[2, 11, 10], [12, 21, 10]])
    assert.deepEqual(computeVisualItems(result.items, 'simplified', result.expandedHeads).map(r => r.lineNum), [1, 2, 12, 22])
    const previous = setEventSegmentExpanded(result.segments, 2, true)
    const open = project(items, [cut(12)], previous)
    assert.deepEqual(open.segments.map(s => s.expanded), [true, false])
    assert.equal(computeVisualItems(open.items, 'simplified', open.expandedHeads).length, 13)
    assert.equal(items[10].group_tail, 21)
})

test('split children inherit only their overlapping parent, across recovery and mode changes', () => {
    const first = project(tools(), [cut(12)], [], [2])
    const mixed = setEventSegmentExpanded(first.segments, 12, false)
    const next = project(tools(), [cut(12), cut(17)], mixed)
    assert.deepEqual(next.segments.map(s => s.expanded), [true, false, false])
    for (const mode of ['normal', 'debug', 'conversation', 'simplified']) {
        const again = project(tools(), [cut(12), cut(17)], next.segments)
        computeVisualItems(again.items, mode, again.expandedHeads)
        assert.deepEqual(again.segments.map(s => s.expanded), [true, false, false])
    }
})

test('consecutive events do not add empty groups; extending a group retains its expansion', () => {
    const original = project(tools(), [cut(12), cut(12)])
    assert.equal(original.segments.length, 2)
    const previous = setEventSegmentExpanded(original.segments, 12, true)
    const items = tools(); items.splice(21, 0, item(23, 2, 2, 23))
    assert.deepEqual(project(items, [cut(12)], previous).segments.map(s => s.expanded), [false, true])
})

const bothSides = () => [item(1, 2, 1, 2), item(2, 1, 1, 3, [{ type: 'thinking' }, { type: 'text', text: 'answer' }, { type: 'tool_use' }]), item(3, 2, 2, 3)]
for (const openHead of [1, 2]) test(`orphan prefix/suffix retains independent state from original group ${openHead}`, () => {
    const items = bothSides()
    const result = project(items, [cut(2), cut(3)], [], [openHead])
    const controls = result.items[1].eventInternalControls
    assert.equal(controls[0].expanded, openHead === 1)
    assert.equal(controls[2].expanded, openHead === 2)
    const changed = result.segments.map(s => s.key === controls[0].segmentKey ? { ...s, expanded: !s.expanded } : s)
    const again = project(items, [cut(2), cut(3)], changed)
    assert.equal(again.items[1].eventInternalControls[0].expanded, openHead !== 1)
    assert.equal(again.items[1].eventInternalControls[2].expanded, openHead === 2)
    assert.equal(again.items[1].group_head, null); assert.equal(again.items[1].group_tail, null)
})

test('an open internal suffix seeds a newly external segment and range metadata reaches visual rows', () => {
    const result = project(bothSides(), [cut(1)], [], [], { internalExpandedGroups: { 2: [2] } })
    const suffix = result.segments.find(s => s.originalHead === 2)
    assert.equal(suffix.expanded, true)
    const row = computeVisualItems(result.items, 'simplified', result.expandedHeads).find(r => r.lineNum === 2)
    assert.deepEqual(row.suffixSegmentRange, { head: 2, tail: 3 })
})

test('retired thinking targets the prefix of a two-sided ALWAYS, never its suffix sibling', () => {
    const result = project(bothSides(), [cut(1)], [], [], { retired: [{ streamingLineNum: -10, realLineNum: 2, contentIndex: 0, expanded: true }] })
    assert.deepEqual(result.segments.map(s => s.expanded), [true, false])
    assert.deepEqual(result.retiredRemaining, [])
})

test('retirement seeds an internal group, consumes a missing group, and waits for missing real metadata', () => {
    const retired = [{ streamingLineNum: -10, realLineNum: 2, contentIndex: 0, expanded: true }]
    const internal = project([item(2, 1, null, null, [{ type: 'thinking' }, { type: 'text' }])], [cut(2)], [], [], { retired })
    assert.deepEqual(internal.internalTransfers, [{ lineNum: 2, startIndex: 0 }]); assert.deepEqual(internal.retiredRemaining, [])
    const noGroup = project([item(2, 1, null, null, [{ type: 'text' }])], [cut(2)], [], [], { retired })
    assert.deepEqual(noGroup.internalTransfers, []); assert.deepEqual(noGroup.retiredRemaining, [])
    assert.equal(project([], [cut(2)], [], [], { retired }).retiredRemaining.length, 1)
})

test('a severed connected prefix keeps tool/result source identities and its parsed blocks intact', () => {
    const items = bothSides(); items.forEach(getParsedContent); const before = JSON.stringify(items)
    const result = project(items, [cut(2)])
    assert.deepEqual(result.items.map(i => i.line_num), [1, 2, 3])
    assert.equal(result.items[0].group_tail, 1); assert.equal(result.items[1].group_head, null)
    assert.equal(JSON.stringify(items), before)
})
