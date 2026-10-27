import { chooseAnchor, survivingAnchor } from '../timeline/scrollKeeper.js'

function controlState(row) {
    return JSON.stringify([row.groupHead, row.groupTail, row.isExpanded, row.prefixExpanded,
        row.suffixExpanded, row.eventInternalControls])
}

/** Payload/detail height changes remain owned by the scroller's ResizeObserver. */
export function eventStructureChanged(previous, next) {
    if (!previous.some(row => row.isSessionEvent) && !next.some(row => row.isSessionEvent)) return false
    return previous.length !== next.length || previous.some((row, index) => row.lineNum !== next[index].lineNum
        || controlState(row) !== controlState(next[index]))
}

export function captureEventScroll({ scroller, rows, intent }) {
    if (!scroller) return null
    const contentKeys = new Set(rows.filter(row => !row.isDaySeparator).map(row => row.lineNum))
    return { anchor: chooseAnchor(scroller.getScrollAnchor(), scroller.positions, key => contentKeys.has(key)),
        positions: scroller.positions, atBottom: scroller.isAtBottom(), intent, revision: scroller.getScrollRevision() }
}

export function planEventScrollRestore({ snapshot, rows, positions, intent, revision }) {
    const none = { action: 'none' }
    if (!snapshot || snapshot.intent !== intent || snapshot.revision !== revision || !rows.length) return none
    if (snapshot.atBottom) return { action: 'bottom' }
    if (!snapshot.anchor) return none
    const indices = new Map(rows.map((row, index) => [row.lineNum, index]))
    const index = indices.get(snapshot.anchor.key)
    if (index === undefined) {
        const contentKeys = new Set(rows.filter(row => !row.isDaySeparator).map(row => row.lineNum))
        const anchor = survivingAnchor(snapshot.anchor, snapshot.positions, indices, key => contentKeys.has(key))
        return anchor ? { action: 'anchor', anchor } : none
    }
    const oldTop = snapshot.positions?.[snapshot.anchor.index]?.top
    const newTop = positions?.[index]?.top
    if (index === snapshot.anchor.index && oldTop === newTop) return none
    return { action: 'anchor', anchor: { ...snapshot.anchor, index } }
}
