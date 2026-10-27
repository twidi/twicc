/**
 * Pure helpers for the sidebar "Show as tree" mode.
 * No Vue or Pinia import: this module must load under plain `node --test`.
 */

const isParticipant = (session) => !session.ephemeral && !session.draft

/** A session that can never be a tree ancestor nor give a hint. */
const isBlocking = (session) =>
    !!session.hidden || !!session.parent_session_id || !isParticipant(session)

/**
 * Build the tree structure (parents, roots, hints) for a candidate list.
 *
 * @param {Object[]} candidates
 * @param {{ lookup: (id: string) => ?Object }} opts
 * @returns {{ nodes: Map<string, Object>, rootIds: string[] }}
 */
export function buildSessionTree(candidates, { lookup }) {
    const byId = new Map(candidates.map((session) => [session.id, session]))
    const isParticipantCandidate = (id) => {
        const session = byId.get(id)
        return !!session && isParticipant(session)
    }

    // Nearest participant-candidate ancestor, walking through loaded sessions.
    const resolveParent = (session) => {
        const visited = new Set([session.id])
        let currentId = session.spawned_by
        while (currentId && !visited.has(currentId)) {
            visited.add(currentId)
            const current = lookup(currentId)
            if (!current || isBlocking(current)) return null
            if (isParticipantCandidate(currentId)) return currentId
            currentId = current.spawned_by
        }
        return null
    }

    const parentIds = new Map()
    for (const session of candidates) {
        parentIds.set(session.id, isParticipant(session) ? resolveParent(session) : null)
    }

    // Second pass: every node on a cycle becomes a root (nothing disappears).
    const state = new Map() // id -> 1 (on current path) | 2 (done)
    for (const startId of parentIds.keys()) {
        const path = []
        let id = startId
        while (id && !state.has(id)) {
            state.set(id, 1)
            path.push(id)
            id = parentIds.get(id)
        }
        if (id && state.get(id) === 1) {
            for (let i = path.indexOf(id); i < path.length; i++) parentIds.set(path[i], null)
        }
        for (const done of path) state.set(done, 2)
    }

    const nodes = new Map()
    for (const session of candidates) {
        nodes.set(session.id, {
            id: session.id,
            session,
            parentId: parentIds.get(session.id),
            childIds: [],
            hintParent: null,
        })
    }
    const rootIds = []
    for (const session of candidates) {
        const node = nodes.get(session.id)
        if (node.parentId) {
            nodes.get(node.parentId).childIds.push(session.id)
            continue
        }
        rootIds.push(session.id)
        if (!isParticipant(session) || !session.spawned_by) continue
        const direct = lookup(session.spawned_by)
        if (direct && !isParticipantCandidate(direct.id) && !isBlocking(direct)) {
            node.hintParent = direct
        }
    }
    return { nodes, rootIds }
}
