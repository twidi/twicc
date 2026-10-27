// frontend/src/utils/sessionTreeExpansion.js
// Pure expansion rules of the sidebar session tree (spec §9 and §10).
// No Vue, no Pinia: the store in stores/sessionTreeExpansion.js wraps this.
//
// Only `explicit` (the user's own collapses and expands) is persisted. The
// other parts are session-only:
//   auto         ids expanded automatically (sticky until the user collapses)
//   seen         global set of session ids that already met a trigger
//                condition (active, unread or pinned): "seen" belongs to the
//                descendant session, not to a node
//   revealed     ids opened to show the selected session (wins over explicit)
//   seededScopes scope keys whose initial pass already ran

/**
 * @param {Object<string, boolean>} [explicit]
 */
export function createExpansionState(explicit = {}) {
    return {
        explicit,
        auto: new Set(),
        seen: new Set(),
        revealed: new Set(),
        seededScopes: new Set(),
    }
}

export function isExpanded(state, id) {
    if (state.revealed.has(id)) return true
    const explicit = state.explicit[id]
    if (explicit === true) return true
    return explicit === undefined && state.auto.has(id)
}

/**
 * Apply the automatic expansion rules to the current tree.
 *
 * @param {Object} state
 * @param {{ nodes: Map<string, Object> }} tree
 * @param {{ settled: boolean, scopeKey: string, seedTriggerIds: () => string[] }} opts
 */
export function reconcileExpansion(state, tree, { settled, scopeKey, seedTriggerIds }) {
    // The initial load is not over: sessions or process states are still arriving.
    if (!settled) return

    const initial = !state.seededScopes.has(scopeKey)
    if (initial) {
        // Seed from every loaded session that meets a condition, so the seed
        // does not depend on the tree shape.
        for (const id of seedTriggerIds()) state.seen.add(id)
    }

    for (const [id, node] of tree.nodes) {
        if (!node.childIds.length) continue
        const triggerIds = node.triggerIds
        if (!triggerIds.length) continue
        const explicit = state.explicit[id]
        if (initial) {
            // Never un-collapse during the initial pass.
            if (explicit === undefined) state.auto.add(id)
            continue
        }
        if (explicit === undefined) {
            state.auto.add(id)
        } else if (explicit === false && triggerIds.some(t => !state.seen.has(t))) {
            delete state.explicit[id]
            state.auto.add(id)
        }
    }

    // After all nodes are evaluated, so a descendant shared by several
    // ancestors counts for each of them in the same pass.
    for (const node of tree.nodes.values()) {
        for (const t of node.triggerIds) state.seen.add(t)
    }
    if (initial) state.seededScopes.add(scopeKey)
}

/**
 * User action: expand or collapse a node.
 */
export function setExpanded(state, id, value, tree) {
    if (value) {
        state.explicit[id] = true
        return
    }
    state.explicit[id] = false
    state.auto.delete(id)
    state.revealed.delete(id)
    const node = tree.nodes.get(id)
    if (node) for (const t of node.triggerIds) state.seen.add(t)
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
