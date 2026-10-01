// Sidebar list exits (visual refresh retouches): a row that leaves the list because its session was
// archived stays in place for the length of its exit animation (it fades, slides out and folds its
// height, so the rows below glide up), instead of vanishing and making the list jump. Pure helpers
// here; the Vue side is composables/useListExit.js, the animation is .list-leaving (styles/motion.css).
export const LIST_EXIT_DURATION_MS = 260

/**
 * The rows of `previousItems` that must exit: absent from `nextKeys`, eligible, on screen
 * (`range` is { start, end } with `end` exclusive, in `previousItems` indices), and not already exiting
 * (`skipKeys`). Each carries the key of the nearest earlier row that stays (`afterKey`, null when none),
 * the anchor it is put back after by mergeExits. List order.
 */
export function pickExits({ previousItems, nextKeys, getKey, range, isEligible, skipKeys = new Set() }) {
    if (!range) return []
    const exits = []
    let anchor = null
    previousItems.forEach((item, index) => {
        const key = getKey(item)
        if (nextKeys.has(key)) {
            anchor = key
            return
        }
        if (skipKeys.has(key) || index < range.start || index >= range.end || !isEligible(item)) return
        exits.push({ item, key, afterKey: anchor })
    })
    return exits
}

/** `items` with each exiting row put back after its anchor (the top when it has none or it left too). */
export function mergeExits(items, exits, getKey) {
    if (!exits.length) return items
    const present = new Set(items.map(getKey))
    const byAnchor = new Map()
    for (const exit of exits) {
        const anchor = exit.afterKey !== null && present.has(exit.afterKey) ? exit.afterKey : null
        if (!byAnchor.has(anchor)) byAnchor.set(anchor, [])
        byAnchor.get(anchor).push(exit.item)
    }
    const merged = [...(byAnchor.get(null) ?? [])]
    for (const item of items) {
        merged.push(item)
        const after = byAnchor.get(getKey(item))
        if (after) merged.push(...after)
    }
    return merged
}
