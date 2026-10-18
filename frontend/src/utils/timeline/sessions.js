// frontend/src/utils/timeline/sessions.js
//
// How the timeline names a session (display design sections 5.3 and 5.4): the title, the provider,
// and whether the mention is a link. One function serves every mention of a session: the group head,
// the "by <session>" author label and the `spawned_by` parent of `session.created`.
// Pure: the side table (`sessions`) is the only source.

/**
 * @typedef {Object} SideTableEntry
 * @property {string|null} title
 * @property {string|null} project_id
 * @property {string|null} provider
 * @property {boolean} hidden - Read for the link rule only.
 * @property {boolean} archived - Sent by the API; the timeline does not show it (the current state is not an event).
 */

/**
 * @typedef {Object} SessionMention
 * @property {string} id
 * @property {string} title - The side-table title, else the id (the full id for a group head, the
 *   first 8 characters in a label).
 * @property {string|null} provider
 * @property {string|null} projectId
 * @property {boolean} link - True only for a session that exists, is not hidden, is not a known
 *   subagent and has a project: the only ones that can be opened.
 */

/**
 * @param {string} sessionId
 * @param {Object<string, SideTableEntry>} sessions - The side table; an id with no row has no entry.
 * @param {Object} [options]
 * @param {boolean} [options.fullId=false] - Fall back to the whole id (the group head) rather than 8 characters.
 * @param {string|null} [options.fallbackProvider=null] - Used when the session has no entry.
 * @param {(id: string) => boolean} [options.isSubagent] - Best-effort knowledge of the data store: a
 *   subagent has no openable route as a top-level session.
 * @returns {SessionMention}
 */
export function sessionMention(sessionId, sessions, { fullId = false, fallbackProvider = null, isSubagent = () => false } = {}) {
    const entry = sessions?.[sessionId] ?? null
    const hidden = entry?.hidden === true
    return {
        id: sessionId,
        title: entry?.title || (fullId ? sessionId : String(sessionId).slice(0, 8)),
        provider: entry?.provider ?? fallbackProvider ?? null,
        projectId: entry?.project_id ?? null,
        link: entry !== null && !hidden && !!entry.project_id && !isSubagent(sessionId),
    }
}

/**
 * The identity shown once, on the group head (display design 5.3).
 *
 * @param {Object} event
 * @param {Object<string, SideTableEntry>} sessions
 * @param {{kind: 'global'|'workspace'|'project'|'worktree', projectId?: string|null}} scope
 * @param {Object} [options] - See {@link sessionMention}.
 * @returns {{mention: SessionMention, projectId: string|null, showProject: boolean}}
 */
export function groupHeadIdentity(event, sessions, scope, options = {}) {
    const data = event.data && typeof event.data === 'object' ? event.data : {}
    const mention = sessionMention(event.session_id, sessions, {
        ...options, fullId: true, fallbackProvider: event.provider ?? data.provider ?? null,
    })
    const projectId = event.project_id ?? sessions?.[event.session_id]?.project_id ?? null
    let showProject = false
    if (projectId) {
        if (scope.kind === 'global' || scope.kind === 'workspace') showProject = true
        // On a project page the rows of the project itself carry no name; the rows of its worktrees do.
        else if (scope.kind === 'project') showProject = projectId !== scope.projectId
    }
    return { mention, projectId, showProject }
}
