// frontend/src/utils/sidebarSessions.js
//
// Shared logic for building the four session blocks that the sidebar
// (SessionList.vue) and the command palette's "Go to Session…" picker
// (staticCommands.js) render in the same order:
//
//   1. `extra`              — selected session that belongs to none of the
//                             other blocks (deep-linked / out-of-scope)
//   2. `crossFilterPinned`  — sessions whose pin mode brings them in from
//                             outside the current filter (`workspace`/`all`)
//   3. `crossFilterActive`  — sessions with a running process or unread
//                             content (never a muted one) from outside the
//                             current filter (gated by
//                             `showActiveAcrossFilters`)
//   4. `natural`            — sessions that naturally belong to the filter
//                             (single project / workspace / all projects)
//
// The blocks come from separate stages (design:
// docs/plans/2026-10-28-sidebar-recompute-cost-design.md, 5.3, 5.4 and 5.7):
//
//   - natural membership: the scope list's `members` (never its order), the
//     archive filter and, in tree mode, the tree extension;
//   - cross-filter membership: both cross-filter blocks from the root sessions,
//     unsorted, outside the natural ids;
//   - order stages: the natural order (the scope list's `ordered` restricted to
//     the natural ids, with the extension merged in) and one sort per
//     cross-filter block.
//
// Each stage returns its previous output when its content is equal.
// `useSidebarSessionBlocks` wires them as computeds (SessionList.vue), so a
// change reruns only the stages that read what changed.
// `computeSidebarSessionBlocks` runs them once (the command palette, the peer
// message review dialog).
//
// The `data` option is a data store (or a facade of it) that provides, besides
// `sessions`, `processStates`, `getProjectScopeIds`: `rootSessions`,
// `getScopeSessionList(scope)`, `archivedProjectIds`,
// `hasUnreadContentMemo(session)` and `inStartupPhase()`.
//
// No Pinia import: this module loads under plain `node --test`.

import { computed, toRaw } from 'vue'
import { sessionSortComparator } from './sessionSort.js'
import { retainIdSet, retainSessionArray } from './retain.js'
import { resolveSessionScope } from './sessionLists.js'
import { noteStageRun } from './stageRuns.js'
import { extendTreeCandidates } from './sessionTree.js'

const EMPTY = Object.freeze([])
const same = (session) => session

/** Return `previous` when every field of `next` holds the same value (`Object.is`). */
function retainFields(previous, next) {
    if (!previous) return next
    const keys = Object.keys(next)
    return keys.length === Object.keys(previous).length && keys.every(key => Object.is(next[key], previous[key]))
        ? previous
        : next
}

/**
 * Build the archived-filter predicate. The currently-selected session is
 * always kept visible, even when the archived toggles are off — so a deep
 * link to an archived session or a session in an archived project stays
 * reachable. The archived projects come from `data.archivedProjectIds`, which
 * never reads project activity.
 */
function makeArchiveFilter({ data, showArchived, showArchivedProjects, sessionId }) {
    const archivedProjectIds = showArchivedProjects ? null : data.archivedProjectIds
    return (s) => (
        (showArchived || !s.archived || s.id === sessionId)
        && (!archivedProjectIds || !archivedProjectIds.has(s.project_id) || s.id === sessionId)
    )
}

/**
 * Natural membership: the sessions that belong to the filter, in the scope
 * list's `members` order (no sort), plus, in tree mode, the loaded in-scope
 * sessions linked to them by a tree-parent chain (see `extendTreeCandidates`).
 * Those count as natural: never in a cross-filter block nor `extra`.
 *
 * It reads `list.members`, never `list.ordered`, so an order change does not
 * rerun it. During a startup phase (`data.inStartupPhase()`, read on each run so
 * a progress message reruns it) it reads the natural records, the roots of the
 * tree extension and the tree lookups through `toRaw`: no session field is
 * tracked, and the outputs hold raw records.
 *
 * @param {Object} options - see `computeSidebarSessionBlocks`
 * @param {?Object} [previous] - the previous result, returned when equal
 * @returns {{ list: Object, natural: Object[], naturalIds: Set<string>, extension: Object[] }}
 *   `list`: the scope's stable list (`getScopeSessionList`); `natural`: its
 *   archive-filtered members; `naturalIds`: the ids of `natural` and `extension`
 */
export function computeSidebarNaturalMembership(options, previous) {
    noteStageRun('blocks:natural')
    const { data, workspaces, effectiveProjectId, showAsTree = false } = options
    const startup = data.inStartupPhase()
    const read = startup ? toRaw : same
    const scope = resolveSessionScope({
        effectiveProjectId,
        getProjectScopeIds: data.getProjectScopeIds,
        getVisibleProjectIds: workspaces.getVisibleProjectIds,
    })
    const list = data.getScopeSessionList(scope)
    const passesArchiveFilter = makeArchiveFilter(options)
    const natural = list.members.filter(session => passesArchiveFilter(read(session)))

    let extension = EMPTY
    if (showAsTree) {
        const inScope = scope.projectIds ? new Set(scope.projectIds) : null
        const sessions = startup ? toRaw(data.sessions) : data.sessions
        const roots = startup ? data.rootSessions.map(toRaw) : data.rootSessions
        noteStageRun('blocks:extension')
        // The extension never holds a member of `list`: it skips the natural
        // members, and a member it could reach that is not natural fails the
        // same archive filter. `orderSidebarNatural` still dedupes on it.
        extension = extendTreeCandidates(startup ? natural.map(toRaw) : natural, {
            roots,
            lookup: id => sessions[id],
            isInScope: session => !inScope || inScope.has(session.project_id),
            passesFilter: passesArchiveFilter,
        })
    }

    const naturalIds = new Set()
    for (const session of natural) naturalIds.add(read(session).id)
    for (const session of extension) naturalIds.add(read(session).id)

    return retainFields(previous, {
        list,
        natural: retainSessionArray(previous?.natural, natural),
        naturalIds: retainIdSet(previous?.naturalIds, naturalIds),
        extension: retainSessionArray(previous?.extension, extension),
    })
}

/**
 * Cross-filter membership, in root-session order (no sort):
 *
 * - `crossFilterPinned`: pinned `all` always, `workspace` when the active
 *   workspace contains the session's project (a worktree of a member counts).
 * - `crossFilterActive` (only with `showActiveAcrossFilters`): a running
 *   process or unread content, never a draft nor a launched ephemeral session.
 *   "Unread" is `hasUnreadContent` through `data.hasUnreadContentMemo`, the
 *   same rule the backend's `/api/sessions/?unread=1` endpoint applies in SQL,
 *   so a muted session is never promoted on unread grounds. The "user_turn
 *   only" refinement of the row's unread indicator is intentionally skipped: a
 *   session mid-assistant-turn still deserves surfacing, and a muted one still
 *   surfaces through its process, since mute is not `hidden`.
 *
 * Archived sessions only surface in the natural block; the selected session
 * still shows through `extra`. Iterates `data.rootSessions`, so it never reads
 * a sub-agent. It never reads the startup phase: it keeps its tracking during
 * startup.
 *
 * @param {Object} options - see `computeSidebarSessionBlocks`
 * @param {Set<string>} naturalIds - from `computeSidebarNaturalMembership`
 * @param {?Object} [previous] - the previous result, returned when equal
 * @returns {{ crossFilterPinned: Object[], crossFilterActive: Object[] }}
 */
export function computeSidebarCrossFilterMembership(options, naturalIds, previous) {
    noteStageRun('blocks:crossFilter')
    const { data, workspaces, activeWorkspaceId, showActiveAcrossFilters } = options
    const passesArchiveFilter = makeArchiveFilter(options)
    const roots = data.rootSessions
    const activeWs = activeWorkspaceId ? workspaces.getWorkspaceById(activeWorkspaceId) : null

    const pinned = []
    for (const s of roots) {
        if (!s.pinned || s.archived || naturalIds.has(s.id)) continue
        const lifted = s.pinned === 'all'
            || (s.pinned === 'workspace' && !!activeWs
                && workspaces.workspaceContainsProject(activeWorkspaceId, s.project_id))
        if (lifted && passesArchiveFilter(s)) pinned.push(s)
    }

    let active = EMPTY
    if (showActiveAcrossFilters) {
        const pinnedIds = new Set(pinned.map(s => s.id))
        const processStates = data.processStates
        const hasUnreadContent = data.hasUnreadContentMemo
        active = []
        for (const s of roots) {
            if (s.draft || s.ephemeral || s.archived) continue
            if (naturalIds.has(s.id) || pinnedIds.has(s.id)) continue
            if ((processStates[s.id] != null || hasUnreadContent(s)) && passesArchiveFilter(s)) active.push(s)
        }
    }

    return retainFields(previous, {
        crossFilterPinned: retainSessionArray(previous?.crossFilterPinned, pinned),
        crossFilterActive: retainSessionArray(previous?.crossFilterActive, active),
    })
}

/**
 * Natural order: the scope list's `ordered` restricted to `naturalIds`. With a
 * non-empty tree extension, the extension is merged in and the whole is sorted
 * with `sessionSortComparator`; during a startup phase that sort reads the raw
 * records, and the stage reads `data.inStartupPhase()` so that it reruns on
 * each progress message.
 *
 * Extension sessions whose sort keys are equal may come out in another
 * relative order than a walk over the ordered list would give (design 5.4).
 *
 * @param {Object} options - see `computeSidebarSessionBlocks`
 * @param {{ ordered: Object[] }} list - the scope list of the natural membership
 * @param {Set<string>} naturalIds
 * @param {Object[]} extension
 * @param {?Object[]} [previous] - the previous result, returned when equal
 * @returns {Object[]}
 */
export function orderSidebarNatural(options, list, naturalIds, extension, previous) {
    noteStageRun('blocks:naturalOrder')
    const restricted = list.ordered.filter(session => naturalIds.has(session.id))
    if (!extension.length) return retainSessionArray(previous, restricted)
    const { data } = options
    const startup = data.inStartupPhase()
    const read = startup ? toRaw : same
    const compare = sessionSortComparator(startup ? toRaw(data.processStates) : data.processStates)
    // `naturalIds` also holds the extension ids. The extension never holds a
    // member of `list` (see `computeSidebarNaturalMembership`), but the merge
    // restricts on the natural members only, so it can never hold a duplicate.
    const extensionIds = new Set(extension.map(session => read(session).id))
    const merged = [...restricted.filter(session => !extensionIds.has(session.id)), ...extension]
        .sort((a, b) => compare(read(a), read(b)))
    return retainSessionArray(previous, merged)
}

/**
 * A cross-filter block in display order: a sorted copy.
 *
 * @param {Object[]} sessions
 * @param {Object} processStates
 * @param {?Object[]} [previous] - the previous result, returned when equal
 * @returns {Object[]}
 */
export function orderSidebarBlock(sessions, processStates, previous) {
    const next = sessions.length > 1 ? [...sessions].sort(sessionSortComparator(processStates)) : sessions
    return retainSessionArray(previous, next)
}

/**
 * The `extra` block: the selected session when it belongs to none of the
 * other blocks (deep link, out of scope). A sub-agent is never extra.
 *
 * @param {Object} options - see `computeSidebarSessionBlocks`
 * @param {Set<string>} naturalIds
 * @param {Object[]} crossFilterPinned
 * @param {Object[]} crossFilterActive
 * @returns {?Object}
 */
export function computeSidebarExtra({ data, sessionId }, naturalIds, crossFilterPinned, crossFilterActive) {
    if (!sessionId || naturalIds.has(sessionId)) return null
    if (crossFilterPinned.some(s => s.id === sessionId) || crossFilterActive.some(s => s.id === sessionId)) return null
    const s = data.sessions[sessionId]
    return s && !s.parent_session_id ? s : null
}

function orderPinned(options, crossFilterPinned, previous) {
    noteStageRun('blocks:pinnedOrder')
    return orderSidebarBlock(crossFilterPinned, options.data.processStates, previous)
}

function orderActive(options, crossFilterActive, previous) {
    noteStageRun('blocks:activeOrder')
    return orderSidebarBlock(crossFilterActive, options.data.processStates, previous)
}

/**
 * Split sidebar sessions into the four ordered blocks, in one pass over the
 * stages (the command palette, the peer message review dialog). Inside a
 * `computed`, every reactive access of every stage is tracked.
 *
 * @param {Object} opts
 * @param {Object} opts.data       - `useDataStore()` instance (see the module comment)
 * @param {Object} opts.workspaces - `useWorkspacesStore()` instance
 * @param {string} opts.effectiveProjectId - Sidebar scope:
 *     real project id (single-project) | `ALL_PROJECTS_ID` | `ws:<id>` | falsy (all projects)
 * @param {?string} opts.activeWorkspaceId - Workspace active for cross-filter
 *     `workspace`-mode pins. In workspace mode this equals the workspace
 *     extracted from `effectiveProjectId`; in single-project mode it comes
 *     from the caller (e.g. `route.query.workspace`).
 * @param {?string} opts.sessionId - Selected session id (drives the `extra`
 *     block and the archived-filter exception).
 * @param {boolean} opts.showArchived
 * @param {boolean} opts.showArchivedProjects
 * @param {boolean} opts.showActiveAcrossFilters
 * @param {boolean} [opts.showAsTree=false] - "Show as tree" mode: `natural`
 *     is extended with the loaded in-scope sessions linked to it by a
 *     tree-parent chain (see `extendTreeCandidates`). Flat mode is unchanged.
 * @param {?Object} [previous] - the previous result, returned when equal
 *
 * @returns {{
 *     extra: ?Object,
 *     crossFilterPinned: Object[],
 *     crossFilterActive: Object[],
 *     natural: Object[],
 * }}
 */
export function computeSidebarSessionBlocks(options, previous) {
    const { list, naturalIds, extension } = computeSidebarNaturalMembership(options)
    const { crossFilterPinned, crossFilterActive } = computeSidebarCrossFilterMembership(options, naturalIds)
    return retainFields(previous, {
        extra: computeSidebarExtra(options, naturalIds, crossFilterPinned, crossFilterActive),
        crossFilterPinned: orderPinned(options, crossFilterPinned, previous?.crossFilterPinned),
        crossFilterActive: orderActive(options, crossFilterActive, previous?.crossFilterActive),
        natural: orderSidebarNatural(options, list, naturalIds, extension, previous?.natural),
    })
}

/**
 * The reactive wiring of the block stages (SessionList.vue). Each stage is a
 * retaining computed that reads its inputs through per-field computeds:
 *
 *   natural membership → list, naturalIds, extension
 *   naturalIds → cross-filter membership → crossFilterPinned, crossFilterActive
 *   list, naturalIds, extension → natural order
 *   crossFilterPinned → pinned order;  crossFilterActive → active order
 *   all of them → blocks (adds `extra`)
 *
 * So an order change reruns no membership stage, a cross-filter reorder does
 * not rerun the natural order, and a natural reorder does not sort the
 * cross-filter blocks again.
 *
 * @param {() => Object} getOptions - returns the options of
 *   `computeSidebarSessionBlocks`; called inside a computed, so it may read
 *   props and refs. Every value it returns must be a primitive or a stable
 *   reference: the fields are compared with `Object.is`, so an inline object
 *   or array would rerun every stage.
 * @returns {{
 *   blocks: import('vue').ComputedRef<{ extra: ?Object, crossFilterPinned: Object[],
 *     crossFilterActive: Object[], natural: Object[] }>,
 *   stages: { natural, crossFilter, naturalOrder, pinnedOrder, activeOrder },
 * }} `stages` exposes the stage computeds (tests)
 */
export function useSidebarSessionBlocks(getOptions) {
    // Kept while every option holds the same value, so a stage reruns only on a real change.
    const options = computed(previous => retainFields(previous, { ...getOptions() }))

    const natural = computed(previous => computeSidebarNaturalMembership(options.value, previous))
    const list = computed(() => natural.value.list)
    const naturalIds = computed(() => natural.value.naturalIds)
    const extension = computed(() => natural.value.extension)

    const crossFilter = computed(previous =>
        computeSidebarCrossFilterMembership(options.value, naturalIds.value, previous))
    const crossFilterPinned = computed(() => crossFilter.value.crossFilterPinned)
    const crossFilterActive = computed(() => crossFilter.value.crossFilterActive)

    const naturalOrder = computed(previous =>
        orderSidebarNatural(options.value, list.value, naturalIds.value, extension.value, previous))
    const pinnedOrder = computed(previous => orderPinned(options.value, crossFilterPinned.value, previous))
    const activeOrder = computed(previous => orderActive(options.value, crossFilterActive.value, previous))

    const blocks = computed(previous => retainFields(previous, {
        extra: computeSidebarExtra(options.value, naturalIds.value, crossFilterPinned.value, crossFilterActive.value),
        crossFilterPinned: pinnedOrder.value,
        crossFilterActive: activeOrder.value,
        natural: naturalOrder.value,
    }))

    return { blocks, stages: { natural, crossFilter, naturalOrder, pinnedOrder, activeOrder } }
}
