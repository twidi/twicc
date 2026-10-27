/**
 * Pure helpers for the sidebar "Show as tree" mode.
 * No Vue or Pinia import: this module must load under plain `node --test`.
 */

import { sessionSortComparator } from './sessionSort.js'

const isParticipant = (session) => !session.ephemeral && !session.draft

/** A session that can never be a tree ancestor nor give a hint. */
const isBlocking = (session) =>
    !!session.hidden || !!session.parent_session_id || !isParticipant(session)

/**
 * Build the tree structure (parents, roots, hints) for a candidate list.
 *
 * @param {Object[]} candidates
 * @param {{
 *   lookup: (id: string) => ?Object,
 *   processStates?: Object,
 *   viewedId?: ?string,
 *   isUnread?: (session: Object, processState: ?Object) => boolean,
 * }} opts
 * @returns {{ nodes: Map<string, Object>, rootIds: string[] }}
 */
export function buildSessionTree(candidates, {
    lookup,
    processStates = {},
    viewedId = null,
    isUnread = () => false,
}) {
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

    // Post-order pass: effective state, triggers, summary, then ordering.
    // Ordering reuses sessionSortComparator through proxy sessions that carry
    // the effective values, so the flat and tree orders can never diverge.
    const proxyStates = {}
    const proxies = new Map()
    const compare = sessionSortComparator(proxyStates)
    const byEffective = (aId, bId) => compare(proxies.get(aId), proxies.get(bId))

    const compute = (id) => {
        const node = nodes.get(id)
        const { session } = node
        const own = processStates[id]
        const isActive = own != null
        let pinned = session.pinned || null
        let active = isActive
        let activeKey = isActive ? (own.started_at || 0) : 0
        let dateKey = session.mtime

        for (const childId of node.childIds) {
            const child = compute(childId)
            active = active || child.effective.active
            activeKey = Math.max(activeKey, child.effective.activeKey)
            dateKey = Math.max(dateKey, child.effective.dateKey)
        }
        node.childIds.sort(byEffective)
        if (!pinned) {
            for (const childId of node.childIds) {
                const childPinned = nodes.get(childId).effective.pinned
                if (childPinned) { pinned = childPinned; break }
            }
        }
        // Descendants in final (sorted) depth-first order.
        const ordered = []
        const walk = (ids) => {
            for (const childId of ids) {
                ordered.push(childId)
                walk(nodes.get(childId).childIds)
            }
        }
        walk(node.childIds)

        node.effective = { pinned, active, activeKey, dateKey }
        const summary = { descendants: ordered.length, active: 0, unread: 0, pinned: 0, latestDate: null }
        const triggerIds = []
        for (const dId of ordered) {
            const d = nodes.get(dId).session
            const dActive = processStates[dId] != null
            const dUnread = dId !== viewedId && !!isUnread(d, processStates[dId])
            const dPinned = !!d.pinned
            if (dActive) summary.active++
            if (dUnread) summary.unread++
            if (dPinned) summary.pinned++
            if (dActive || dUnread || dPinned) triggerIds.push(dId)
        }
        summary.latestDate = dateKey > session.mtime ? dateKey : null
        node.summary = summary
        node.triggerIds = triggerIds

        proxies.set(id, {
            id,
            pinned,
            mtime: dateKey,
            ephemeral: session.ephemeral,
            draft: session.draft,
            ephemeralStartedAt: session.ephemeralStartedAt,
        })
        if (active) proxyStates[id] = { started_at: activeKey }
        return node
    }
    for (const id of rootIds) compute(id)
    rootIds.sort(byEffective)

    return { nodes, rootIds }
}

/** Deepest indentation level the row visuals render. */
export const TREE_MAX_INDENT_LEVEL = 3

/**
 * Flatten the tree into display rows, pre-order. Children are listed only
 * under expanded nodes.
 *
 * @param {{ nodes: Map<string, Object>, rootIds: string[] }} tree
 * @param {(id: string) => boolean} isExpanded
 * @returns {Array<{ id: string, depth: number }>}
 */
export function flattenTree(tree, isExpanded) {
    const rows = []
    const visit = (id, depth) => {
        rows.push({ id, depth })
        if (!isExpanded(id)) return
        for (const childId of tree.nodes.get(id).childIds) visit(childId, depth + 1)
    }
    for (const id of tree.rootIds) visit(id, 0)
    return rows
}

/**
 * Loaded sessions that must join the natural list so the tree is complete:
 * every in-scope session linked to a participant of `natural` by a
 * `spawned_by` chain (ancestor or descendant). Walks stop at hidden,
 * sub-agent, ephemeral and draft sessions, exactly like `buildSessionTree`.
 * The chain may pass through out-of-scope sessions, which are not added.
 *
 * @param {Object[]} natural
 * @param {{
 *   sessionsById: Object<string, Object>,
 *   isInScope: (session: Object) => boolean,
 *   passesFilter: (session: Object) => boolean,
 * }} opts
 * @returns {Object[]}
 */
export function extendTreeCandidates(natural, { sessionsById, isInScope, passesFilter }) {
    const naturalIds = new Set(natural.map((session) => session.id))
    const childrenOf = new Map()
    for (const session of Object.values(sessionsById)) {
        if (!session.spawned_by) continue
        if (!childrenOf.has(session.spawned_by)) childrenOf.set(session.spawned_by, [])
        childrenOf.get(session.spawned_by).push(session)
    }

    const found = new Map()
    const visited = new Set()
    const consider = (session) => {
        if (naturalIds.has(session.id) || found.has(session.id)) return
        if (isInScope(session) && passesFilter(session)) found.set(session.id, session)
    }
    const walkUp = (session) => {
        let currentId = session.spawned_by
        while (currentId && !visited.has(currentId)) {
            visited.add(currentId)
            const current = sessionsById[currentId]
            if (!current || isBlocking(current)) return
            consider(current)
            currentId = current.spawned_by
        }
    }
    const walkDown = (session) => {
        for (const child of childrenOf.get(session.id) || []) {
            if (visited.has(child.id) || isBlocking(child)) continue
            visited.add(child.id)
            consider(child)
            walkDown(child)
        }
    }

    for (const member of natural) {
        if (!isParticipant(member)) continue
        visited.add(member.id)
        walkUp(member)
        if (!isBlocking(member)) walkDown(member)
    }
    return [...found.values()]
}
