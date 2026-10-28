// frontend/src/utils/sessionTreeExpansion.js
// Pure expansion rules of the sidebar session tree (spec §9 and §10).
// No Vue, no Pinia: the store in stores/sessionTreeExpansion.js wraps this.
//
// A manual collapse or expand is final (spec §9). Only `explicit` (the user's
// own choices) is persisted. The other parts are session-only:
//   auto      ids expanded automatically (sticky until the user collapses)
//   revealed  ids opened to show the selected session (wins over explicit)

/**
 * @param {Object<string, boolean>} [explicit]
 */
export function createExpansionState(explicit = {}) {
    return {
        explicit,
        auto: new Set(),
        revealed: new Set(),
    }
}

export function isExpanded(state, id) {
    if (state.revealed.has(id)) return true
    const explicit = state.explicit[id]
    if (explicit === true) return true
    return explicit === undefined && state.auto.has(id)
}

/**
 * Apply the automatic expansion rule to the current tree: a node the user
 * never touched expands while its subtree has a trigger, and stays expanded.
 * A node with an explicit choice is never changed.
 *
 * @param {Object} state
 * @param {{ nodes: Map<string, Object> }} tree
 */
export function reconcileExpansion(state, tree) {
    for (const [id, node] of tree.nodes) {
        if (!node.childIds.length || !node.triggerIds.length) continue
        if (state.explicit[id] === undefined) state.auto.add(id)
    }
}

/**
 * User action: expand or collapse a node.
 */
export function setExpanded(state, id, value) {
    state.explicit[id] = value
    if (!value) {
        state.auto.delete(id)
        state.revealed.delete(id)
    }
}

/**
 * Reveal the ancestors of `id` (session-only; wins over a persisted collapse).
 * Pass the path returned by the previous call for the same selected session to
 * reveal only the ancestors that are new.
 *
 * @returns {string[]} the full current ancestor path, root first
 */
export function revealPath(state, tree, id, previousPath = []) {
    const path = []
    const visited = new Set([id])
    let parentId = tree.nodes.get(id)?.parentId ?? null
    while (parentId && !visited.has(parentId)) {
        visited.add(parentId)
        path.unshift(parentId)
        parentId = tree.nodes.get(parentId)?.parentId ?? null
    }
    const previous = new Set(previousPath)
    for (const ancestorId of path) {
        if (!previous.has(ancestorId)) state.revealed.add(ancestorId)
    }
    return path
}
