/** Admit one measured lifecycle notification, then only changed four-index tuples. */
export function createVirtualScrollRangeUpdates() {
    let lastEpoch = -1
    let last = null
    return {
        admit(payload, { epoch }) {
            if (epoch < lastEpoch) return false
            const next = [payload.startIndex, payload.endIndex, payload.visibleStartIndex, payload.visibleEndIndex]
            const changed = !last || next.some((value, index) => value !== last[index])
            if (epoch === lastEpoch && !changed) return false
            lastEpoch = epoch
            last = next
            return true
        },
    }
}
