// Shared owner-view/share-view agent cache. Roots identify trees; owners identify transcripts.
// ``agentLoaded`` marks the roots whose ``/subagents/`` snapshot has landed, so a
// consumer can tell "this tree has no agent" from "we don't know yet".
// ``agentRunStates`` / ``agentInteractions`` hold the backend's per-agent run state
// and control calls (design §8.2); they live as long as their root. Their stamp
// maps hold global ``agentRevision`` stamps written only by the live events, apart
// from ``agentRevisions`` (which ``markAgentIdle`` also stamps).
export function agentLinkState() {
    return {
        agentLinks: {}, agentLinkIndex: {}, agentStops: {}, agentIdle: {}, agentRevision: 0, agentRevisions: {}, agentFetches: {}, agentLoaded: {},
        agentRunStates: {}, agentInteractions: {}, agentRunRevisions: {}, agentInteractionRevisions: {},
    }
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
    // Link-created messages carry identity only. A duplicate or background
    // upgrade keeps the idle display time learned from an earlier snapshot.
    if (live && prior?.agentId === entry.agentId) {
        entry = { ...entry, agentStoppedAt: prior.agentStoppedAt }
    }
    // ``metrics`` (cost / turns / context), ``displayName`` and ``model`` only ever arrive
    // with a snapshot; a live event carries identity, so it must not erase them.
    if (prior?.agentId === entry.agentId) {
        if (entry.metrics === undefined) entry = { ...entry, metrics: prior.metrics }
        if (entry.displayName === undefined) entry = { ...entry, displayName: prior.displayName }
        if (entry.model === undefined) entry = { ...entry, model: prior.model }
    }
    const idle = state.agentIdle[entry.agentId]
    if (idle) entry = { ...entry, agentStoppedAt: idle.stoppedAt }
    const value = { ...entry, stoppedAt, ownerSessionId: owner, toolUseId: tool }
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
// Display only: the stop time the tree shows. The running state is the run state's.
export function markAgentStopped(state, agentId, stoppedAt, rootSessionId) {
    const prior = state.agentStops[agentId]
    state.agentStops[agentId] = { stoppedAt: latest(stoppedAt, prior?.stoppedAt), rootSessionId: rootSessionId || prior?.rootSessionId }
    const entry = state.agentLinkIndex[agentId]
    if (entry) entry.stoppedAt = latest(entry.stoppedAt, stoppedAt)
    state.agentRevisions[agentId] = ++state.agentRevision
}
// Display only: the agent's own idle time.
export function markAgentIdle(state, agentId, stoppedAt) {
    // Keep null wake evidence too, including when the first link is still loading.
    state.agentIdle[agentId] = { stoppedAt: stoppedAt ?? null, revision: state.agentRevision + 1 }
    const entry = state.agentLinkIndex[agentId]
    if (entry) entry.agentStoppedAt = stoppedAt ?? null
    state.agentRevisions[agentId] = ++state.agentRevision
}
// Key of one run / one control call: the call that opened it, in its owner's transcript.
export function runKey(ownerSessionId, toolUseId) {
    return `${ownerSessionId}:${toolUseId}`
}
// A ``/subagents/`` snapshot entry or an ``agent_run_state`` payload → an
// ``agentRunStates`` entry. ``runs`` becomes a map keyed ``runKey``; ``closed_at``
// is not stored (no card reads it).
export function runStateFromPayload(payload, rootSessionId) {
    const runs = {}
    for (const run of payload.runs ?? []) {
        runs[runKey(run.owner_session_id, run.tool_use_id)] = { open: !!run.open, startedAt: run.started_at ?? null }
    }
    return {
        running: !!payload.running, runStartedAt: payload.run_started_at ?? null,
        runBackground: !!payload.run_background, rootSessionId, runs,
    }
}
// A snapshot ``interactions`` item or an ``agent_interaction`` payload → an
// ``agentInteractions`` entry targeting ``agentId``.
export function interactionFromPayload(payload, agentId, rootSessionId) {
    return {
        agentId, rootSessionId, kind: payload.kind, opensRun: !!payload.opens_run, startedAt: payload.started_at ?? null,
        toolUseLineNum: payload.tool_use_line_num ?? null, ownerSessionId: payload.owner_session_id, toolUseId: payload.tool_use_id,
    }
}
// ``live`` writes (WS events) stamp the global revision so a snapshot fetch
// started before them cannot overwrite them; snapshot writes stamp nothing.
export function setAgentRunState(state, agentId, entry, live = true) {
    state.agentRunStates[agentId] = entry
    if (live) state.agentRunRevisions[agentId] = ++state.agentRevision
}
export function setAgentInteraction(state, entry, live = true) {
    (state.agentInteractions[entry.ownerSessionId] ||= {})[entry.toolUseId] = entry
    if (live) state.agentInteractionRevisions[runKey(entry.ownerSessionId, entry.toolUseId)] = ++state.agentRevision
}
function deleteAgentInteraction(state, entry) {
    const owned = state.agentInteractions[entry.ownerSessionId]
    delete owned?.[entry.toolUseId]
    if (owned && !Object.keys(owned).length) delete state.agentInteractions[entry.ownerSessionId]
    delete state.agentInteractionRevisions[runKey(entry.ownerSessionId, entry.toolUseId)]
}
function rootInteractions(state, root) {
    return Object.values(state.agentInteractions).flatMap(owned => Object.values(owned)).filter(entry => entry.rootSessionId === root)
}
// Drop the run states and interactions of one root (its unload). Returns the
// agent ids whose run state was dropped, so the caller removes their synthetic state.
export function dropRootAgentState(state, root) {
    const dropped = []
    for (const [agentId, entry] of Object.entries(state.agentRunStates)) {
        if (entry.rootSessionId !== root) continue
        delete state.agentRunStates[agentId]
        delete state.agentRunRevisions[agentId]
        dropped.push(agentId)
    }
    for (const entry of rootInteractions(state, root)) deleteAgentInteraction(state, entry)
    return dropped
}
// The one copy of the frontend cutoff rule (design §8.2): running when the backend
// says so and the newest run did not start before the root cutoff. A null start
// counts as before a cutoff; ``cutoffMs`` 0 applies no cutoff.
export function effectiveAgentRun(runState, cutoffMs) {
    const startedMs = runState?.runStartedAt ? Date.parse(runState.runStartedAt) : NaN
    const startedAtUnix = Number.isNaN(startedMs) ? null : startedMs / 1000
    const running = !!runState?.running && (!cutoffMs || (startedAtUnix !== null && startedMs >= cutoffMs))
    return { running, startedAtUnix }
}
export function beginAgentFetch(state, root) {
    return { generation: state.agentFetches[root] = (state.agentFetches[root] || 0) + 1, revision: state.agentRevision }
}
export function applyAgentSnapshot(state, root, agents, token) {
    if (state.agentFetches[root] !== token.generation) return []
    // Run states and interactions first, independent of the per-link skip below:
    // an idle or link event never suppresses them; only their own live stamps do.
    const runStates = new Map(), interactions = new Map()
    for (const agent of agents) {
        runStates.set(agent.agent_id, runStateFromPayload(agent, root))
        for (const item of agent.interactions ?? []) {
            const entry = interactionFromPayload(item, agent.agent_id, root)
            interactions.set(runKey(entry.ownerSessionId, entry.toolUseId), entry)
        }
    }
    for (const [agentId, entry] of Object.entries(state.agentRunStates)) {
        if (entry.rootSessionId === root && !runStates.has(agentId) && (state.agentRunRevisions[agentId] || 0) <= token.revision) {
            delete state.agentRunStates[agentId]
            delete state.agentRunRevisions[agentId]
        }
    }
    for (const entry of rootInteractions(state, root)) {
        const key = runKey(entry.ownerSessionId, entry.toolUseId)
        if (!interactions.has(key) && (state.agentInteractionRevisions[key] || 0) <= token.revision) deleteAgentInteraction(state, entry)
    }
    for (const [agentId, entry] of runStates) {
        if ((state.agentRunRevisions[agentId] || 0) <= token.revision) setAgentRunState(state, agentId, entry, false)
    }
    for (const [key, entry] of interactions) {
        if ((state.agentInteractionRevisions[key] || 0) <= token.revision) setAgentInteraction(state, entry, false)
    }
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
            agentStoppedAt: agent.agent_stopped_at ?? null,
            // What the launcher called this agent, resolved from the spawn call
            // (see utils/agentLabel.js). Shared payloads carry it too.
            displayName: agent.display_name ?? null,
            // The agent's last used model ({raw, family, version}), as the session header shows it.
            model: agent.model ?? null,
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
// Agents of one root whose synthetic state started before the root cutoff. Walks
// the run states: a synthetic state only ever comes from one (link or not).
export function staleSyntheticAgentIds(state, root, processStates, cutoffMs) {
    return Object.entries(state.agentRunStates).filter(([agentId, entry]) => entry.rootSessionId === root
        && processStates[agentId]?.synthetic
        && (processStates[agentId].started_at || 0) * 1000 < cutoffMs).map(([agentId]) => agentId)
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
// Executable WS seam shared with the main dispatcher. A link event carries
// identity only; the running state comes from ``agent_run_state``.
export function handleAgentEvent(store, msg) {
    if (msg.type === 'agent_stopped') {
        store.markAgentStopped(msg.agent_session_id, msg.stopped_at, msg.root_session_id)
        return
    }
    if (msg.type === 'agent_run_state') {
        store.setAgentRunState(msg)
        return
    }
    if (msg.type === 'agent_interaction') {
        store.setAgentInteraction(msg)
        return
    }
    const root = msg.root_session_id || msg.parent_session_id
    store.setAgentLink(msg.parent_session_id, msg.tool_use_id, msg.agent_session_id,
        msg.is_background, msg.tool_use_line_num, msg.agent_slug ?? null, null,
        msg.started_at ?? null, null, root, msg.display_name ?? undefined)
}
