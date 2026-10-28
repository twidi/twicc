// Identity-retaining helpers for retaining computeds: each returns `previous`
// when `next` has the same content, so the computed stops propagation.
// Leaf module: it imports nothing, so any util or store can use it without a cycle.

/** Retain list identity when the same records remain in the same order. */
export function retainSessionArray(previous, next) {
    return previous?.length === next.length && next.every((entry, i) => entry === previous[i]) ? previous : next
}

/** Retain an id array when the same ids remain in the same order. Either side may be null. */
export function retainIds(previous, next) {
    if (previous === next || !previous || !next) return next
    return previous.length === next.length && next.every((id, i) => id === previous[i]) ? previous : next
}

/** Retain an id `Set` when it holds the same members. */
export function retainIdSet(previous, next) {
    if (!previous || !next) return next
    return previous.size === next.size && [...next].every(id => previous.has(id)) ? previous : next
}
