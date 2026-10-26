<script setup>
// TimelinePanel.vue - The Timeline tab of a scope page (global, workspace, project, worktree).
// Design: docs/plans/2026-10-15-timeline-display-design.md.
//
// The Stats tab's structure: in compact-height mode the navigation list of the scope first (the page header
// drops its own copy then), then the "Timeline" band (StickyBand.vue, the band of Stats' provider filter), then
// the newest-first list. The list and the band scroll together: the navigation list scrolls away, the band
// stays at the top of the list (position: sticky).
//
// The list lives in the simple virtual scroller, its own scroll container, so the band and the navigation list
// sit in the scroller's `before` slot (a sticky element must be in the container it sticks to). The scroller
// knows nothing of that slot: it lays its rows out from 0. So the two take no room in the flow (the band's
// negative bottom margin) and the first row of the list (`top`) keeps the room under them, its height measured
// from them.
//
// The scroller and its ONE band stay mounted through every state (filters design section 3.1): the band holds
// the filter controls (TimelineFilterBand.vue), and swapping it would close their open panel and lose the
// keyboard focus. Outside the list (loading, empty, not found, error, no match), the event rows are replaced by
// one state item under the top room.
//
// The data side lives in utils/timeline/ (pure, tested): which events the scope asks for, the fetch queue,
// the grouping into rows and the wording of each row. This component wires them to the stores and the DOM.

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useTimelineFeed } from '../../composables/useTimelineFeed'
import { ALL_PROJECTS_ID, useDataStore } from '../../stores/data'
import { useTimelineFiltersStore } from '../../stores/timelineFilters'
import { useWorkspacesStore } from '../../stores/workspaces'
import { getProviderHelpers } from '../../providers'
import { compactHeight } from '../../utils/compactHeight'
import { bindTimelineEvents } from '../../utils/timeline/liveEvents'
import { timeColumnSizer } from '../../utils/timeline/eventTime'
import { VIEWS, queryKey, toQuery } from '../../utils/timeline/filters'
import { buildTimelineRows, estimateRowHeight, isEventRowKey, isNearEnd } from '../../utils/timeline/rows'
import { createLabelsFor } from '../../utils/timeline/providerLabels'
import { resolveTimelineScope } from '../../utils/timeline/scope'
import { createScrollKeeper } from '../../utils/timeline/scrollKeeper'
import ProjectTabNavList from '../project/ProjectTabNavList.vue'
import DaySeparator from '../session/detail/items/DaySeparator.vue'
import StickyBand from '../ui/StickyBand.vue'
import VirtualScroller from '../virtual-scroller/VirtualScroller.vue'
import TimelineEventRow from './TimelineEventRow.vue'
import TimelineFilterBand from './TimelineFilterBand.vue'

const props = defineProps({
    /** Project ID, `ws:<id>` of a workspace, or ALL_PROJECTS_ID. */
    projectId: {
        type: String,
        required: true,
    },
    /** The Timeline tab is the one on screen (the page is not behind a session, nor on another tab). */
    active: {
        type: Boolean,
        default: true,
    },
})

const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()
const filtersStore = useTimelineFiltersStore()
const feed = useTimelineFeed()
const state = feed.state

// ── What the page asks for ────────────────────────────────────────────────────────────────────────────────

const scope = computed(() => resolveTimelineScope({
    pageId: props.projectId,
    allProjectsId: ALL_PROJECTS_ID,
    projectsLoaded: dataStore.projectsLoaded,
    // The archived-blind list, as Stats uses: the "show archived projects" setting does not apply here.
    workspaceOf: (id) => (workspacesStore.getWorkspaceById(id) ? { allProjectIds: workspacesStore.getAllProjectIds(id) } : null),
    projectOf: (id) => dataStore.getProject(id),
    projectScopeIds: (id) => dataStore.getProjectScopeIds(id),
}))

// The filters of this page (filters design section 5): kept by the store per page id, so a page shown again,
// or mounted again, finds its own. Read only here; the band changes them through the store's actions.
const filters = computed(() => filtersStore.filtersFor(props.projectId))
const filterQuery = computed(() => toQuery(filters.value))
const filterQueryKey = computed(() => queryKey(filterQuery.value))
// What the server is asked decides whether the list is filtered: a group with every value checked filters nothing
// (filters design section 4.5), so its query is empty, like no filter at all.
const UNFILTERED_QUERY_KEY = queryKey({})

// The list's scroller (VirtualScroller.vue), mounted with the panel and kept through every state.
const scrollerRef = ref(null)

// The feed compares the SET of ids and the query: a project added, removed or archived, or a filter change,
// reloads the first page. A filter change also brings the scroll back to the top (filters design section 6).
watch([() => scope.value.projectIds, filterQueryKey], ([ids, key], previous) => {
    feed.setScope(ids, filterQuery.value)
    if (previous && key !== previous[1]) scrollerRef.value?.scrollToTop()
}, { immediate: true })

// ── Staying current (design section 9.2) ───────────────────────────────────────────────────────────────────
// The page is kept alive per scope: it keeps fetching while hidden and renders nothing. The signal and the
// socket events reach it through window events dispatched by useWebSocket.js.
let unbindEvents = null
onMounted(() => { unbindEvents = bindTimelineEvents(window, feed) })
onBeforeUnmount(() => {
    unbindEvents?.()
    feed.dispose()
})

// Back on the tab (or the kept-alive page shown again): catch up once; the fetches coalesce in the queue.
watch(() => props.active, (active) => {
    if (active) feed.refresh()
})

// ── What the page shows ───────────────────────────────────────────────────────────────────────────────────

const context = computed(() => ({
    sessions: state.sessions,
    labelsFor: createLabelsFor(getProviderHelpers),
    // A subagent has no openable route as a top-level session; the store knows the ones it loaded.
    isSubagent: (id) => dataStore.getSession(id)?.type === 'subagent',
}))

const eventRows = computed(() => buildTimelineRows(state.events, {
    sessions: state.sessions,
    scope: { kind: scope.value.kind, projectId: scope.value.projectId },
    isSubagent: context.value.isSubagent,
}))

/**
 * Which block of the page is visible: the list, or one of the states. "No match" (filters design section 3.5)
 * only follows a successful first page whose query filters something: loading and error win over it.
 */
const view = computed(() => {
    if (scope.value.state === 'loading') return 'loading'
    if (scope.value.state === 'empty') return 'empty'
    if (scope.value.state === 'not-found') return 'not-found'
    if (state.status === 'error') return 'error'
    if (state.status !== 'ready') return 'loading'
    if (state.events.length) return 'list'
    return filterQueryKey.value !== UNFILTERED_QUERY_KEY ? 'no-match' : 'empty'
})

// The room kept under the navigation list and the band (see the top of the file), then the event rows or the
// one state item. Every item is a constant per shape: the scroller sees the same objects while nothing changed.
const TOP_ROW = Object.freeze({ key: 'timeline-top', type: 'top', withNav: false })
const TOP_ROW_WITH_NAV = Object.freeze({ key: 'timeline-top', type: 'top', withNav: true })
const STATE_ITEMS = Object.freeze(Object.fromEntries(['loading', 'empty', 'not-found', 'error', 'no-match']
    .map((name) => [name, Object.freeze({ key: 'timeline-state', type: 'state', state: name })])))
const items = computed(() => [
    compactHeight.value ? TOP_ROW_WITH_NAV : TOP_ROW,
    ...(view.value === 'list' ? eventRows.value : [STATE_ITEMS[view.value]]),
])

/** The no-match message names the view when it is not `all`: some views can never match some filters. */
const noMatchMessage = computed(() => {
    const selected = VIEWS.find((entry) => entry.value === filters.value.view && entry.value !== 'all')
    return selected ? `Nothing matches ${selected.label} with these filters` : 'Nothing matches these filters'
})

// The filter band, for the focus moves of spec 3.1.
const filterBandRef = ref(null)

/** The no-match "Clear all": its button disappears with the state, so the focus goes to the Filters button. */
async function clearAllFilters() {
    filtersStore.clearAll(props.projectId)
    await nextTick()
    filterBandRef.value?.focusFiltersButton()
}

const itemKey = (item) => item.key

// ── The room under the navigation list and the band ───────────────────────────────────────────────────────
// Their height together, measured, as `--timeline-top-height` on the panel: the height of the top row and the
// band's negative bottom margin. A band of no height is a hidden tab (display: none): the last room is kept.
const topNavRef = ref(null)
const bandRef = ref(null)
const topHeight = ref(0)
// The widest times of the locale, for the time column of every row (TimelineEventRow.vue): computed once, with the
// clock format of the rows (the context sets no `time` options, like this call).
const TIME_COLUMN_SIZER = timeColumnSizer()
const panelStyle = computed(() => ({
    '--timeline-top-height': `${topHeight.value}px`,
    '--timeline-time-sizer': TIME_COLUMN_SIZER,
}))
let topObserver = null

function measureTop() {
    const band = bandRef.value?.$el
    const bandHeight = band?.getBoundingClientRect?.().height ?? 0
    if (!bandHeight) return
    const navHeight = topNavRef.value?.$el?.getBoundingClientRect?.().height ?? 0
    topHeight.value = bandHeight + navHeight
}

watch([bandRef, topNavRef], ([band, nav]) => {
    topObserver?.disconnect()
    topObserver = null
    if (!band) return
    measureTop()
    if (typeof ResizeObserver === 'undefined') return
    topObserver = new ResizeObserver(measureTop)
    topObserver.observe(band.$el)
    if (nav) topObserver.observe(nav.$el)
}, { flush: 'post' })
onBeforeUnmount(() => topObserver?.disconnect())

// ── Scroll: rows inserted above the view must not move it ─────────────────────────────────────────────────
// Anchored on event rows only: the top room and the day separators keep their index when rows land below them.
const scrollKeeper = createScrollKeeper(() => scrollerRef.value, { isAnchorKey: isEventRowKey })

watch(items, async (next) => {
    const snapshot = scrollKeeper.capture()
    if (!snapshot) return
    await nextTick()
    scrollKeeper.restore(snapshot, new Map(next.map((row, index) => [row.key, index])))
}, { flush: 'pre' })

// ── Scroller: estimates, next page ────────────────────────────────────────────────────────────────────────

// A first height estimate per row type (day separator, event line, block head and end, top room, state item) for the
// rows the scroller has not measured yet. It runs AFTER the render (the scroller exists once the panel is mounted). A
// row the scroller already measured keeps its measure (seeding would overwrite it, and no resize would correct it).
// `seeded` maps a key to the estimate written for it (null: the scroller had its own height): when a live insertion
// flips a row's head or end status, the new estimate replaces the old one while the cache still holds that old seed
// (the row was not measured since). `seeded` follows the list: it is trimmed to the keys still in the list, so a row
// that left (the state item replaced it) is seeded again when it returns. The scroller stays while the panel is
// mounted; the empty-scroller guard only runs before its first render.
const seeded = new Map()
function seedHeights() {
    const scroller = scrollerRef.value
    if (!scroller) {
        seeded.clear()
        return
    }
    const keys = new Set()
    for (const row of items.value) {
        keys.add(row.key)
        const estimate = estimateRowHeight(row)
        if (!seeded.has(row.key)) {
            const unmeasured = scroller.getItemHeight(row.key) === undefined
            if (unmeasured) scroller.seedItemHeight(row.key, estimate)
            seeded.set(row.key, unmeasured ? estimate : null)
            continue
        }
        const previous = seeded.get(row.key)
        if (previous === null || previous === estimate) continue
        if (scroller.getItemHeight(row.key) === previous) {
            scroller.seedItemHeight(row.key, estimate)
            seeded.set(row.key, estimate)
        } else {
            seeded.set(row.key, null)
        }
    }
    for (const key of seeded.keys()) if (!keys.has(key)) seeded.delete(key)
}
watch([items, scrollerRef], seedHeights, { flush: 'post' })

let visibleEnd = 0

function maybeLoadMore() {
    if (view.value === 'list' && isNearEnd(items.value.length, visibleEnd)) feed.loadMore()
}

function onScrollerUpdate(range) {
    visibleEnd = range.visibleEndIndex
    maybeLoadMore()
}

// What is in view now, asked to the scroller (its last `update` may be older: a page landed, the tab came back).
function refreshVisibleEnd() {
    const range = scrollerRef.value?.getVisibleRange()
    if (range) visibleEnd = range.end
}

function onBecameVisible() {
    refreshVisibleEnd()
    maybeLoadMore()
}

// A page that just landed may leave the end of the list still in view (a tall window): ask again.
watch(() => state.events.length, async () => {
    await nextTick()
    refreshVisibleEnd()
    maybeLoadMore()
})
</script>

<template>
    <div class="timeline-panel" :style="panelStyle">
        <div class="timeline-list">
            <VirtualScroller
                ref="scrollerRef"
                class="timeline-scroller"
                :items="items"
                :item-key="itemKey"
                :min-item-height="28"
                :buffer="400"
                :unload-buffer="800"
                prevent-auto-scroll-to-bottom
                @update="onScrollerUpdate"
                @became-visible="onBecameVisible"
            >
                <template #before>
                    <ProjectTabNavList v-if="compactHeight" ref="topNavRef" :project-id="projectId" class="timeline-top-nav" />
                    <StickyBand ref="bandRef" class="timeline-band--over-list">
                        <TimelineFilterBand ref="filterBandRef" :page-id="projectId" :active="active" />
                    </StickyBand>
                </template>
                <template #default="{ item }">
                    <div v-if="item.type === 'top'" class="timeline-top-room" aria-hidden="true"></div>
                    <DaySeparator v-else-if="item.type === 'day'" :label="item.label" :day-key="item.dayKey" />
                    <template v-else-if="item.type === 'state'">
                        <div v-if="item.state === 'loading'" class="timeline-state" role="status">
                            <wa-spinner></wa-spinner>
                            <span>Loading…</span>
                        </div>
                        <div v-else-if="item.state === 'empty'" class="timeline-state">No events yet</div>
                        <div v-else-if="item.state === 'no-match'" class="timeline-state">
                            <span class="timeline-no-match-message">{{ noMatchMessage }}</span>
                            <wa-button appearance="outlined" size="small" @click="clearAllFilters">Clear all</wa-button>
                        </div>
                        <div v-else-if="item.state === 'not-found'" class="timeline-state">
                            <span>Project not found</span>
                            <RouterLink :to="{ name: 'projects-all' }">Back to All Projects</RouterLink>
                        </div>
                        <div v-else class="timeline-state">
                            <!-- wa-callout has a default slot and an icon slot only: the button goes in the default one. -->
                            <wa-callout variant="danger">
                                <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                                <div class="timeline-error-content">
                                    <span>Failed to load the timeline</span>
                                    <wa-button variant="danger" appearance="outlined" size="small" @click="feed.retry()">
                                        <wa-icon name="arrow-rotate-right" slot="start"></wa-icon>
                                        Retry
                                    </wa-button>
                                </div>
                            </wa-callout>
                        </div>
                    </template>
                    <TimelineEventRow v-else :row="item" :context="context" />
                </template>
            </VirtualScroller>

            <div v-if="state.loadingMore" class="timeline-footer" role="status">
                <wa-spinner></wa-spinner>
            </div>
            <!-- A failed first page is the error state item: only a failed older page shows here. -->
            <div v-else-if="state.loadError?.phase === 'before'" class="timeline-footer timeline-footer--error">
                <!-- wa-callout has a default slot and an icon slot only: the button goes in the default one. -->
                <wa-callout variant="danger">
                    <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                    <div class="timeline-error-content">
                        <span>Failed to load more events</span>
                        <wa-button variant="danger" appearance="outlined" size="small" @click="feed.retry()">
                            <wa-icon name="arrow-rotate-right" slot="start"></wa-icon>
                            Retry
                        </wa-button>
                    </div>
                </wa-callout>
            </div>
        </div>
    </div>
</template>

<style scoped>
.timeline-panel {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

/* In the list, the navigation list and the band take no room in the flow: the top row keeps it (see the top of
   the script). The navigation list is positioned, so its links stay above that empty row. */
.timeline-band--over-list {
    margin-block-end: calc(-1 * var(--timeline-top-height));
}

.timeline-top-nav {
    position: relative;
    z-index: 1;
}

.timeline-top-room {
    height: var(--timeline-top-height);
    pointer-events: none;
}

.timeline-list {
    flex: 1;
    min-height: 0;
    position: relative;
    display: flex;
    flex-direction: column;
}

/* The scroller shares the column with the footer (spinner, error): it takes what is left. */
.timeline-list .timeline-scroller {
    flex: 1;
    min-height: 0;
}

/* The scroller pins itself to the bottom when the user is near it (native scroll anchoring on its sentinel).
   A newest-first list must never do that: loading older events would drag the view down. */
.timeline-list .timeline-scroller.at-bottom {
    overflow-anchor: none;
}

.timeline-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-2xl) var(--wa-space-l);
    color: var(--wa-color-text-quiet);
}

.timeline-footer {
    flex: 0 0 auto;
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s) var(--wa-space-l);
}

.timeline-footer--error {
    display: block;
}

.timeline-error-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-s);
    text-align: center;
}
</style>
