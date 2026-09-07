/**
 * Subagent naming — one resolver for every surface that shows an agent: the
 * subagent tabs, the subagent header, the Orchestration tab's agent tree and
 * the share viewer.
 *
 * The name comes from the call that spawned the agent (``display_name`` in the
 * agent-links payload): Claude Code's ``description`` qualified by its
 * ``subagent_type``, Codex multi-agent v2's ``task_name``. That is the only
 * source that tells an agent apart, because the providers' own names do not:
 *
 * - Claude Code stores the SESSION's slug on every one of its agents, so they
 *   all share it — and it has all but disappeared from recent transcripts;
 * - Codex hands a nested agent its launcher's nickname, and recycles nicknames
 *   between siblings on a long session.
 *
 * Hence the rule for the provider slug: it is only shown when it differs from
 * the launcher's, which drops both repetitions on its own. What is left when
 * nothing identifies the agent is the short id, and only then do callers wrap
 * it as ``Agent "…"`` — a real name speaks for itself.
 *
 * The data store is passed in rather than imported here to keep this module
 * free of Pinia / store cycles (it is imported from views, components, and
 * possibly composables). Both the SPA store and the share shim satisfy it.
 */

const SHORT_ID_LENGTH = 8

/**
 * Truncated agent id, used when no source names the agent.
 *
 * A workflow agent's id is ``<run id>:<agent id>``; the run prefix is the same
 * for every agent of the run, so only the part after the colon identifies it.
 */
export function getAgentShortId(agentId) {
    if (!agentId) return ''
    return agentId.slice(agentId.indexOf(':') + 1).substring(0, SHORT_ID_LENGTH)
}

/** Provider slug of a session or agent, wherever it is known. */
function slugOf(id, dataStore) {
    return dataStore?.getAgentLinkInfo?.(id)?.slug || dataStore?.getSession?.(id)?.slug || null
}

/**
 * Resolve how to present one agent.
 *
 * @returns {{ name: string, isFallback: boolean }} ``isFallback`` marks a name
 *   that is just the short id, which callers label as ``Agent "<id>"``.
 */
export function getAgentDisplay(agentId, dataStore) {
    const link = dataStore?.getAgentLinkInfo?.(agentId) || null
    const spawnName = link?.displayName || null
    // A slug the launcher already carries names the launcher, not this agent.
    const slug = link?.slug || dataStore?.getSession?.(agentId)?.slug || null
    const ownSlug = slug && slug !== slugOf(link?.ownerSessionId, dataStore) ? slug : null
    if (spawnName) {
        return { name: ownSlug ? `${spawnName} (${ownSlug})` : spawnName, isFallback: false }
    }
    if (ownSlug) return { name: ownSlug, isFallback: false }
    return { name: getAgentShortId(agentId), isFallback: true }
}
