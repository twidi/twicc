import { sessionRouteLocation } from './sessionRoute.js'

/**
 * Convert the owner-only created_by serializer shape into badge rendering data.
 * The link to the creator session keeps the current frame (see sessionRouteLocation).
 */
export function shareCreatorBadge(createdBy, route) {
    if (createdBy?.kind !== 'agent') return null
    const session = createdBy.session
    if (!session) {
        return { label: 'Agent-created (hidden session)', to: null }
    }
    return {
        label: session.title || session.id,
        to: sessionRouteLocation({ id: session.id, project_id: session.project_id }, route),
    }
}
