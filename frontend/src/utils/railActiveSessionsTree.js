/**
 * Tree-mode selection of the icon rail rows (spec §14).
 * Pure: no Vue or Pinia import, explicit `.js` imports (loads under `node --test`).
 */

import { activeCronCount, userTurnBackgroundShellCount } from './backgroundWork.js'
import { processActivityDisplayMode } from './processActivity.js'
import { isSessionUnread } from './sessions.js'
import { buildSessionTree, flattenTree, isBlocking, isParticipant, treeActivity, treeParentId } from './sessionTree.js'

/** Map of tree parent id (`treeParentId`) -> ids of the loaded sessions below it. */
export function buildChildrenIndex(sessions) {
    const index = new Map()
    for (const id of Object.keys(sessions)) {
        const parentId = sessions[id] ? treeParentId(sessions[id]) : null
        if (!parentId) continue
        const children = index.get(parentId)
        if (children) children.push(id)
        else index.set(parentId, [id])
    }
    return index
}

export function sameChildrenIndex(a, b) {
    if (a === b) return true
    if (!a || !b || a.size !== b.size) return false
    for (const [parentId, children] of a) {
        const other = b.get(parentId)
        if (!other || other.length !== children.length || children.some((id, i) => id !== other[i])) return false
    }
    return true
}

/**
 * Rows of the rail when the sidebar shows orchestrated sessions as a tree.
 *
 * The tree covers the rail-active sessions (`flatRows`), their ancestor chains
 * and the descendants of every collapsed candidate. Ordinary rows are the flat
 * rows plus `groupId` (the root node id of their tree) and `showMarkDot`
 * (same project as the nearest ancestor with a button). A collapsed node with loaded children becomes one row
 * `{ session, groupId, activeDescendants, cumulative }` (`activeDescendants`: tree-ordered entries
 * `{ session, processState, hasUnread, pendingRequest, ... }` of the descendants that contribute to the
 * cumulative indicator) carrying the cumulative summary of the node and its
 * descendants (no `processState`: the node may be idle).
 *
 * @param {Object[]} flatRows - result of selectRailActiveSessions
 * @param {{
 *   processStates: Object,
 *   sessions: Object<string, Object>,
 *   childrenIndex: Map<string, string[]>,
 *   isCollapsed: (id: string) => boolean,
 *   viewedId?: ?string,
 *   isUnread?: (session: Object, processState: ?Object) => boolean,
 * }} opts
 */
export function selectRailTreeRows(flatRows, {
    processStates, sessions, childrenIndex, isCollapsed, viewedId = null, isUnread = isSessionUnread,
}) {
    if (!flatRows.length) return flatRows
    const rowById = new Map(flatRows.map(row => [row.session.id, row]))
    const lookup = id => sessions[id] ?? null
    const candidates = new Map(flatRows.map(row => [row.session.id, row.session]))

    // Ancestor chains: same walk as the list (stops at unknown or blocking sessions).
    for (const row of flatRows) {
        if (!isParticipant(row.session)) continue
        let currentId = treeParentId(row.session)
        while (currentId && !candidates.has(currentId)) {
            const current = lookup(currentId)
            if (!current || isBlocking(current)) break
            candidates.set(currentId, current)
            currentId = treeParentId(current)
        }
    }

    // Descendants: only below collapsed candidates (their cumulative needs them).
    const walkDown = parentId => {
        for (const childId of childrenIndex.get(parentId) || []) {
            const child = lookup(childId)
            if (!child || isBlocking(child) || candidates.has(childId)) continue
            candidates.set(childId, child)
            walkDown(childId)
        }
    }
    for (const [id, session] of [...candidates]) {
        if (isParticipant(session) && !isBlocking(session) && isCollapsed(id)) walkDown(id)
    }

    const tree = buildSessionTree([...candidates.values()], { lookup, processStates, viewedId, isUnread })
    const rootOf = id => {
        let node = tree.nodes.get(id)
        while (node.parentId) node = tree.nodes.get(node.parentId)
        return node.id
    }
    // Active descendants of a collapsed node, tree order (pre-order). "Active" = contributes
    // to the cumulative indicator (same function, same exclusions, the viewed session is never unread).
    const activityOpts = { processStates, sessions: lookup, viewedId, isUnread }
    const activeDescendants = id => {
        const found = []
        const visit = parentId => {
            for (const childId of tree.nodes.get(parentId).childIds) {
                const child = tree.nodes.get(childId)
                const processState = processStates[childId]
                const unread = isUnread(child.session, processState) && childId !== viewedId
                if (processActivityDisplayMode(treeActivity([childId], activityOpts)) !== null) {
                    found.push({
                        session: child.session, processState, hasUnread: unread,
                        pendingRequest: (processState?.pending_requests?.length || 0) > 0,
                        userTurnBackgroundShells: processState ? userTurnBackgroundShellCount(processState) : 0,
                        hasActiveCrons: processState ? activeCronCount(processState) > 0 : false,
                    })
                }
                visit(childId)
            }
        }
        visit(id)
        return found
    }
    const rows = []
    for (const { id } of flattenTree(tree, nodeId => !isCollapsed(nodeId))) {
        const node = tree.nodes.get(id)
        if (node.childIds.length && isCollapsed(id)) {
            rows.push({
                session: node.session,
                groupId: rootOf(id),
                activeDescendants: activeDescendants(id),
                cumulative: treeActivity([id, ...node.descendantIds], {
                    processStates, sessions: lookup, viewedId, isUnread,
                }),
            })
        } else if (rowById.has(id)) {
            rows.push({ ...rowById.get(id), groupId: rootOf(id) })
        }
    }
    // Repeated project mark (§14): same project as the nearest ancestor that has a button.
    const rowById2 = new Map(rows.map(row => [row.session.id, row]))
    for (const row of rows) {
        let ref = tree.nodes.get(row.session.id)?.parentId
        while (ref && !rowById2.has(ref)) ref = tree.nodes.get(ref).parentId
        row.showMarkDot = !!ref && rowById2.get(ref).session.project_id === row.session.project_id
    }
    return rows
}
