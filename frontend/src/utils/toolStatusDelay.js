// Status-line tool delay: a tool only counts for the "<Provider> is …ing" line once it has been running for
// `delayMs`; before that the line keeps its previous phrase ("thinking"), so fast tools never flash a verb.

/**
 * @param {Array<{id?: string, name?: string}>} tools - the tools currently running.
 * @param {Map<string, number>} firstSeen - id → first time seen (ms). Mutated: new ids are added, ids
 *   no longer running are dropped.
 * @param {number} now - current time (ms).
 * @param {number} delayMs - how long a tool must have been running to be shown.
 * @returns {{tools: Array, nextCheckInMs: number|null}} the tools old enough to show, and when the next
 *   hidden one becomes old enough (null when none is waiting).
 */
export function settleTools(tools, firstSeen, now, delayMs) {
    const keyOf = (tool) => tool.id || tool.name
    const running = new Set()
    for (const tool of tools) {
        const key = keyOf(tool)
        running.add(key)
        if (!firstSeen.has(key)) firstSeen.set(key, now)
    }
    for (const key of [...firstSeen.keys()]) {
        if (!running.has(key)) firstSeen.delete(key)
    }
    const settled = []
    let nextCheckInMs = null
    for (const tool of tools) {
        const remaining = firstSeen.get(keyOf(tool)) + delayMs - now
        if (remaining <= 0) {
            settled.push(tool)
        } else if (nextCheckInMs === null || remaining < nextCheckInMs) {
            nextCheckInMs = remaining
        }
    }
    return { tools: settled, nextCheckInMs }
}
