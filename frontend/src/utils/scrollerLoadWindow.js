import { hasContent } from './parsedContent.js'

/** Keep the conservative inclusive endpoint used by conversation gap loading. */
export function collectMissingScrollerLines(visualItems, { start, end }, buffer) {
    const lines = []
    for (let i = Math.max(0, start - buffer); i <= Math.min(visualItems.length - 1, end + buffer); i++) {
        const item = visualItems[i]
        if (item && !item.isDaySeparator && !hasContent(item)) lines.push(item.lineNum)
    }
    return lines
}

export function sameScrollerLoadCandidates(left, right) {
    return left.length === right.length && left.every((line, index) => line === right[index])
}

export function hasScrollerPaginationProgress(before, after) {
    return before.cursor !== after.cursor || [...after.ids].some(id => !before.ids.has(id))
}
