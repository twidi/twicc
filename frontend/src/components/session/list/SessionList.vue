<script setup>
/**
 * SessionList - Virtual-scrolled list of sessions for a project.
 *
 * Handles list-level concerns: virtual scrolling, pagination (load more),
 * search filtering, keyboard navigation. Each session item is rendered
 * by SessionListItem, which owns its own store lookups (computed).
 */
import { ref, shallowRef, computed, watch, nextTick, onBeforeUnmount, onMounted, onActivated, onDeactivated, unref } from 'vue'
import { useRoute } from 'vue-router'
import { useDataStore, ALL_PROJECTS_ID } from '../../../stores/data'
import { useWorkspacesStore } from '../../../stores/workspaces'
import { useSessionSelectionStore } from '../../../stores/sessionSelection'
import { useSessionTreeExpansionStore } from '../../../stores/sessionTreeExpansion'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../../utils/workspaceIds'
import { computeSidebarSessionBlocks } from '../../../utils/sidebarSessions'
import { buildSessionTree, flattenTree, treeActivity, sharesProjectWithParent } from '../../../utils/sessionTree.js'
import { isSessionUnread } from '../../../utils/sessions'
import { retainSessionArray } from '../../../utils/sessionLists.js'
import { matchQuery } from '../../../utils/textFilter'
import { dateBucketSeparator } from '../../../utils/datePresets'
import { useSessionTreeAncestors } from '../../../composables/useSessionTreeAncestors'
import { useListCascade } from '../../../composables/useListCascade'
import { useListExit } from '../../../composables/useListExit'
import { useGlideInk } from '../../../composables/useGlideInk'
import { useSidebarScrollbar } from '../../../composables/useSidebarScrollbar'
import { activeRowBase, entranceOffset, revealBands, revealMargins } from '../../../utils/sidebarRows'
import { hasScrollerPaginationProgress } from '../../../utils/scrollerLoadWindow.js'
import { getSessionDisplayName } from '../../../utils/sessionTitle'
import VirtualScroller from '../../virtual-scroller/VirtualScroller.vue'
import SessionListItem from './SessionListItem.vue'
import SidebarListSeparator from '../../sidebar/SidebarListSeparator.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        default: null
    },
    showProjectName: {
        type: Boolean,
        default: false
    },
    searchQuery: {
        type: String,
        default: ''
    },
    showArchived: {
        type: Boolean,
        default: false
    },
    showArchivedProjects: {
        type: Boolean,
        default: true
    },
    compactView: {
        type: Boolean,
        default: false
    },
    // When true, sessions with a running Claude SDK process or unread content
    // are always surfaced in the sidebar — even when they belong to a project
    // outside the current filter/workspace.
    showActiveAcrossFilters: {
        type: Boolean,
        default: false
    },
    // "Show as tree" mode: sessions are nested under the session that spawned them.
    // Ignored (flat list) while a search query is active.
    showAsTree: {
        type: Boolean,
        default: false
    },
    // Show the provider icon on each row.
    showProviderIcons: {
        type: Boolean,
        default: true
    }
})

const store = useDataStore()
const workspacesStore = useWorkspacesStore()
const selectionStore = useSessionSelectionStore()
const expansion = useSessionTreeExpansionStore()
const route = useRoute()

// Natural scope project IDs for the current sidebar filter, passed down so
// SessionListItem can flag any session whose project falls outside it as
// cross-filter (and show its project badge accordingly). null = all-projects
// mode (no scope restriction).
const scopeProjectIds = computed(() => {
    if (isWorkspaceProjectId(props.projectId)) {
        const wsId = extractWorkspaceId(props.projectId)
        return workspacesStore.getVisibleProjectIds(wsId)
    }
    if (props.projectId === ALL_PROJECTS_ID) return null
    // A main repo's scope also covers its worktrees, so their sessions aren't
    // flagged as cross-filter and the project badge is shown to tell them apart.
    return store.getProjectScopeIds(props.projectId)
})

// The single real project the sidebar is filtering on, or null in workspace /
// all-projects mode. Passed down so SessionListItem can hide the worktree
// marker only when we're filtering directly on that worktree (where every
// session already belongs to it, so the marker would be noise).
const filterProjectId = computed(() => {
    if (isWorkspaceProjectId(props.projectId)) return null
    if (props.projectId === ALL_PROJECTS_ID) return null
    return props.projectId
})

// The currently "active" workspace for cross-filter `workspace`-mode pins. Set
// when the sidebar is on a workspace view (projectId `workspace:X`) OR when a
// single-project view preserves the workspace via the `?workspace=X` query.
const activeWorkspaceId = computed(() => {
    if (isWorkspaceProjectId(props.projectId)) {
        return extractWorkspaceId(props.projectId)
    }
    return route.query.workspace || null
})

// Tree mode is off while a query is active: the results are the flat blocks, each session
// classified and sorted by its own state.
const treeActive = computed(() => props.showAsTree && !props.searchQuery.trim())

// All four sidebar blocks, computed in one shot by the shared helper so the
// command palette's "Go to Session…" sub-picker can use the exact same
// ordering / filtering / grouping logic.
const sessionBlocks = computed(previous => computeSidebarSessionBlocks({
    data: store,
    workspaces: workspacesStore,
    effectiveProjectId: props.projectId,
    activeWorkspaceId: activeWorkspaceId.value,
    sessionId: props.sessionId,
    showArchived: props.showArchived,
    showArchivedProjects: props.showArchivedProjects,
    showActiveAcrossFilters: props.showActiveAcrossFilters,
    showAsTree: props.showAsTree,
}, previous))

const extraSessionId = computed(() => sessionBlocks.value.extra?.id ?? null)

// Flat list consumed by the virtual scroller:
//   [extra?, ...crossFilterPinned, ...crossFilterActive, ...natural]
// Section separators live in the template, keyed off `separatorBeforeIds`.
const natural = computed(() => sessionBlocks.value.natural)

// The session tree (null in flat mode). It reads every candidate's `mtime`, so it is
// recomputed on each write of an active session; the order does not change and
// `treeRowMeta` retains the unchanged row metadata.
const tree = computed(() => treeActive.value
    ? buildSessionTree(natural.value, {
        lookup: id => store.sessions[id] ?? null,
        processStates: store.processStates,
        viewedId: props.sessionId,
        isUnread: isSessionUnread,
    })
    : null)

// Fetch the tree ancestors that are not loaded yet.
useSessionTreeAncestors(natural, treeActive)

// An untouched node with an active, unread or pinned descendant expands (spec §9).
watch(tree, (t) => {
    if (t) expansion.reconcile(t)
}, { immediate: true })

const treeRows = computed(() => tree.value ? flattenTree(tree.value, expansion.isExpanded) : null)

const NO_TREE_ROWS = new Map()
const ACTIVITY_KEYS = ['processCount', 'pendingRequestCount', 'hasAssistantTurn', 'activeCronCount',
    'backgroundShellCount', 'unreadCount']
const sameActivity = (a, b) => a === b || (!!a && !!b && ACTIVITY_KEYS.every(key => a[key] === b[key]))
const sameThrough = (a, b) => a.length === b.length && a.every((value, i) => value === b[i])
const sameRowMeta = (a, b) => a.depth === b.depth && a.isLast === b.isLast && sameThrough(a.through, b.through)
    && a.hasChildren === b.hasChildren && a.expanded === b.expanded && a.hint === b.hint
    && a.sameProjectAsParent === b.sameProjectAsParent
    && sameActivity(a.activity, b.activity)
// Per-row tree metadata. An unchanged entry keeps its previous object, so an mtime tick
// of an active session does not re-render every row. `activity` is the cumulative activity
// (the node and every descendant) of a collapsed parent, computed only for those rows.
const treeRowMeta = computed(previous => {
    if (!tree.value) return NO_TREE_ROWS
    const meta = new Map()
    for (const { id, depth, isLast, through } of treeRows.value) {
        const node = tree.value.nodes.get(id)
        const hasChildren = node.childIds.length > 0
        const expanded = expansion.isExpanded(id)
        const next = {
            depth,
            isLast,
            through,
            hasChildren,
            sameProjectAsParent: sharesProjectWithParent(tree.value, id),
            expanded,
            activity: hasChildren && !expanded
                ? treeActivity([id, ...node.descendantIds], {
                    processStates: store.processStates,
                    sessions: sessionId => store.sessions[sessionId] ?? null,
                    viewedId: props.sessionId,
                    isUnread: isSessionUnread,
                })
                : null,
            hint: depth === 0 && node.hintParent ? getSessionDisplayName(node.hintParent) : null,
        }
        const old = previous?.get(id)
        meta.set(id, old && sameRowMeta(old, next) ? old : next)
    }
    return meta
})

const allSessions = computed(previous => {
    const { extra, crossFilterPinned, crossFilterActive } = sessionBlocks.value
    const result = []
    if (extra) result.push(extra)
    result.push(...crossFilterPinned)
    result.push(...crossFilterActive)
    if (treeRows.value) result.push(...treeRows.value.map(row => store.sessions[row.id]))
    else result.push(...natural.value)
    return retainSessionArray(previous, result)
})

// Filtered sessions based on the search query. Fuzzy by default; exact
// substring when the query is wrapped/prefixed with `"` or `'`.
const sessions = computed(previous => {
    const query = props.searchQuery.trim()
    if (!query) return allSessions.value

    const next = allSessions.value.filter(session => {
        return matchQuery(query, getSessionDisplayName(session))
    })
    return retainSessionArray(previous, next)
})

// One labeled separator before each group of the list (replacing the old
// inter-block wa-dividers). The top blocks each form a section; the `natural`
// block is sub-bucketed pinned → active → date threshold (Last 24 hours, then
// the bulk-archive "older than <X>" cutoffs — each marking exactly what the
// matching archive option would select).
//
// A Map of session id -> separator props ({ label, prefix?, title? }), consumed
// in the scroller slot to render a <SidebarListSeparator> *before* the matching
// item. Classification is built over every session, then walked over the filtered
// `sessions` so a separator lands on the first *visible* session of each section
// (search stays correct). Two adjacent separators are intentionally not
// deduplicated for now.
const separatorBeforeIds = computed(() => {
    const { extra, crossFilterPinned, crossFilterActive, natural } = sessionBlocks.value
    const rootIds = tree.value ? new Set(tree.value.rootIds) : null
    const processStates = store.processStates
    const nowMs = Date.now()

    // id -> { sectionKey, entry } — entry is the separator to render before the
    // section's first visible session.
    const classified = new Map()
    if (extra) classified.set(extra.id, { sectionKey: 'extra', entry: { label: 'Selected' } })
    for (const s of crossFilterPinned) {
        classified.set(s.id, { sectionKey: 'xpinned', entry: { label: 'Pinned elsewhere' } })
    }
    for (const s of crossFilterActive) {
        classified.set(s.id, { sectionKey: 'xactive', entry: { label: 'Active elsewhere' } })
    }
    for (const s of natural) {
        // Tree mode: only the roots are classified, from their effective state.
        if (rootIds && !rootIds.has(s.id)) continue
        const effective = rootIds ? tree.value.nodes.get(s.id).effective : null
        let sectionKey, entry
        if (s.ephemeral && !s.draft) {
            sectionKey = 'n-ephemeral'
            entry = { label: 'Ephemeral' }
        } else if (effective ? effective.pinned : s.pinned) {
            sectionKey = 'n-pinned'
            entry = { label: 'Pinned' }
        } else if (effective ? effective.active : processStates[s.id] != null) {
            sectionKey = 'n-active'
            entry = { label: 'Active' }
        } else {
            const bucket = dateBucketSeparator(effective ? effective.dateKey : s.mtime, nowMs)
            sectionKey = `n-${bucket.key}`
            entry = bucket.entry
        }
        classified.set(s.id, { sectionKey, entry })
    }

    // A separator marks the first visible session of each section.
    const map = new Map()
    let prevSection = null
    for (const session of sessions.value) {
        const c = classified.get(session.id)
        if (!c) continue
        if (c.sectionKey !== prevSection) {
            prevSection = c.sectionKey
            map.set(session.id, c.entry)
        }
    }
    return map
})

// Pagination state
const hasMore = computed(() => store.hasMoreSessions(props.projectId))
const isLoading = computed(() => store.areSessionsLoading(props.projectId))

// Local error state for "load more" failures (not initial load)
const loadMoreError = ref(false)
// A successful unchanged page stops chaining until a separate pagination opportunity.
const paginationNoProgress = ref(false)

// Virtual scroller configuration
// Session items have relatively uniform height (~80-100px normal, ~35-40px compact)
const minSessionHeight = computed(() => props.compactView ? 35 : 70)
const SCROLLER_BUFFER = 300

// Reference to the VirtualScroller component
const scrollerRef = ref(null)

// Keyboard navigation: highlighted item index (-1 = none)
const highlightedIndex = ref(-1)

// Number of items to jump for PageUp/PageDown
const PAGE_SIZE = 10

// Entrances (visual refresh step 5c): the rows on screen cascade in when a scope's list
// arrives; a session that appears live enters alone. Declared before the sessionId
// watchers: the scrolling one runs during setup and holds the cascade's start.
const cascade = useListCascade({
    items: sessions,
    getKey: (s) => s.id,
    sourceSize: () => allSessions.value.length,
    scopeKey: () => props.projectId,
    getVisibleRange: () => scrollerRef.value?.getVisibleRange() ?? null,
})

// Exits (visual refresh retouches): a row that was on screen and leaves the list because its session
// was archived stays for its exit animation (it fades, slides out and folds its height), so the rows
// below glide up instead of jumping. Not for a scope, search or filter change: those lists just swap.
const exits = useListExit({
    items: sessions,
    getKey: (s) => s.id,
    // A row that only leaves the list because its parent collapsed is still a tree node: no exit.
    isEligible: (s) => !!store.sessions[s.id]?.archived && (!tree.value || !tree.value.nodes.has(s.id)),
    scopeKey: () => [props.projectId, props.searchQuery, props.showArchived, props.showArchivedProjects,
        props.showActiveAcrossFilters, activeWorkspaceId.value, props.showAsTree].join('|'),
    getVisibleRange: () => scrollerRef.value?.getVisibleRange() ?? null,
    getHeight: (key) => scrollerRef.value?.getItemHeight(key) ?? 0,
})
const { displayItems } = exits
const rowClass = (session) => exits.exitClass(session) ?? cascade.itemClass(session)
const rowStyle = (session) => exits.exitStyle(session) ?? cascade.itemStyle(session)

// Gliding open-row fill (visual refresh step 6a-bis, design:
// docs/plans/2026-09-29-sidebar-row-glide-design.md): the open row's lit look is an ink in
// the scroller root that glides from row to row (styles/sidebar-rows.css). It takes the box
// of the open button's base part, minus its entrance translate. The rows are observed: a row
// mounted by the scroller renders its button a microtask later and re-places the ink then.
// The visible range: the open row unmounted by the scroller hides the ink, remounted snaps.
const inkRef = ref(null)
const scrollerEl = computed(() => scrollerRef.value?.$el ?? null)
useSidebarScrollbar(scrollerEl, 'sessions')
useGlideInk({
    container: scrollerEl,
    flushTarget: inkRef,
    getActive: () => activeRowBase(scrollerEl.value),
    getActiveOffset: () => entranceOffset(scrollerEl.value),
    getItems: () => [...(scrollerEl.value?.querySelectorAll('.sidebar-row') ?? [])],
    sources: [() => props.sessionId, sessions, () => props.compactView,
              () => scrollerRef.value?.getVisibleRange()],
    resetKey: () => props.projectId,
})

// Live ids: a session received from the server or created in this tab. A Codex draft's
// canonical row replaces a row the user already sees: it must not enter. In a tab that
// never held the draft the new row keeps its entrance.
store.$onAction(({ name, args, after }) => {
    if (name === 'addSession') {
        cascade.noteLive(args[0]?.id)
    } else if (name === 'createDraftSession') {
        after((id) => cascade.noteLive(id))
    } else if (name === 'bindDraftSession') {
        const [draftId, sessionId] = args
        if (draftId !== sessionId && store.sessions[draftId]) cascade.dropLive(sessionId)
    }
})

// Pagination follows measured scope ownership, not repeated geometry notifications.
const paginationMounted = ref(false)
const paginationActive = ref(true)
const paginationRange = ref(null)
const paginationMeasurementRevision = ref(0)
let paginationGeneration = 0
let paginationRequest = null
let pendingPaginationGeneration = null
let externalPageStart = null
let paginationScrollTop = null
const paginationReady = computed(() => {
    // DOM height is not reactive. Measured updates and native scrolls refresh this cached decision.
    paginationMeasurementRevision.value
    return paginationMounted.value && paginationActive.value &&
        !!scrollerRef.value && !unref(scrollerRef.value.suspended) && scrollerRef.value.getScrollState().clientHeight > 0
})

// Match the store's natural scope before archive/search/cross-filter display rules.
const canonicalSessions = computed(previous => {
    const ids = scopeProjectIds.value
    if (!ids) return store.getAllSessions
    if (!isWorkspaceProjectId(props.projectId) && ids.length === 1) return store.getProjectSessions(props.projectId)
    const scope = new Set(ids)
    return retainSessionArray(previous, store.getAllSessions.filter(session => scope.has(session.project_id)))
})
const paginationSnapshot = computed(() => ({
    cursor: store.localState.projects[props.projectId]?.oldestSessionMtime ?? null,
    ids: new Set(canonicalSessions.value.map(session => session.id)),
}))

function canPaginate() {
    return paginationReady.value && scrollerRef.value.getScrollState().clientHeight > 0
}

async function loadMore() {
    if (!canPaginate() || !paginationRange.value || !hasMore.value || loadMoreError.value || paginationNoProgress.value) return
    if (paginationRequest) {
        if (paginationRequest.generation !== paginationGeneration) pendingPaginationGeneration = paginationGeneration
        return
    }
    if (sessions.value.length - paginationRange.value.end >= 10) return
    const before = externalPageStart?.projectId === props.projectId ? externalPageStart.snapshot : paginationSnapshot.value
    const owner = { generation: paginationGeneration, before }
    externalPageStart = null
    paginationRequest = owner
    const current = () => paginationRequest === owner && owner.generation === paginationGeneration && canPaginate()
    let progressed = false
    try {
        // Join an external load, including its finishing phase. Awaiting the public
        // promise lets the store remove its in-flight entry before we chain.
        await store.loadSessions(props.projectId)
        if (!current()) return
        progressed = hasScrollerPaginationProgress(owner.before, paginationSnapshot.value)
        if (hasMore.value && !progressed) paginationNoProgress.value = true
    } catch {
        if (current()) loadMoreError.value = true
    } finally {
        const ownsSettlement = current()
        paginationRequest = null
        const currentOpportunity = pendingPaginationGeneration === paginationGeneration
        pendingPaginationGeneration = null
        if ((ownsSettlement && progressed) || currentOpportunity) {
            const generation = paginationGeneration
            await nextTick()
            if (generation === paginationGeneration) loadMore()
        }
    }
}

function handleRetry() {
    loadMoreError.value = false
    paginationNoProgress.value = false
    return loadMore()
}

function requestPaginationOpportunity() {
    paginationNoProgress.value = false
    loadMore()
}

function onScrollerUpdate({ visibleStartIndex, visibleEndIndex }) {
    paginationMeasurementRevision.value++
    const previous = paginationRange.value
    paginationRange.value = { start: visibleStartIndex, end: visibleEndIndex }
    if (paginationScrollTop === null) paginationScrollTop = scrollerRef.value?.getScrollState().scrollTop ?? null
    if (!previous || previous.start !== visibleStartIndex || previous.end !== visibleEndIndex) requestPaginationOpportunity()
    else loadMore()
}

function onScrollerScroll(event) {
    paginationMeasurementRevision.value++
    if (!canPaginate()) return
    // VirtualScroller forwards the native event. Equal range indices can still contain real scrolling.
    const scrollTop = event.target?.scrollTop
    if (!Number.isFinite(scrollTop) || scrollTop === paginationScrollTop) return
    paginationScrollTop = scrollTop
    requestPaginationOpportunity()
}

// Retain the baseline from loading START, not the already-merged finishing page.
watch(isLoading, loading => {
    if (loading && !paginationRequest) externalPageStart = { projectId: props.projectId, snapshot: paginationSnapshot.value }
    // _doLoadSessions publishes true before loadSessions registers its promise.
    // Capture now, but never reenter the store from this synchronous watcher.
    const generation = paginationGeneration
    nextTick(() => { if (generation === paginationGeneration) loadMore() })
}, { immediate: true, flush: 'sync' })
watch([paginationSnapshot, hasMore], () => loadMore())
// Compare membership values, not newly allocated arrays or unrelated session fields.
// Displayed membership supplies opportunities; canonical IDs/cursor still decide completed-page progress.
watch(() => JSON.stringify(sessions.value.map(session => session.id)), requestPaginationOpportunity)
watch(() => JSON.stringify([...paginationSnapshot.value.ids].sort()), requestPaginationOpportunity)

async function refreshPaginationScope() {
    const generation = ++paginationGeneration
    paginationRange.value = null
    paginationScrollTop = null
    loadMoreError.value = false
    paginationNoProgress.value = false
    await nextTick()
    if (generation !== paginationGeneration || !canPaginate()) return
    paginationRange.value = scrollerRef.value.getVisibleRange()
    loadMore()
}
watch([paginationReady, () => props.projectId, () => props.searchQuery, () => props.showArchived,
    () => props.showArchivedProjects, () => props.showActiveAcrossFilters, activeWorkspaceId,
    () => scopeProjectIds.value?.join('|')], refreshPaginationScope, { flush: 'sync' })
onMounted(() => { paginationMounted.value = true })
onActivated(() => { paginationActive.value = true })
onDeactivated(() => { paginationActive.value = false })
onBeforeUnmount(() => { paginationMounted.value = false })

// Reset scroll to top and highlight when project changes
watch(() => props.projectId, () => {
    loadMoreError.value = false
    highlightedIndex.value = -1
    if (scrollerRef.value) {
        scrollerRef.value.scrollToTop()
    }
})

// Reset highlight when search query changes
watch(() => props.searchQuery, () => {
    highlightedIndex.value = -1
})

// Reset highlight when selected session changes.
watch(() => props.sessionId, (newSessionId) => {
    highlightedIndex.value = -1
})

// Scroll to the selected session after DOM update.
// Uses flush:'post' because the VirtualScroller has :key="projectId" — when both
// projectId and sessionId change simultaneously (e.g., navigating from search results),
// the scroller is destroyed and recreated. A pre-flush watcher would scroll the OLD
// scroller. flush:'post' ensures the new scroller is mounted and has run its
// onMounted/syncScrollPosition before we attempt to scroll.
// Uses immediate:true because navigating from single-project (/project/X) to all-projects
// (/projects/X/session/Y) remounts the entire component tree (different route branches).
// Without immediate, the watcher wouldn't fire for the initial sessionId value on mount.
// The cascade's start waits (capped) for this scroll when the session is off screen.
watch(() => props.sessionId, (newSessionId) => {
    if (newSessionId) {
        cascade.holdTarget(newSessionId, scrollToSession(newSessionId))
    }
}, { flush: 'post', immediate: true })

// Sessions that leave the visible list (filter change, archived away, …)
// are dropped from the multi-select selection.
watch(sessions, (list) => {
    if (!selectionStore.active) return
    selectionStore.prune(new Set(list.map(s => s.id)))
})

// Publish the rendered list (in order, search filter included) so the
// Ctrl+Shift+` switcher can offer exactly what's on screen. Cleared on unmount
// so a stale list never lingers when the sidebar is gone.
// With a tree, the sessions under collapsed nodes are offered too (navigation aid).
const switcherIds = computed(() => {
    if (!tree.value) return sessions.value.map(s => s.id)
    const { extra, crossFilterPinned, crossFilterActive } = sessionBlocks.value
    return [
        ...(extra ? [extra.id] : []),
        ...crossFilterPinned.map(s => s.id),
        ...crossFilterActive.map(s => s.id),
        ...flattenTree(tree.value, () => true).map(row => row.id),
    ]
})
watch(switcherIds, (ids) => {
    store.setDisplayedSessionIds(ids)
}, { immediate: true })
onBeforeUnmount(() => store.setDisplayedSessionIds([]))

// Reveal the selected session in the tree: expand its collapsed ancestors. The mark is the
// last revealed (session, ancestor path). A deep-linked child first renders as a root, then
// its ancestors arrive one fetch at a time: each arrival changes the path and reveals again.
// Pre-flush, so the row exists when `scrollToSession` needs it.
function ancestorPath(t, id) {
    const path = []
    const visited = new Set([id])
    let parentId = t.nodes.get(id)?.parentId ?? null
    while (parentId && !visited.has(parentId)) {
        visited.add(parentId)
        path.unshift(parentId)
        parentId = t.nodes.get(parentId)?.parentId ?? null
    }
    return path
}
let lastRevealed = null
// A re-enabled tree or a scope change is a fresh first render: forget the reveal mark.
watch([treeActive, () => props.projectId], () => { lastRevealed = null })
const revealRescroll = shallowRef(null)
watch([() => props.sessionId, tree], ([id, t]) => {
    if (!t || !id || !t.nodes.has(id)) return
    const path = ancestorPath(t, id)
    const key = path.join('>')
    if (lastRevealed && lastRevealed.id === id && lastRevealed.key === key) return
    const sameSession = lastRevealed?.id === id
    const previousPath = sameSession ? lastRevealed.path : []
    lastRevealed = { id, key, path }
    expansion.reveal(t, id, previousPath)
    if (sameSession) revealRescroll.value = { id }
}, { immediate: true })
// `scrollToSession` gives up after a few retries and the row has moved: run it again once the
// rows are rendered (the scroller searches its `items`, updated after the render).
watch(revealRescroll, (mark) => {
    const id = mark?.id
    if (id && id === props.sessionId) cascade.holdTarget(id, scrollToSession(id))
}, { flush: 'post' })

/**
 * Scroll the session list to reveal a session, only as far as needed: no scroll when its
 * entry (label and row) lies inside the list's zone, the visible area minus the reveal
 * bands (styles/sidebar-rows.css; the bottom one adds the part the floating "New session"
 * button covers), otherwise the minimal scroll to the nearer band edge (design:
 * docs/plans/2026-09-29-sidebar-row-glide-design.md §10).
 * Retries a few times because the VirtualScroller may be recreated (via :key)
 * when projectId changes simultaneously with sessionId, and the new scroller
 * needs time to mount and measure items.
 * Resolves when the scroll settles, or when the retries give up.
 */
function scrollToSession(targetSessionId, attempt = 0) {
    const MAX_ATTEMPTS = 5
    const RETRY_DELAY = 50

    // A retry for a session that is no longer open must not start a reveal: it would
    // cancel the newer one.
    if (targetSessionId !== props.sessionId) return Promise.resolve()

    const retry = () => new Promise((resolve) => {
        setTimeout(() => resolve(scrollToSession(targetSessionId, attempt + 1)), RETRY_DELAY)
    })

    if (!sessions.value.some(s => s.id === targetSessionId)) {
        // Session not in list yet (data loading). Retry a few times.
        return attempt < MAX_ATTEMPTS ? retry() : Promise.resolve()
    }

    if (!scrollerRef.value) {
        // Scroller not mounted yet (recreated via :key). Retry.
        return attempt < MAX_ATTEMPTS ? retry() : Promise.resolve()
    }

    // The bands, read once per call. The scroller's positions start at 0 without the list's
    // top padding: the margins are shifted by it so the zone edges are real pixels.
    const pad = parseFloat(getComputedStyle(scrollerEl.value).paddingTop) || 0
    const { marginTop, marginBottom } = revealMargins(revealBands(scrollerEl.value), pad)

    // Use the VirtualScroller's scrollToKey which has a robust "jump, settle, correct"
    // loop: it scrolls to the item, waits for ResizeObserver height measurements to
    // stabilize, then verifies the item is in its zone and re-scrolls if needed. This
    // handles all timing issues when the scroller was just recreated (via :key on
    // projectId change). Any newer intent ends it: another open session (or none), a
    // newer reveal, a user scroll, keyboard navigation. A short move scrolls smoothly (§10.7),
    // never while the list's arrival cascade is pending or playing.
    return scrollerRef.value.scrollToKey(targetSessionId, {
        align: 'nearest',
        marginTop,
        marginBottom,
        isCurrent: () => targetSessionId === props.sessionId,
        allowSmooth: !cascade.isArriving(),
    }).then(() => {}, () => {})
}

const emit = defineEmits(['select', 'drop-data', 'focus-search'])

function handleSelect(session) {
    // In multi-select mode, opening a session also anchors it: a following
    // Shift+click ranges from the last clicked item (file-manager semantics),
    // even though the plain click doesn't touch the selection itself.
    if (selectionStore.active) {
        selectionStore.setAnchor(session.id)
    }
    emit('select', session)
}

function onToggleTree(session) {
    if (tree.value) expansion.toggle(session.id)
}

function handleDropData(data) {
    emit('drop-data', data)
}

/**
 * Handle a modifier click forwarded by SessionListItem while the
 * multi-select mode is active. Ranges follow the visual order of the
 * filtered flat list (`sessions`), crossing block dividers.
 *
 * Semantics (standard Windows/macOS list selection):
 * - Plain click (open)        → sets the anchor without touching the selection (see handleSelect)
 * - Ctrl/Cmd+click            → toggle the item, it becomes the anchor
 * - Shift+click               → select anchor→target range, REPLACING the selection
 * - Ctrl/Cmd+Shift+click      → ADD the anchor→target range to the selection
 * - Shift+click with no anchor → select the item alone and anchor it
 */
function handleSelectionClick({ session, shift, ctrl }) {
    if (!shift) {
        selectionStore.toggle(session.id)
        return
    }
    const ids = sessions.value.map(s => s.id)
    const targetIndex = ids.indexOf(session.id)
    if (targetIndex === -1) return
    const anchorIndex = selectionStore.anchorId ? ids.indexOf(selectionStore.anchorId) : -1
    if (anchorIndex === -1) {
        // No usable anchor (never set, or pruned out of the visible list):
        // anchor the clicked item. Ctrl keeps the existing selection (adds),
        // plain Shift replaces it.
        selectionStore.setSelection(
            ctrl ? [...selectionStore.selectedIds, session.id] : [session.id],
            { anchor: session.id },
        )
        return
    }
    const [from, to] = anchorIndex <= targetIndex
        ? [anchorIndex, targetIndex]
        : [targetIndex, anchorIndex]
    const range = ids.slice(from, to + 1)
    if (ctrl) selectionStore.addSelection(range)
    else selectionStore.setSelection(range)
}

/**
 * Get the starting index for keyboard navigation.
 * If a session is highlighted, use that. Otherwise, use the selected session's index.
 * Returns -1 if neither is available.
 */
function getNavigationStartIndex() {
    if (highlightedIndex.value >= 0) {
        return highlightedIndex.value
    }
    // No highlight - try to start from selected session
    if (props.sessionId) {
        const selectedIndex = sessions.value.findIndex(s => s.id === props.sessionId)
        if (selectedIndex >= 0) {
            return selectedIndex
        }
    }
    return -1
}

/**
 * Handle keyboard navigation from the search input or the list itself.
 * Navigates through sessions with arrow keys and selects with Enter.
 *
 * @param {KeyboardEvent} event - The keyboard event
 * @param {Object} [options] - Navigation options
 * @param {boolean} [options.fromSearch=false] - True when called from the search input.
 *   When true, navigation ignores the selected session and always starts from scratch
 *   (e.g., ArrowDown goes to the first item, not relative to the active session).
 * @returns {boolean} True if the event was handled (should preventDefault)
 */
function handleKeyNavigation(event, { fromSearch = false } = {}) {
    // Escape priority: exit multi-select mode > clear highlight > (parent:
    // clear search). Checked before the empty-list early return so the mode
    // can be exited even when the filter matches nothing.
    if (event.key === 'Escape' && selectionStore.active) {
        selectionStore.exit()
        return true
    }

    const count = sessions.value.length
    if (count === 0) return false

    const key = event.key
    // When coming from the search input with no highlight, always start from
    // scratch (-1) so that ArrowDown goes to the first item, not relative to
    // the currently selected session.
    const startIndex = (fromSearch && highlightedIndex.value < 0) ? -1 : getNavigationStartIndex()
    let newIndex = highlightedIndex.value

    switch (key) {
        case 'ArrowDown':
            // Move down from current position, or start at first item
            newIndex = startIndex < 0 ? 0 : Math.min(startIndex + 1, count - 1)
            break

        case 'ArrowUp':
            // If already at the first item, move focus back to the search input
            if (startIndex === 0) {
                highlightedIndex.value = -1
                emit('focus-search')
                return true
            }
            // Move up from current position, or start at last item
            newIndex = startIndex < 0 ? count - 1 : Math.max(startIndex - 1, 0)
            break

        case 'Home':
            newIndex = 0
            break

        case 'End':
            newIndex = count - 1
            break

        case 'PageDown':
            newIndex = startIndex < 0 ? PAGE_SIZE - 1 : Math.min(startIndex + PAGE_SIZE, count - 1)
            break

        case 'PageUp':
            // If already at the first item, move focus back to the search input
            if (startIndex === 0) {
                highlightedIndex.value = -1
                emit('focus-search')
                return true
            }
            newIndex = startIndex < 0 ? 0 : Math.max(startIndex - PAGE_SIZE, 0)
            break

        case 'Enter':
            // Select the highlighted session
            if (highlightedIndex.value >= 0 && highlightedIndex.value < count) {
                handleSelect(sessions.value[highlightedIndex.value])
                return true
            }
            return false

        case 'Escape':
            // Clear highlight if any, otherwise let parent handle it (e.g., clear search)
            if (highlightedIndex.value >= 0) {
                highlightedIndex.value = -1
                return true
            }
            return false

        default:
            return false
    }

    // Update highlight and scroll to it
    if (newIndex !== highlightedIndex.value) {
        highlightedIndex.value = newIndex
        if (scrollerRef.value) {
            // For Home/End, use the scroller's native methods which work better
            // For other navigation, scroll to make the item visible
            if (key === 'Home') {
                scrollerRef.value.scrollToTop()
            } else if (key === 'End') {
                // scrollToBottom() uses estimated heights for unmeasured items,
                // which may not scroll far enough. After the initial scroll,
                // wait for items to be rendered AND measured by ResizeObserver.
                // ResizeObserver is async and not tied to Vue's nextTick, so we use
                // a small timeout to allow measurements to complete.
                scrollerRef.value.scrollToBottom()
                setTimeout(() => {
                    scrollerRef.value?.scrollToIndex(newIndex, { align: 'end' })
                }, 50)
            } else if (key === 'PageDown' || key === 'PageUp') {
                // Page navigation may jump to unmeasured items, use delayed correction
                scrollToIndexIfNeeded(newIndex, { delayedCorrection: true })
            } else {
                // For arrow keys, items are usually already measured (adjacent to visible)
                scrollToIndexIfNeeded(newIndex)
            }

            // Ensure focus stays on the list after scroll (items may be re-rendered)
            // Use nextTick to wait for Vue to update the DOM
            nextTick(() => {
                scrollerRef.value?.$el?.focus()
            })
        }
    }
    return true
}

/**
 * Scroll to an index only if it's not already fully visible in the viewport.
 * Uses align 'start' or 'end' depending on scroll direction.
 *
 * @param {number} index - The item index to scroll to
 * @param {Object} [options] - Options
 * @param {boolean} [options.delayedCorrection=false] - If true, re-scroll after a delay
 *        to account for items that weren't measured yet (heights were estimated)
 */
function scrollToIndexIfNeeded(index, { delayedCorrection = false } = {}) {
    if (!scrollerRef.value) return

    // Get the actual visible range from the scroller (based on measured heights)
    // visibleEnd is exclusive and may include a partially visible item at the bottom
    const { start: visibleStart, end: visibleEnd } = scrollerRef.value.getVisibleRange()

    let scrolled = false
    let align = null

    if (index < visibleStart) {
        // Item is above the viewport
        align = 'start'
        scrollerRef.value.scrollToIndex(index, { align })
        scrolled = true
    } else if (index >= visibleEnd) {
        // Item is below the viewport
        align = 'end'
        scrollerRef.value.scrollToIndex(index, { align })
        scrolled = true
    }

    // If we scrolled and correction is requested, re-scroll after items are measured
    // This handles the case where we scroll to unmeasured items with estimated heights
    if (scrolled && delayedCorrection && align) {
        setTimeout(() => {
            scrollerRef.value?.scrollToIndex(index, { align })
        }, 50)
    }
}

/**
 * Handle keydown events directly on the session list container.
 * This allows keyboard navigation when focus is in the list (not just the search input).
 *
 * @param {KeyboardEvent} event
 */
function handleListKeydown(event) {
    // Only handle navigation keys
    const navigationKeys = ['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageUp', 'PageDown', 'Enter', 'Escape']
    if (!navigationKeys.includes(event.key)) return

    const handled = handleKeyNavigation(event)
    if (handled) {
        event.preventDefault()
    }
}

// Expose methods for parent component access via ref
defineExpose({
    handleKeyNavigation,
})
</script>

<template>
    <div class="session-list-container" :class="{ 'session-list-container--compact': compactView }">
        <!-- Empty state: no sessions at all -->
        <div v-if="allSessions.length === 0 && !isLoading" class="empty-state" :class="{ 'pagination-empty': hasMore }">
            No sessions
        </div>

        <!-- Empty state: no matching sessions (search returned nothing) -->
        <div v-else-if="displayItems.length === 0 && !isLoading" class="empty-state" :class="{ 'pagination-empty': hasMore }">
            No matching sessions
        </div>

        <!-- Session list with virtual scroller -->
        <VirtualScroller
            v-if="displayItems.length > 0 || hasMore || isLoading"
            ref="scrollerRef"
            :key="projectId"
            :items="displayItems"
            :item-key="session => session.id"
            :item-class="rowClass"
            :item-style="rowStyle"
            :min-item-height="minSessionHeight"
            :buffer="SCROLLER_BUFFER"
            :unload-buffer="SCROLLER_BUFFER * 1.5"
            class="session-list sidebar-row-list"
            :class="{ 'sidebar-row-list--compact': compactView }"
            tabindex="0"
            @update="onScrollerUpdate"
            @scroll="onScrollerScroll"
            @keydown="handleListKeydown"
        >
            <template #before><span ref="inkRef" class="glide-ink" aria-hidden="true"></span></template>
            <template #default="{ item: session, index }">
                <SidebarListSeparator
                    v-if="separatorBeforeIds.has(session.id)"
                    v-bind="separatorBeforeIds.get(session.id)"
                />
                <SessionListItem
                    :session="session"
                    :active="session.id === sessionId"
                    :highlighted="index === highlightedIndex"
                    :compact-view="compactView"
                    :show-provider-icon="showProviderIcons"
                    :show-project-name="showProjectName"
                    :scope-project-ids="scopeProjectIds"
                    :filter-project-id="filterProjectId"
                    :tree-row="treeRowMeta.get(session.id) ?? null"
                    :can-detach-from-tree="showAsTree"
                    @toggle-tree="onToggleTree"
                    @select="handleSelect"
                    @drop-data="handleDropData"
                    @selection-click="handleSelectionClick"
                />
            </template>
        </VirtualScroller>

        <!-- No-progress Retry overlays the list so its appearance cannot create a pagination range opportunity. -->
        <div
            v-if="loadMoreError || paginationNoProgress"
            class="load-more-error"
            :style="paginationNoProgress && !loadMoreError ? { position: 'absolute', insetInline: 0, bottom: 0, zIndex: 1 } : null"
        >
            <wa-callout :variant="loadMoreError ? 'danger' : 'neutral'">
                <span>{{ loadMoreError ? 'Failed to load more sessions' : 'No additional sessions received' }}</span>
                <wa-button
                    slot="footer"
                    :variant="loadMoreError ? 'danger' : 'neutral'"
                    appearance="outlined"
                    size="small"
                    :loading="isLoading"
                    @click="handleRetry"
                >
                    <wa-icon name="arrow-rotate-right" slot="start"></wa-icon>
                    Retry
                </wa-button>
            </wa-callout>
        </div>

        <!-- Loading indicator (shown at bottom when loading more) -->
        <div v-if="isLoading && sessions.length > 0" class="load-more-indicator">
            <wa-spinner></wa-spinner>
        </div>

    </div>
</template>

<style scoped>
.session-list-container {
    position: relative;
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
    container-type: inline-size;
    container-name: session-list;
}

.pagination-empty {
    position: absolute;
    inset: 0;
    pointer-events: none;
}

.session-list {
    flex: 1;
    min-height: 0;
    scrollbar-gutter: stable;
    /* Rows fade out at the top and bottom edges instead of being cut: always on, not tied to the
       scroll position. The padding equals the fade, so nothing is faded at rest. */
    --sidebar-list-fade: var(--wa-space-xs);
    padding-block: var(--sidebar-list-fade);
    -webkit-mask-image: linear-gradient(to bottom, transparent, #000 var(--sidebar-list-fade), #000 calc(100% - var(--sidebar-list-fade)), transparent);
    mask-image: linear-gradient(to bottom, transparent, #000 var(--sidebar-list-fade), #000 calc(100% - var(--sidebar-list-fade)), transparent);
    padding-inline-start: var(--sidebar-gutter, 0px);
    padding-inline-end: 0; /* the scrollbar sits at the sidebar's right edge */
    --sidebar-row-inset: 0px;
    --sidebar-separator-inset: 0px;
    /* The floating "New session" button covers the list's last ~3.08rem (measured: 35px +
       11.25px at a 15px root), rounded up to 3.125rem; a row under it is not visible. The
       open row's reveal keeps clear of it (styles/sidebar-rows.css, the reveal bands). */
    --sidebar-row-reveal-cover: 3.125rem;
}

/* Remove default focus outline on the list - we show highlight on items instead */
.session-list:focus {
    outline: none;
}

/* Gap between items (non-compact mode only).
   Targets the child component's root element via Vue's scoped CSS inheritance. */
.session-list-container:not(.session-list-container--compact) :deep(.session-item-wrapper) {
    margin-block: var(--wa-space-3xs);
}

.load-more-indicator {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s);
    flex-shrink: 0;
}

.load-more-error {
    padding: var(--wa-space-s);
    flex-shrink: 0;
}

.load-more-error wa-callout {
    --wa-callout-padding: var(--wa-space-s);
}

.load-more-error wa-callout span {
    font-size: var(--wa-font-size-s);
}

.empty-state {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: var(--wa-space-l);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}
</style>
