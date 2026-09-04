import { truncateTitle } from './truncate.js'

/** Session titles in routing lines (inbox rows, toasts, notifications): long
 *  enough to recognise a session, short enough to leave the project visible. */
export const PEER_ROUTING_TITLE_MAX = 40

/**
 * Where a peer message counts, for every surface that names it — inbox rows,
 * the review dialog, toasts, browser and push notifications. One reading:
 *
 *   - the message's own local session (`delivered_to_session` inbound,
 *     `origin_session` outbound), with its project;
 *   - else the session its thread names (`effective_session`, the nearest
 *     thread row that has one), with its project — `fromConversation`;
 *   - else a bare project: attached by hand (`attached`), or inherited from a
 *     thread row that only has one (`fromConversation`);
 *   - else nothing.
 *
 * @returns {{sessionId: string|null, sessionTitle: string, projectId: string|null,
 *            fromConversation: boolean} | null}
 */
export function peerMessageRouting(message) {
    if (!message) return null
    const local = message.direction === 'in' ? message.delivered_to_session : message.origin_session
    if (local) {
        return {
            sessionId: local.id,
            sessionTitle: local.title || 'Untitled session',
            projectId: local.project_id || null,
            fromConversation: false,
        }
    }
    const session = message.effective_session
    if (session?.id) {
        return {
            sessionId: session.id,
            sessionTitle: session.title || 'Untitled session',
            projectId: session.project_id || null,
            fromConversation: true,
        }
    }
    const project = message.effective_project
    if (project?.id) {
        return {
            sessionId: null,
            sessionTitle: '',
            projectId: project.id,
            fromConversation: project.source === 'conversation',
        }
    }
    return null
}

/** The routing's session title, one flattened line cut at
 *  `PEER_ROUTING_TITLE_MAX` — the full title stays for tooltips. */
export function peerRoutingSessionTitle(routing) {
    const flat = String(routing?.sessionTitle || '').replace(/\s+/g, ' ').trim()
    return truncateTitle(flat, PEER_ROUTING_TITLE_MAX, '')
}

/**
 * The routing as one line of plain text, for surfaces without a badge
 * (browser and push notifications): `session “…” in <project>`, or
 * `in <project>` alone. A worktree reads `main › worktree`, like the
 * process-state notifications. `projectLabel(projectId)` supplies the
 * project text.
 */
export function peerRoutingText(routing, projectLabel) {
    if (!routing) return ''
    const project = routing.projectId ? projectLabel(routing.projectId) : ''
    const parts = []
    if (routing.sessionId) parts.push(`session “${peerRoutingSessionTitle(routing)}”`)
    if (project) parts.push(`in ${project}`)
    return parts.join(' ')
}
