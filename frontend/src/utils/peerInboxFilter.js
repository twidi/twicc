const MESSAGE_HISTORY_LIMIT = 200
const MESSAGE_PEER_STATES = new Set(['active', 'broken', 'revoked'])

/** Whether any peer-inbox filter currently narrows message results. */
export function peerInboxFiltersActive(peerId, query, projectId = '') {
    return !!peerId || !!query.trim() || !!projectId
}

/** Partition the active inbox result source and choose its empty-state copy. */
export function peerInboxView(messages, filtersActive) {
    const received = []
    const history = []
    for (const message of messages) {
        if (message.direction === 'in' && message.status === 'pending') {
            received.push(message)
        } else {
            history.push(message)
        }
    }
    return {
        received,
        history,
        emptyMessage: received.length || history.length
            ? null
            : filtersActive ? 'No messages match your filters.' : 'No peer messages yet.',
    }
}

/** Peers that are established now, or still own retained message history. */
export function peerInboxSelectablePeers(peers, messages) {
    const peerIdsWithMessages = new Set(messages.map(message => message.peer_id))
    return peers.filter(peer =>
        MESSAGE_PEER_STATES.has(peer.state) || peerIdsWithMessages.has(peer.id)
    )
}

/**
 * Projects the inbox's project filter offers: among the listable (main)
 * projects, those that own peer messages themselves or through one of their
 * worktrees — a repository whose only messages live in a worktree still shows,
 * with that worktree nested under it. `worktreesOf(projectId)` returns a
 * project's worktree rows; `projectIdsWithMessages` is a Set of effective
 * project ids from the server.
 */
export function peerInboxSelectableProjects(projects, projectIdsWithMessages, worktreesOf) {
    return projects.filter(project =>
        projectIdsWithMessages.has(project.id) ||
        worktreesOf(project.id).some(worktree => projectIdsWithMessages.has(worktree.id))
    )
}

/** Hide revoked history until its Peer is selected explicitly. */
export function peerInboxVisibleMessages(messages, peers) {
    const revokedIds = new Set(peers.filter(peer => peer.state === 'revoked').map(peer => peer.id))
    return messages.filter(message => !revokedIds.has(message.peer_id))
}

/** Build the filtered inbox request without adding empty filter parameters.
 *  The project filter is resolved server-side (a message's own local session,
 *  else its nearest reply-chain ancestor's; a main repo folds in its
 *  worktrees) — the store only holds a page of history, never enough to
 *  filter client-side. */
export function buildPeerInboxSearchUrl(peerId, query, projectId = '') {
    const params = new URLSearchParams({ limit: String(MESSAGE_HISTORY_LIMIT) })
    if (peerId) params.set('peer_id', peerId)
    if (projectId) params.set('project_id', projectId)
    const trimmedQuery = query.trim()
    if (trimmedQuery) params.set('q', trimmedQuery)
    return `/api/peer-messages/?${params}`
}
