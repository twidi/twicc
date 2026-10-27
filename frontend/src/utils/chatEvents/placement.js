import { getParsedContent } from '../parsedContent.js'
import { compareNewestFirst } from '../timeline/eventTime.js'

/** Normalize wire/provider ISO timestamps without losing their fractional precision. */
export function normalizeChatTimestamp(value) {
    if (typeof value !== 'string') return null
    const match = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,6}))?(Z|[+-]\d\d:\d\d)$/.exec(value)
    if (!match) return null
    const seconds = Date.parse(match[1] + match[3])
    return Number.isFinite(seconds) ? BigInt(seconds) * 1000n + BigInt((match[2] ?? '').padEnd(6, '0')) : null
}

/** Source order is authoritative. Missing/regressing times use its monotonic envelope. */
export function placeSessionEvents(items, events) {
    const sorted = [...events].sort((a, b) => compareNewestFirst(b, a))
    if (!sorted.length) return []
    const times = items.map(item => normalizeChatTimestamp(item.timestamp))
    let previous = times.find(time => time !== null) ?? null
    if (previous === null) return [{ beforeLine: items[0]?.line_num ?? null, events: sorted }]
    const boundaries = []
    let eventIndex = 0
    for (let i = 0; i < items.length; i++) {
        const time = times[i]
        if (time !== null && time > previous) previous = time
        const atBoundary = []
        while (eventIndex < sorted.length) {
            const eventTime = normalizeChatTimestamp(sorted[eventIndex].occurred_at)
            if (eventTime !== null && eventTime > previous) break
            atBoundary.push(sorted[eventIndex++])
        }
        if (atBoundary.length) boundaries.push({ beforeLine: items[i].line_num, events: atBoundary })
    }
    if (eventIndex < sorted.length) boundaries.push({ beforeLine: null, events: sorted.slice(eventIndex) })
    return boundaries
}

/** The existing bounded /compact correction, shared by source placement and visual output. */
export function orderCompactItems(items, compacted) {
    if (!compacted) return items
    let result = items
    for (let i = 0; i < result.length; i++) {
        if (result[i].kind !== 'compact_summary') continue
        for (let j = i + 1; j < Math.min(i + 10, result.length); j++) {
            if (result[j].kind !== 'user_message') continue
            const text = getParsedContent(result[j])?.message?.content
            if (typeof text === 'string' && text.includes('<command-name>/compact</command-name>')) {
                if (result === items) result = [...items]
                const [moved] = result.splice(j, 1)
                result.splice(i, 0, moved)
            }
            break
        }
    }
    return result
}
