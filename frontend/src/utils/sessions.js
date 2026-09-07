// frontend/src/utils/sessions.js

/**
 * Compute the lifecycle cutoff timestamp (in ms) for a session.
 * Tools/agents started before this cutoff cannot be running — the session
 * was restarted or stopped since then.
 * Returns max(last_started_at, last_stopped_at) in ms, or 0 if unavailable.
 *
 * @param {Object} session - Session object with last_started_at / last_stopped_at
 * @returns {number} Cutoff in milliseconds (0 if no cutoff can be determined)
 */
export function getSessionCutoffMs(session) {
    if (!session?.last_started_at) return 0
    const started = new Date(session.last_started_at).getTime()
    const stopped = session.last_stopped_at ? new Date(session.last_stopped_at).getTime() : 0
    return Math.max(started, stopped)
}

/**
 * Canonical "is this session unread" predicate — the single source of truth
 * shared by every surface that counts unread sessions (project/workspace
 * badges via AggregatedProcessIndicator, the command palette, the favicon and
 * the data-store unread getters). Keeping one definition prevents the surfaces
 * from drifting apart (they previously disagreed on `hidden` and on whether a
 * running session could still read as unread).
 *
 * A session is unread when it has content added after it was last viewed (or
 * was never viewed). Drafts, archived, subagent (`parent_session_id`) and
 * hidden sessions never count. When a process is running for the session it
 * only counts while waiting for the user (`user_turn`): during the agent's own
 * work the activity indicator takes over, so an "unread" eye would mislead.
 *
 * @param {Object} session - A session record from the data store.
 * @param {Object|null|undefined} processState - The session's live process
 *   state (data store `processStates[session.id]`), or null/undefined if none.
 * @returns {boolean}
 */
export function isSessionUnread(session, processState) {
    if (!session) return false
    if (session.hidden) return false
    if (session.draft || session.ephemeral || session.archived || session.parent_session_id) return false
    if (!session.last_new_content_at) return false
    if (session.last_viewed_at && session.last_new_content_at <= session.last_viewed_at) return false
    if (processState && processState.state !== 'user_turn') return false
    return true
}
