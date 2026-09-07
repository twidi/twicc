// Cost resolution for the Orchestration tab's agent tree, shared by the tree
// nodes and the toolbar total so both always agree.
//
// Two sources, in order: the agent's own ``Session`` row when it is loaded in
// the store (live, refreshed by ``session_updated``), else the ``metrics`` block
// carried by the ``/subagents/`` snapshot — a historical agent has no row.
//
// ``dataStore`` is passed in rather than imported, to keep this module free of
// Pinia cycles (it is imported from components).

/** Own cost of one agent, or null when no source knows it. */
export function agentCost(dataStore, agentId, metrics) {
    const row = dataStore?.getSession?.(agentId)
    if (row?.total_cost != null) return Number(row.total_cost)
    return metrics?.totalCost != null ? Number(metrics.totalCost) : null
}

/**
 * Cost of a ``buildAgentTree`` node and everything it launched.
 *
 * An agent's stored cost covers its own items only — a nested agent is NOT
 * rolled into its launcher (flat parenthood: every depth aggregates into the
 * root session) — so summing the levels never double-counts. A subtree no
 * source knows anything about stays null rather than reading as a free run.
 */
export function agentSubtreeCost(dataStore, node) {
    let total = agentCost(dataStore, node.id, node.entry?.metrics)
    for (const child of node.children ?? []) {
        const childTotal = agentSubtreeCost(dataStore, child)
        if (childTotal != null) total = (total ?? 0) + childTotal
    }
    return total
}

/** Sum of a whole forest (the root's children), for the toolbar total. */
export function agentForestCost(dataStore, nodes) {
    let total = null
    for (const node of nodes ?? []) {
        const subtotal = agentSubtreeCost(dataStore, node)
        if (subtotal != null) total = (total ?? 0) + subtotal
    }
    return total
}
