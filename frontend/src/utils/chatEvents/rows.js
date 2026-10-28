import { toRaw } from 'vue'
import { hasContent } from '../parsedContent.js'
import { dayKeyOfTimestamp, formatDaySeparatorLabel, refreshDayKeysTimeZone } from '../date.js'

export const isSessionEvent = item => item?.isSessionEvent === true
export const isTranscriptLoadCandidate = item => typeof item?.lineNum === 'number' && item.lineNum > 0 && !hasContent(item)

export function mergeSessionEventRows({ sourceItems, messageRows, boundaries }) {
    if (!boundaries.length) return messageRows
    const indices = new Map(toRaw(sourceItems).map((item, i) => [toRaw(item).line_num, i]))
    const result = []
    let boundaryIndex = 0
    function appendBoundary(boundary) {
        for (const event of boundary.events) result.push({ lineNum: `event:${event.id}`, isSessionEvent: true, event, timestamp: event.occurred_at })
    }
    for (const row of messageRows) {
        const rank = indices.get(row.lineNum) ?? sourceItems.length
        while (boundaryIndex < boundaries.length && (indices.get(boundaries[boundaryIndex].beforeLine) ?? sourceItems.length) <= rank) {
            appendBoundary(boundaries[boundaryIndex++])
        }
        result.push(row)
    }
    while (boundaryIndex < boundaries.length) appendBoundary(boundaries[boundaryIndex++])
    return result
}

/** Calendar context follows displayed portions. Events never modify logical block flags. */
export function insertEventDaySeparators(rows) {
    refreshDayKeysTimeZone()
    const result = []
    let day = null
    for (const row of rows) {
        const nextDay = dayKeyOfTimestamp(row.timestamp)
        if (day && nextDay && nextDay !== day) {
            result.push({ lineNum: `daysep-${row.lineNum}`, isDaySeparator: true,
                dayKey: nextDay, dayLabel: formatDaySeparatorLabel(Date.parse(row.timestamp)) })
        }
        if (nextDay) day = nextDay
        result.push(row)
    }
    return result
}
