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
    items = toRaw(items).map(toRaw)
    previous = toRaw(previous).map(toRaw)
    expandedGroups = toRaw(expandedGroups)
    internalExpandedGroups = toRaw(internalExpandedGroups)
    const indices = new Map(items.map((item, i) => [item.line_num, i]))
    const cuts = [...new Set(boundaries.map(b => indices.get(b.beforeLine)).filter(i => i !== undefined))].sort((a, b) => a - b)
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
    const groups = new Map()
    function member(head, item) {
        if (head == null) return
        if (!groups.has(head)) groups.set(head, [])
        const group = groups.get(head)
        // Each source item contributes at most its prefix and suffix, consecutively.
        if (group.at(-1) !== item) group.push(item)
    }
    const partitionAt = new Int32Array(items.length)
    let part = 0
    for (let i = 0; i < items.length; i++) {
        const item = items[i]
        while (part < cuts.length && cuts[part] <= i) part++
        partitionAt[i] = part
        member(item.group_head, item)
        if (item.display_level === DISPLAY_LEVEL.ALWAYS && item.group_tail != null) member(item.line_num, item)
    }
    const segments = []
    const expandedSet = new Set(expandedGroups)
    for (const [originalHead, members] of groups) {
        const owner = members.find(item => item.line_num === originalHead && item.display_level === DISPLAY_LEVEL.ALWAYS)
        const content = owner ? getParsedContent(owner)?.message?.content : null
        const suffixStart = getPrefixSuffixBoundaries(content, null, owner?.group_tail).suffixStartIndex
        const initiallyExpanded = expandedSet.has(originalHead)
            || (suffixStart != null && (internalExpandedGroups[originalHead] ?? []).includes(suffixStart))
        const partitions = new Map()
        for (const item of members) {
            const part = partitionAt[indices.get(item.line_num)]
            if (!partitions.has(part)) partitions.set(part, [])
            partitions.get(part).push(item)
        }
        for (const partition of partitions.values()) {
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
        }
    }

    const internalTransfers = [], retiredRemaining = []
    for (const transfer of retired) {
        const item = items[indices.get(transfer.realLineNum)]
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
    const stableSegments = segments.length === previous.length && segments.every((segment, i) => segment === previous[i]) ? previousInput : segments
    return { items: items.map(item => projected.get(item.line_num) ?? item), segments: stableSegments,
        expandedHeads: [...new Set([...expandedGroups.filter(head => head < 0 && !groups.has(head)),
            ...segments.filter(s => s.expanded && !s.internal).map(s => s.head)])], internalTransfers, retiredRemaining }
}
