// Shared owner-view/share-view agent cache. Roots identify trees; owners identify transcripts.
// ``agentLoaded`` marks the roots whose ``/subagents/`` snapshot has landed, so a
// consumer can tell "this tree has no agent" from "we don't know yet".
export function agentLinkState() {
    return { agentLinks: {}, agentLinkIndex: {}, agentStops: {}, agentIdle: {}, agentRevision: 0, agentRevisions: {}, agentFetches: {}, agentLoaded: {} }
}
const time = value => value ? Date.parse(value) || 0 : 0
const latest = (a, b) => time(a) >= time(b) ? a : b
export function setAgentLink(state, owner, tool, entry, live = true) {
    if (!entry.agentId) return
    const map = state.agentLinks[owner] ||= {}
    const prior = map[tool]
    if (prior && prior.agentId !== entry.agentId && state.agentLinkIndex[prior.agentId]?.ownerSessionId === owner) {
        delete state.agentLinkIndex[prior.agentId]
    }
    const stoppedAt = latest(latest(entry.stoppedAt, prior?.agentId === entry.agentId ? prior.stoppedAt : null), state.agentStops[entry.agentId]?.stoppedAt) || null
    // Link-created messages carry identity, not a new run boundary. A duplicate
    // or background upgrade cannot erase state learned from an earlier snapshot.
    if (live && prior?.agentId === entry.agentId) {
        entry = { ...entry, agentStoppedAt: prior.agentStoppedAt, running: prior.running }
    }
    // ``metrics`` (cost / turns / context) and ``displayName`` only ever arrive
    // with a snapshot; a live event carries identity, so it must not erase them.
    if (prior?.agentId === entry.agentId) {
        if (entry.metrics === undefined) entry = { ...entry, metrics: prior.metrics }
        if (entry.displayName === undefined) entry = { ...entry, displayName: prior.displayName }
    }
    const idle = state.agentIdle[entry.agentId]
    if (idle) entry = { ...entry, agentStoppedAt: idle.stoppedAt, running: undefined }
    const value = { ...entry, running: stoppedAt ? false : entry.running, stoppedAt, ownerSessionId: owner, toolUseId: tool }
    map[tool] = value
    state.agentLinkIndex[entry.agentId] = value
    if (live) state.agentRevisions[entry.agentId] = ++state.agentRevision
    return value
}
export function clearAgentLinks(state, owner) {
    for (const entry of Object.values(state.agentLinks[owner] || {})) {
        if (state.agentLinkIndex[entry.agentId]?.ownerSessionId === owner) delete state.agentLinkIndex[entry.agentId]
    }
    delete state.agentLinks[owner]
    delete state.agentLoaded[owner]
    // Invalidate in-flight reads, including an owner evicted during its root fetch.
    state.agentRevision++
    for (const root of Object.keys(state.agentFetches)) state.agentFetches[root]++
}
export function markAgentStopped(state, agentId, stoppedAt, rootSessionId) {
    const prior = state.agentStops[agentId]
    state.agentStops[agentId] = { stoppedAt: latest(stoppedAt, prior?.stoppedAt), rootSessionId: rootSessionId || prior?.rootSessionId }
    const entry = state.agentLinkIndex[agentId]
    if (entry) {
        entry.stoppedAt = latest(entry.stoppedAt, stoppedAt)
        entry.running = false
    }
    state.agentRevisions[agentId] = ++state.agentRevision
}
export function markAgentIdle(state, agentId, stoppedAt) {
    // Keep null wake evidence too, including when the first link is still loading.
    state.agentIdle[agentId] = { stoppedAt: stoppedAt ?? null, revision: state.agentRevision + 1 }
    const entry = state.agentLinkIndex[agentId]
    if (entry) {
        entry.agentStoppedAt = stoppedAt ?? null
        entry.running = undefined
    }
    state.agentRevisions[agentId] = ++state.agentRevision
}
export function beginAgentFetch(state, root) {
    return { generation: state.agentFetches[root] = (state.agentFetches[root] || 0) + 1, revision: state.agentRevision }
}
export function applyAgentSnapshot(state, root, agents, token) {
    if (state.agentFetches[root] !== token.generation) return []
    const present = new Set(agents.map(a => a.agent_id))
    for (const entry of Object.values(state.agentLinkIndex)) {
        if (entry.rootSessionId === root && !present.has(entry.agentId) && (state.agentRevisions[entry.agentId] || 0) <= token.revision) {
            delete state.agentLinks[entry.ownerSessionId]?.[entry.toolUseId]
            delete state.agentLinkIndex[entry.agentId]
        }
    }
    const applied = []
    for (const agent of agents) {
        if ((state.agentRevisions[agent.agent_id] || 0) > token.revision && state.agentLinkIndex[agent.agent_id]) continue
        // A read started after the idle event is authoritative again. Older reads
        // may add link identity but must retain the event's idle/wake state.
        if (state.agentIdle[agent.agent_id]?.revision <= token.revision) delete state.agentIdle[agent.agent_id]
        applied.push(setAgentLink(state, agent.owner_session_id || root, agent.tool_use_id, {
            agentId: agent.agent_id, rootSessionId: root, isBackground: agent.is_background,
            toolUseLineNum: agent.tool_use_line_num, slug: agent.agent_slug ?? null,
            startedAt: agent.started_at ?? null, stoppedAt: agent.stopped_at ?? null,
            agentStoppedAt: agent.agent_stopped_at ?? null, running: agent.running,
            // What the launcher called this agent, resolved from the spawn call
            // (see utils/agentLabel.js). Shared payloads carry it too.
            displayName: agent.display_name ?? null,
            // Owner payload only (never shared): the agent's own numbers, for a
            // tree node that has no Session row loaded.
            metrics: Object.hasOwn(agent, 'total_cost')
                ? { totalCost: agent.total_cost, userMessageCount: agent.user_message_count, contextUsage: agent.context_usage }
                : undefined,
        }, false))
    }
    return applied
}
export function rootAgentToolLine(state, root, agentId) {
    const seen = new Set()
    while (agentId && !seen.has(agentId)) {
        seen.add(agentId)
        const entry = state.agentLinkIndex[agentId]
        if (!entry || entry.rootSessionId !== root) return null
        if (entry.ownerSessionId === root) return entry.toolUseLineNum ?? null
        agentId = entry.ownerSessionId
    }
    return null
}
export function staleSyntheticAgentIds(state, root, processStates, cutoffMs) {
    return Object.values(state.agentLinkIndex).filter(entry => entry.rootSessionId === root
        && processStates[entry.agentId]?.synthetic
        && (processStates[entry.agentId].started_at || 0) * 1000 < cutoffMs).map(entry => entry.agentId)
}
// Every agent of one tree, whatever its depth (the snapshot and the WS events
// both stamp ``rootSessionId``).
function treeAgentEntries(state, root) {
    return Object.values(state.agentLinkIndex).filter(entry => entry.rootSessionId === root && entry.agentId !== root)
}
export function hasTreeAgents(state, root) {
    return Object.values(state.agentLinkIndex).some(entry => entry.rootSessionId === root && entry.agentId !== root)
}
// Owner-anchored tree of a root's agents: ``ownerSessionId`` is the launching
// session (the root itself, or another agent), so nesting comes for free at any
// depth. An owner chain that does not provably reach the root — a missing owner,
// or a cycle — re-anchors the agent on the root, so the result is always a tree
// and no agent is silently dropped. Returns the root's children as
// ``{ id, entry, children }`` nodes, oldest spawn first.
export function buildAgentTree(state, root) {
    const entries = treeAgentEntries(state, root)
    const byId = new Map(entries.map(entry => [entry.agentId, entry]))
    const children = new Map()
    for (const entry of entries) {
        let owner = entry.ownerSessionId
        const seen = new Set([entry.agentId])
        while (owner && owner !== root && byId.has(owner) && !seen.has(owner)) {
            seen.add(owner)
            owner = byId.get(owner).ownerSessionId
        }
        const anchored = owner === root ? entry.ownerSessionId : root
        if (children.has(anchored)) children.get(anchored).push(entry)
        else children.set(anchored, [entry])
    }
    const order = (a, b) => (time(a.startedAt) - time(b.startedAt)) || (a.agentId < b.agentId ? -1 : 1)
    const build = owner => (children.get(owner) ?? []).sort(order)
        .map(entry => ({ id: entry.agentId, entry, children: build(entry.agentId) }))
    return build(root)
}
// Executable WS seam shared with the main dispatcher.
export function handleAgentEvent(store, msg) {
    if (msg.type === 'agent_stopped') {
        store.markAgentStopped(msg.agent_session_id, msg.stopped_at, msg.root_session_id)
        return
    }
    const root = msg.root_session_id || msg.parent_session_id
    store.setAgentLink(msg.parent_session_id, msg.tool_use_id, msg.agent_session_id,
        msg.is_background, msg.tool_use_line_num, msg.agent_slug ?? null, null,
        msg.started_at ?? null, null, root, msg.display_name ?? undefined)
    const link = store.getAgentLink(msg.parent_session_id, msg.tool_use_id)
    if (!link?.stoppedAt && link?.running !== false) store.setSyntheticProcessState(msg.agent_session_id, root, msg.project_id,
        msg.started_at ? time(msg.started_at) / 1000 : null)
}
