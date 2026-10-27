/**
 * Pure helpers for the sidebar "Show as tree" mode.
 * No Vue or Pinia import: this module must load under plain `node --test`.
 */

import { sessionSortComparator } from './sessionSort.js'
import { summarizeProcessActivity } from './processActivity.js'

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

    // Post-order pass: effective state, triggers, descendants, then ordering.
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
        const triggerIds = []
        for (const dId of ordered) {
            const d = nodes.get(dId).session
            const dActive = processStates[dId] != null
            const dUnread = dId !== viewedId && !!isUnread(d, processStates[dId])
            const dPinned = !!d.pinned
            if (dActive || dUnread || dPinned) triggerIds.push(dId)
        }
        node.descendantIds = ordered
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

/**
 * Aggregated activity of a set of tree sessions: what a collapsed node shows
 * (the node itself plus its descendants). Same cascade input as the project
 * badges; skips synthetic, ephemeral and hidden sessions. The viewed session
 * never counts as unread, like on its own row.
 *
 * @param {string[]} ids
 * @param {{
 *   processStates: Object,
 *   sessions: (id: string) => ?Object,
 *   viewedId: ?string,
 *   isUnread: (session: Object, processState: ?Object) => boolean,
 * }} opts
 * @returns {ReturnType<typeof summarizeProcessActivity>}
 */
export function treeActivity(ids, { processStates, sessions, viewedId, isUnread }) {
    const states = []
    let unreadCount = 0
    for (const id of ids) {
        const ps = processStates[id]
        if (ps?.synthetic || ps?.extra?.ephemeral) continue
        const session = sessions(id)
        if (session?.hidden) continue
        if (ps) states.push(ps)
        if (session && id !== viewedId && isUnread(session, ps)) unreadCount++
    }
    return summarizeProcessActivity(states, unreadCount)
}

/** Deepest indentation level the row visuals render. */
export const TREE_MAX_INDENT_LEVEL = 3

/**
 * Flatten the tree into display rows, pre-order. Children are listed only
 * under expanded nodes.
 *
 * `isLast`: the node is the last child of its parent (roots: true).
 * `through`: length `max(depth - 1, 0)`; entry `i` is true when the ancestor
 * at depth `i + 1` is not the last child of its parent, i.e. the vertical
 * guide of column `i` continues through this row.
 *
 * @param {{ nodes: Map<string, Object>, rootIds: string[] }} tree
 * @param {(id: string) => boolean} isExpanded
 * @returns {Array<{ id: string, depth: number, isLast: boolean, through: boolean[] }>}
 */
export function flattenTree(tree, isExpanded) {
    const rows = []
    // `notLast`: for each ancestor at depth >= 1, whether it is not the last child.
    const visit = (id, depth, isLast, notLast) => {
        rows.push({ id, depth, isLast, through: notLast })
        if (!isExpanded(id)) return
        const childIds = tree.nodes.get(id).childIds
        const next = depth >= 1 ? [...notLast, !isLast] : notLast
        childIds.forEach((childId, i) => visit(childId, depth + 1, i === childIds.length - 1, next))
    }
    for (const id of tree.rootIds) visit(id, 0, true, [])
    return rows
}

/**
 * Guide lines of a child row, in columns (column `c` is the vertical line of
 * the ancestor at depth `c`, one indentation step wide).
 *
 * - `columns`: ancestor columns whose line continues through the row.
 * - `ownColumn`: the column of the row's own line and elbow.
 * - `ownEnds`: the own line stops at the elbow (no later row needs it).
 *
 * Past `TREE_MAX_INDENT_LEVEL` the rows keep the last column: the whole band
 * of deeper rows shares it, so it ends only when no row of the band follows.
 *
 * @param {{ depth: number, isLast: boolean, through: boolean[] }} row
 * @returns {?{ columns: number[], ownColumn: number, ownEnds: boolean }} null for roots
 */
export function treeGuides({ depth, isLast, through }) {
    if (depth < 1) return null
    const visible = TREE_MAX_INDENT_LEVEL - 1
    const columns = []
    through.slice(0, visible).forEach((continues, column) => { if (continues) columns.push(column) })
    const bandContinues = through.slice(visible).some(Boolean)
    return { columns, ownColumn: Math.min(depth, TREE_MAX_INDENT_LEVEL) - 1, ownEnds: isLast && !bandContinues }
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
    // Separate sets: an up walk must never hide a subtree from a down walk.
    const upVisited = new Set()
    const downVisited = new Set()
    const consider = (session) => {
        if (naturalIds.has(session.id) || found.has(session.id)) return
        if (isInScope(session) && passesFilter(session)) found.set(session.id, session)
    }
    const walkUp = (session) => {
        let currentId = session.spawned_by
        while (currentId && !upVisited.has(currentId)) {
            upVisited.add(currentId)
            const current = sessionsById[currentId]
            if (!current || isBlocking(current)) return
            consider(current)
            currentId = current.spawned_by
        }
    }
    const walkDown = (session) => {
        for (const child of childrenOf.get(session.id) || []) {
            if (downVisited.has(child.id) || isBlocking(child)) continue
            downVisited.add(child.id)
            consider(child)
            walkDown(child)
        }
    }

    for (const member of natural) {
        if (!isParticipant(member)) continue
        upVisited.add(member.id)
        walkUp(member)
        if (!isBlocking(member)) {
            downVisited.add(member.id)
            walkDown(member)
        }
    }
    return [...found.values()]
}

/**
 * Ids of ancestors the client must fetch: walking `spawned_by` upward from
 * every participant candidate, collect the ids `lookup` does not know.
 * The walk stops at a loaded blocking session (hidden, sub-agent, ephemeral,
 * draft), at ids in `skipIds` and on a cycle.
 *
 * @param {Object[]} candidates
 * @param {(id: string) => ?Object} lookup
 * @param {Set<string>} skipIds
 * @returns {string[]}
 */
export function findMissingAncestorIds(candidates, lookup, skipIds) {
    const missing = new Set()
    for (const candidate of candidates) {
        if (!isParticipant(candidate)) continue
        const visited = new Set([candidate.id])
        let currentId = candidate.spawned_by
        while (currentId && !visited.has(currentId) && !skipIds.has(currentId)) {
            visited.add(currentId)
            const current = lookup(currentId)
            if (!current) {
                missing.add(currentId)
                break
            }
            if (isBlocking(current)) break
            currentId = current.spawned_by
        }
    }
    return [...missing]
}
