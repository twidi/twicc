import { toRaw } from 'vue'
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
    // The store invokes this projection explicitly. Read raw metadata for the bulk calculation;
    // do not pay proxy access/spread costs for every historical item on each tool update.
    const previousInput = previous
    items = toRaw(items)
    previous = toRaw(previous).map(toRaw)
    expandedGroups = toRaw(expandedGroups)
    internalExpandedGroups = toRaw(internalExpandedGroups)
    // Lines that open a new partition (an event renders before them).
    const cutLines = new Set()
    for (const boundary of boundaries) if (boundary.beforeLine != null) cutLines.add(boundary.beforeLine)
    const previousByKey = new Map(), previousByHead = new Map(), overlapsByHead = new Map()
    for (const segment of previous) {
        previousByKey.set(segment.key, segment)
        if (!previousByHead.has(segment.originalHead)) previousByHead.set(segment.originalHead, [])
        previousByHead.get(segment.originalHead).push(segment)
    }
    function previousSegment(key, originalHead, lines) {
        const exact = previousByKey.get(key)
        if (exact) return exact
        // Build the overlap index only for a group whose boundaries actually changed.
        if (!overlapsByHead.has(originalHead)) {
            const byLine = new Map()
            for (const [order, segment] of (previousByHead.get(originalHead) ?? []).entries()) {
                for (const line of segment.members) if (!byLine.has(line)) byLine.set(line, { segment, order })
            }
            overlapsByHead.set(originalHead, byLine)
        }
        let first = null
        for (const line of lines) {
            const candidate = overlapsByHead.get(originalHead).get(line)
            if (candidate && (!first || candidate.order < first.order)) first = candidate
        }
        return first?.segment
    }
    // Each group: its member items, with their position in `items` and their partition.
    const groups = new Map()
    let part = 0
    function member(head, item, index) {
        if (head == null) return
        let group = groups.get(head)
        if (!group) groups.set(head, group = { items: [], indexes: [], parts: [] })
        // Each source item contributes at most its prefix and suffix, consecutively.
        if (group.items.at(-1) === item) return
        group.items.push(item)
        group.indexes.push(index)
        group.parts.push(part)
    }
    // One pass over the items (the per-recompute hot path: no per-item map or copy).
    // An item that a boundary renders before opens a new partition, itself included.
    let rawItems = items
    for (let i = 0; i < items.length; i++) {
        const item = toRaw(items[i])
        if (item !== items[i]) {
            if (rawItems === items) rawItems = items.slice()
            rawItems[i] = item
        }
        if (cutLines.has(item.line_num)) part++
        member(item.group_head, item, i)
        if (item.display_level === DISPLAY_LEVEL.ALWAYS && item.group_tail != null) member(item.line_num, item, i)
    }
    items = rawItems
    const segments = [], segmentIndexes = []
    const expandedSet = new Set(expandedGroups)
    for (const [originalHead, group] of groups) {
        const members = group.items
        const owner = members.find(item => item.line_num === originalHead && item.display_level === DISPLAY_LEVEL.ALWAYS)
        const content = owner ? getParsedContent(owner)?.message?.content : null
        const suffixStart = getPrefixSuffixBoundaries(content, null, owner?.group_tail).suffixStartIndex
        const initiallyExpanded = expandedSet.has(originalHead)
            || (suffixStart != null && (internalExpandedGroups[originalHead] ?? []).includes(suffixStart))
        const partitions = new Map()
        for (let m = 0; m < members.length; m++) {
            let partition = partitions.get(group.parts[m])
            if (!partition) partitions.set(group.parts[m], partition = { items: [], indexes: [] })
            partition.items.push(members[m])
            partition.indexes.push(group.indexes[m])
        }
        for (const { items: partition, indexes } of partitions.values()) {
            const head = partition[0].line_num, tail = partition.at(-1).line_num
            const key = `tools:${originalHead}:${head}`
            const lines = partition.map(item => item.line_num)
            const old = previousSegment(key, originalHead, lines)
            const expanded = old?.expanded ?? initiallyExpanded
            const internal = partition.length === 1 && partition[0].display_level === DISPLAY_LEVEL.ALWAYS
            // Synthetic streaming lines participate in display groups, but have no source comments.
            let rangeHead = null, rangeTail = null
            for (const line of lines) if (line > 0) {
                rangeHead = rangeHead === null ? line : Math.min(rangeHead, line)
                rangeTail = rangeTail === null ? line : Math.max(rangeTail, line)
            }
            const range = rangeHead === null ? null : old?.range?.head === rangeHead && old.range.tail === rangeTail
                ? old.range : { head: rangeHead, tail: rangeTail }
            segments.push({ key, originalHead, head, tail, members: lines, expanded, internal, range })
            segmentIndexes.push(indexes)
        }
    }

    const internalTransfers = [], retiredRemaining = []
    const itemByLine = retired.length ? new Map(items.map(item => [item.line_num, item])) : null
    for (const transfer of retired) {
        const item = itemByLine.get(transfer.realLineNum)
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

    // Stable controls avoid invalidating subscribers on unrelated tool/status updates.
    for (let i = 0; i < segments.length; i++) {
        const segment = segments[i], old = previousByKey.get(segment.key)
        if (old && old.tail === segment.tail && old.internal === segment.internal
            && old.expanded === segment.expanded && old.range === segment.range
            && old.members.length === segment.members.length
            && segment.members.every((line, index) => line === old.members[index])) segments[i] = old
    }

    // Projected copies, by position in `items` (an item with a prefix and a suffix is in two segments).
    const projected = new Array(items.length)
    function clone(index) {
        if (!projected[index]) {
            const original = items[index], copy = { ...original }
            const parsed = getParsedContent(original)
            if (parsed !== null) setParsedContent(copy, parsed)
            projected[index] = copy
        }
        return projected[index]
    }
    for (let s = 0; s < segments.length; s++) {
        const segment = segments[s]
        for (const index of segmentIndexes[s]) {
            const original = items[index], item = clone(index)
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
    const stableSegments = segments.length === previous.length && segments.every((segment, i) => segment === previous[i]) ? previousInput : segments
    return { items: items.map((item, index) => projected[index] ?? item), segments: stableSegments,
        expandedHeads: [...new Set([...expandedGroups.filter(head => head < 0 && !groups.has(head)),
            ...segments.filter(s => s.expanded && !s.internal).map(s => s.head)])], internalTransfers, retiredRemaining }
}
