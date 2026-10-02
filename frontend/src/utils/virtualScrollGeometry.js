/**
 * Cache cumulative positions for an immutable ordered-key snapshot.
 * Callers own height storage and invalidate keys before reconciling changes.
 * Height updates read only the affected suffix. Copying prefix references can
 * still cost O(N); a new key snapshot also requires structural reconciliation.
 */
export function createVirtualScrollGeometry({ minItemHeight }) {
    let currentKeys = null
    let keyIndices = new Map()
    let hasDuplicates = false
    let dirtyIndex = Infinity
    let positions = Object.freeze([])

    function invalidateKey(key) {
        const index = keyIndices.get(key)
        if (index !== undefined) dirtyIndex = Math.min(dirtyIndex, hasDuplicates ? 0 : index)
    }

    function reconcile(keys, readHeight) {
        let start = dirtyIndex
        if (keys !== currentKeys) {
            const indices = new Map()
            let duplicates = false
            let mismatch = Math.min(positions.length, keys.length)
            for (let index = 0; index < keys.length; index++) {
                const key = keys[index]
                if (indices.has(key)) duplicates = true
                else indices.set(key, index)
                if (index < mismatch && !Object.is(key, positions[index].key)) mismatch = index
            }
            start = Math.min(start, mismatch)
            if (duplicates || hasDuplicates) start = 0
            keyIndices = indices
            hasDuplicates = duplicates
            currentKeys = keys
        }

        if (start === Infinity) return positions
        start = Math.min(start, keys.length)
        const next = positions.slice(0, start)
        let top = start ? next[start - 1].top + next[start - 1].height : 0
        let unchanged = keys.length === positions.length
        for (let index = start; index < keys.length; index++) {
            const key = keys[index]
            const height = readHeight(key) ?? minItemHeight
            const previous = positions[index]
            if (!previous || !Object.is(previous.key, key) || previous.top !== top || previous.height !== height) {
                unchanged = false
            }
            next.push(Object.freeze({ index, key, top, height }))
            top += height
        }
        if (!unchanged) positions = Object.freeze(next)
        dirtyIndex = Infinity
        return positions
    }

    return { invalidateKey, reconcile }
}
