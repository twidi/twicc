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
 * Raw "does this session carry content the user has not seen" check — the
 * data-level rule, without any visibility or process refinement.
 *
 * This is the frontend twin of the SQL sticky filter in
 * `_get_sessions_page` (`src/twicc/views.py`): both must stay in step, so
 * both carry the same three clauses (content exists, it is newer than the
 * last view, the session is not muted).
 *
 * Muting a session suppresses its unread state as well as its
 * finished-working notifications: the point of muting is that nothing about
 * the session surfaces on its own. Nothing is erased — `last_new_content_at`
 * and `last_viewed_at` keep being written — so unmuting brings the
 * accumulated unread state straight back.
 *
 * @param {Object} session - A session record from the data store.
 * @returns {boolean}
 */
export function hasUnreadContent(session) {
    if (!session) return false
    if (session.mute_on_user_turn) return false
    if (!session.last_new_content_at) return false
    if (session.last_viewed_at && session.last_new_content_at <= session.last_viewed_at) return false
    return true
}

/**
 * Canonical "is this session unread" predicate — the single source of truth
 * shared by every surface that counts unread sessions (project/workspace
 * badges via AggregatedProcessIndicator, the command palette, the favicon and
 * the data-store unread getters). Keeping one definition prevents the surfaces
 * from drifting apart (they previously disagreed on `hidden` and on whether a
 * running session could still read as unread).
 *
 * A session is unread when `hasUnreadContent` holds. Drafts, archived,
 * subagent (`parent_session_id`), hidden and muted sessions never count. When
 * a process is running for the session it only counts while waiting for the
 * user (`user_turn`): during the agent's own work the activity indicator takes
 * over, so an "unread" eye would mislead.
 *
 * The `'user_turn'` literal below and in `canToggleSessionReadState` must
 * track `PROCESS_STATE.USER_TURN` (`frontend/src/constants.js`). It is spelled
 * out rather than imported so this module stays import-free: every consumer
 * reaches it through Vite, but its unit test loads it under plain node, which
 * does not resolve this codebase's extensionless imports.
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
    if (!hasUnreadContent(session)) return false
    if (processState && processState.state !== 'user_turn') return false
    return true
}

/**
 * Whether the "mark as read / mark as unread" actions apply to a session.
 *
 * One definition for the three surfaces that offer them: the row's Session
 * Actions menu, the command palette on the open session, and the multi-select
 * bar. They used to carry three copies of this rule and could drift.
 *
 * Dropped for drafts, ephemerals, archived sessions and sessions whose process
 * is running outside `user_turn` — and for muted sessions, which never read as
 * unread, so both actions would be invisible no-ops.
 *
 * @param {Object} session - A session record from the data store.
 * @param {Object|null|undefined} processState - The session's live process state.
 * @returns {boolean}
 */
export function canToggleSessionReadState(session, processState) {
    if (!session) return false
    if (session.draft || session.ephemeral || session.archived) return false
    if (session.mute_on_user_turn) return false
    if (processState && processState.state !== 'user_turn') return false
    return true
}
