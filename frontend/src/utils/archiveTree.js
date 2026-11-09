/**
 * Pure rules of the "archive with descendants" dialog: which descendants are
 * rows, which checkboxes are checked, and the labels of the buttons.
 * No Vue, Pinia or i18n import: this module must load under plain `node --test`.
 *
 * Spec: docs/plans/2026-11-09-archive-with-descendants-spec.md
 */

const I18N_PREFIX = 'session.archiveDescendants'

// Literal states: `PROCESS_STATE` lives in `constants.js`, which imports i18n.
const LIVE_STATES = new Set(['starting', 'assistant_turn', 'awaiting_user_input'])

/**
 * Whether a session has work that an archive stops: the first two conditions
 * of the barrier definition.
 *
 * @param {{ state: ?string, backgroundWork: ?Object }} work
 * @returns {boolean}
 */
export function isWorkInProgress({ state, backgroundWork }) {
    return LIVE_STATES.has(state) || backgroundWork != null
}

/**
 * Whether a topology node is a barrier: a session the dialog never checks by
 * itself.
 *
 * @param {{ session: Object, process: ?Object, active_cron_count: number }} node
 * @returns {boolean}
 */
export function isArchiveBarrier(node) {
    return isWorkInProgress({
        state: node.process?.state,
        backgroundWork: node.process?.background_work_in_progress,
    }) || node.active_cron_count > 0 || !!node.session.pinned
}

function findTreeNode(treeNode, id) {
    if (treeNode.id === id) return treeNode
    for (const child of treeNode.children) {
        const found = findTreeNode(child, id)
        if (found) return found
    }
    return null
}

/**
 * Flat dialog rows of the descendants of the target, in tree order.
 *
 * - `depth`: 0 for the first listed level.
 * - `isLast`, `through`: the shape of `flattenTree` in `sessionTree.js`, for
 *   `treeGuides`. Like the roots of `flattenTree`, every row at depth 0 has
 *   `isLast: true`.
 * - `barrier`: the session of the row is a barrier.
 * - `stopsDescent`: the row is a barrier, or a hidden barrier lies between it
 *   and its nearest listed ancestor.
 * - `openChecked`: the row is checked when the dialog opens.
 * - `childIds`: the listed rows directly below this row.
 *
 * @param {{ tree: Object, nodes: Object[] }} topology
 * @param {string} targetId
 * @returns {Array<{
 *   id: string, session: Object, depth: number, isLast: boolean, through: boolean[],
 *   barrier: boolean, stopsDescent: boolean, openChecked: boolean, childIds: string[],
 * }>}
 */
export function buildArchiveTree(topology, targetId) {
    const target = findTreeNode(topology.tree, targetId)
    if (!target) return []
    const nodesById = new Map(topology.nodes.map((node) => [node.id, node]))

    // The tree nodes that become rows directly below `treeNode`, through the
    // sessions that are not listed. A detached session ends the walk. An archived
    // session never counts as a barrier, even when hidden.
    const listedBelow = (treeNode, crossedHiddenBarrier = false) => treeNode.children.flatMap((child) => {
        const node = nodesById.get(child.id)
        const { tree_detached: detached, archived, hidden } = node.session
        if (detached) return []
        if (archived) return listedBelow(child, crossedHiddenBarrier)
        if (hidden) return listedBelow(child, crossedHiddenBarrier || isArchiveBarrier(node))
        return [{ treeNode: child, crossedHiddenBarrier }]
    })

    const rows = []
    // `notLast`: for each ancestor at depth >= 1, whether it is not the last child.
    const visit = ({ treeNode, crossedHiddenBarrier }, depth, isLast, notLast, parentOpenChecked) => {
        const node = nodesById.get(treeNode.id)
        const barrier = isArchiveBarrier(node)
        const stopsDescent = barrier || crossedHiddenBarrier
        const openChecked = parentOpenChecked && !stopsDescent
        const children = listedBelow(treeNode)
        rows.push({
            id: treeNode.id,
            session: node.session,
            depth,
            isLast,
            through: notLast,
            barrier,
            stopsDescent,
            openChecked,
            childIds: children.map((child) => child.treeNode.id),
        })
        const next = depth >= 1 ? [...notLast, !isLast] : notLast
        children.forEach((child, i) => visit(child, depth + 1, i === children.length - 1, next, openChecked))
    }
    for (const entry of listedBelow(target)) visit(entry, 0, true, [], true)
    return rows
}

/**
 * Checkboxes at the opening of the dialog.
 *
 * @param {Object[]} rows
 * @returns {Set<string>}
 */
export function initialChecked(rows) {
    return new Set(rows.filter((row) => row.openChecked).map((row) => row.id))
}

/**
 * Check a row and descend through its children. A child with `stopsDescent`
 * and everything below it stay as they were.
 *
 * @param {Object[]} rows
 * @param {Set<string>} checked - Not modified.
 * @param {string} id
 * @returns {Set<string>}
 */
export function checkRow(rows, checked, id) {
    const rowsById = new Map(rows.map((row) => [row.id, row]))
    const next = new Set(checked)
    const check = (rowId) => {
        next.add(rowId)
        for (const childId of rowsById.get(rowId).childIds) {
            if (!rowsById.get(childId).stopsDescent) check(childId)
        }
    }
    check(id)
    return next
}

/**
 * Uncheck a row and its whole subtree.
 *
 * @param {Object[]} rows
 * @param {Set<string>} checked - Not modified.
 * @param {string} id
 * @returns {Set<string>}
 */
export function uncheckRow(rows, checked, id) {
    const rowsById = new Map(rows.map((row) => [row.id, row]))
    const next = new Set(checked)
    const uncheck = (rowId) => {
        next.delete(rowId)
        rowsById.get(rowId).childIds.forEach(uncheck)
    }
    uncheck(id)
    return next
}

/**
 * Every row, barriers included.
 *
 * @param {Object[]} rows
 * @returns {Set<string>}
 */
export function selectAll(rows) {
    return new Set(rows.map((row) => row.id))
}

/**
 * @param {Object[]} rows
 * @param {Set<string>} checked
 * @returns {boolean}
 */
export function allChecked(rows, checked) {
    return rows.every((row) => checked.has(row.id))
}

/**
 * Totals of the warning callout, for the target and the checked sessions.
 *
 * @param {Array<{ working: boolean, shells: number, crons: number }>} entries
 * @returns {{ processCount: number, shellCount: number, cronCount: number }}
 */
export function warningTotals(entries) {
    const totals = { processCount: 0, shellCount: 0, cronCount: 0 }
    for (const { working, shells, crons } of entries) {
        if (working) totals.processCount++
        totals.shellCount += shells
        totals.cronCount += crons
    }
    return totals
}

/**
 * Labels and variant of the dialog buttons. `count` is present on `primary`
 * only when the label needs it.
 *
 * @param {{ checkedCount: number, warningVisible: boolean, targetConcerned: boolean }} state -
 *   `targetConcerned`: the warning concerns the target. Ignored without a visible warning.
 * @returns {{
 *   primary: { key: string, count?: number, danger: boolean },
 *   secondary: ?{ key: string },
 *   cancel: { key: string },
 * }}
 */
export function archiveDialogButtons({ checkedCount, warningVisible, targetConcerned }) {
    const stopsTarget = warningVisible && targetConcerned
    const cancel = { key: stopsTarget ? 'session.stopConfirmation.cancel' : `${I18N_PREFIX}.cancel` }
    if (checkedCount === 0) {
        const key = `${I18N_PREFIX}.${warningVisible ? 'stopArchiveOnly' : 'archiveOnly'}`
        return { primary: { key, danger: warningVisible }, secondary: null, cancel }
    }
    return {
        primary: {
            key: `${I18N_PREFIX}.${warningVisible ? 'stopArchiveWith' : 'archiveWith'}`,
            count: checkedCount,
            danger: warningVisible,
        },
        secondary: { key: `${I18N_PREFIX}.${stopsTarget ? 'stopArchiveOnly' : 'archiveOnly'}` },
        cancel,
    }
}
