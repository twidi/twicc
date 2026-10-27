import { DISPLAY_LEVEL } from '../../constants.js'
import { getParsedContent, setParsedContent } from '../parsedContent.js'
import { getPrefixSuffixBoundaries, getInternalCollapsibleGroups } from '../contentVisibility.js'

export function setEventSegmentExpanded(segments, head, expanded) {
    return segments.map(segment => segment.head === head && !segment.internal && segment.expanded !== expanded
        ? { ...segment, expanded } : segment)
}

/** Project provider groups without changing provider metadata or source identities. */
export function projectEventSegments({ items, boundaries, previous = [], expandedGroups = [], internalExpandedGroups = {}, retired = [] }) {
    if (!boundaries.length && !previous.length && !retired.length) {
        return { items, segments: [], expandedHeads: expandedGroups, internalTransfers: [], retiredRemaining: [] }
    }
    const indices = new Map(items.map((item, i) => [item.line_num, i]))
    const cuts = [...new Set(boundaries.map(b => indices.get(b.beforeLine)).filter(i => i !== undefined))].sort((a, b) => a - b)
    const groups = new Map()
    function member(head, item) {
        if (head == null) return
        if (!groups.has(head)) groups.set(head, [])
        const group = groups.get(head)
        if (!group.includes(item)) group.push(item)
    }
    for (const item of items) {
        member(item.group_head, item)
        if (item.display_level === DISPLAY_LEVEL.ALWAYS && item.group_tail != null) member(item.line_num, item)
    }
    const segments = []
    for (const [originalHead, members] of groups) {
        const partitions = new Map()
        for (const item of members) {
            const part = cuts.filter(cut => cut <= indices.get(item.line_num)).length
            if (!partitions.has(part)) partitions.set(part, [])
            partitions.get(part).push(item)
        }
        for (const partition of partitions.values()) {
            const head = partition[0].line_num, tail = partition.at(-1).line_num
            const key = `tools:${originalHead}:${head}`
            const lines = partition.map(item => item.line_num)
            const old = previous.find(s => s.key === key)
                ?? previous.find(s => s.originalHead === originalHead && s.members.some(line => lines.includes(line)))
            const owner = members.find(item => item.line_num === originalHead && item.display_level === DISPLAY_LEVEL.ALWAYS)
            const content = owner ? getParsedContent(owner)?.message?.content : null
            const suffixStart = getPrefixSuffixBoundaries(content, null, owner?.group_tail).suffixStartIndex
            const expanded = old?.expanded ?? (expandedGroups.includes(originalHead)
                || (suffixStart != null && (internalExpandedGroups[originalHead] ?? []).includes(suffixStart)))
            const internal = partition.length === 1 && partition[0].display_level === DISPLAY_LEVEL.ALWAYS
            // Synthetic streaming lines participate in display groups, but have no source comments.
            const sourceLines = lines.filter(line => line > 0)
            const rangeHead = sourceLines.length ? Math.min(...sourceLines) : null
            const rangeTail = sourceLines.length ? Math.max(...sourceLines) : null
            const range = rangeHead === null ? null : old?.range?.head === rangeHead && old.range.tail === rangeTail
                ? old.range : { head: rangeHead, tail: rangeTail }
            segments.push({ key, originalHead, head, tail, members: lines, expanded, internal, range })
        }
    }

    const internalTransfers = [], retiredRemaining = []
    for (const transfer of retired) {
        const item = items.find(item => item.line_num === transfer.realLineNum)
        if (!item) { retiredRemaining.push(transfer); continue }
        if (!transfer.expanded) continue
        let originalHead = item.group_head
        if (item.display_level === DISPLAY_LEVEL.ALWAYS) {
            const content = getParsedContent(item)?.message?.content
            const bounds = getPrefixSuffixBoundaries(content, item.group_head, item.group_tail)
            if (transfer.contentIndex != null && bounds.prefixEndIndex != null && transfer.contentIndex <= bounds.prefixEndIndex) originalHead = item.group_head
            else if (transfer.contentIndex != null && bounds.suffixStartIndex != null && transfer.contentIndex >= bounds.suffixStartIndex) originalHead = item.line_num
            else {
                const group = getInternalCollapsibleGroups(content, bounds).find(group => transfer.contentIndex >= group.startIndex && transfer.contentIndex <= group.endIndex)
                if (group) internalTransfers.push({ lineNum: item.line_num, startIndex: group.startIndex })
                continue
            }
        }
        const destination = segments.find(s => s.originalHead === originalHead && s.members.includes(item.line_num))
        if (destination) destination.expanded = true
    }

    const projected = new Map()
    function clone(item) {
        if (!projected.has(item.line_num)) {
            const copy = { ...item }
            const parsed = getParsedContent(item)
            if (parsed !== null) setParsedContent(copy, parsed)
            projected.set(item.line_num, copy)
        }
        return projected.get(item.line_num)
    }
    for (const segment of segments) {
        for (const line of segment.members) {
            const original = items[indices.get(line)], item = clone(original)
            if (original.display_level === DISPLAY_LEVEL.COLLAPSIBLE) {
                item.group_head = segment.head
                item.group_tail = segment.tail
                item.segmentRange = segment.range
                continue
            }
            const prefix = original.group_head === segment.originalHead
            const suffix = original.line_num === segment.originalHead && original.group_tail != null
            if (prefix) { item.group_head = segment.internal ? null : segment.head; item.prefixSegmentRange = segment.range }
            if (suffix) { item.group_tail = segment.internal ? null : segment.tail; item.suffixSegmentRange = segment.range }
            if (segment.internal) {
                const content = getParsedContent(original)?.message?.content
                const bounds = getPrefixSuffixBoundaries(content, original.group_head, original.group_tail)
                const startIndex = prefix && bounds.prefixEndIndex != null ? 0 : suffix ? bounds.suffixStartIndex : null
                if (startIndex != null) {
                    item.eventInternalControls = { ...item.eventInternalControls, [startIndex]: {
                        segmentKey: segment.key, expanded: segment.expanded, range: segment.range,
                    } }
                }
            }
        }
    }
    return { items: items.map(item => projected.get(item.line_num) ?? item), segments,
        expandedHeads: [...new Set([...expandedGroups.filter(head => head < 0 && !groups.has(head)),
            ...segments.filter(s => s.expanded && !s.internal).map(s => s.head)])], internalTransfers, retiredRemaining }
}
