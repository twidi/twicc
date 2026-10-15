<script setup>
// TimelinePanel.vue - The Timeline tab of a scope page (global, workspace, project, worktree).
// Design: docs/plans/2026-10-15-timeline-display-design.md.
//
// A sticky "Timeline" band, then the newest-first list in the simple virtual scroller. The page header and
// the band never scroll; only the list does. In compact-height mode the page header drops its navigation
// list and a copy of it becomes the FIRST ROW of the list, so it scrolls away with the content while the
// band stays at the top (the same behaviour as the Stats tab).
//
// The data side lives in utils/timeline/ (pure, tested): which events the scope asks for, the fetch queue,
// the grouping into rows and the wording of each row. This component wires them to the stores and the DOM.

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useTimelineFeed } from '../../composables/useTimelineFeed'
import { ALL_PROJECTS_ID, useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { getProviderHelpers } from '../../providers'
import { compactHeight } from '../../utils/compactHeight'
import { bindTimelineEvents } from '../../utils/timeline/liveEvents'
import { buildTimelineRows, estimateRowHeight, isNearEnd } from '../../utils/timeline/rows'
import { createLabelsFor } from '../../utils/timeline/providerLabels'
import { resolveTimelineScope } from '../../utils/timeline/scope'
import { createScrollKeeper } from '../../utils/timeline/scrollKeeper'
import ProjectDetailNavList from '../project/ProjectDetailNavList.vue'
import DaySeparator from '../session/detail/items/DaySeparator.vue'
import VirtualScroller from '../virtual-scroller/VirtualScroller.vue'
import TimelineEventRow from './TimelineEventRow.vue'

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

// The feed compares the SET of ids: a project added, removed or archived reloads the first page.
watch(() => scope.value.projectIds, (ids) => feed.setScope(ids), { immediate: true })

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

const NAV_ROW = Object.freeze({ key: 'timeline-nav', type: 'nav' })
const items = computed(() => (compactHeight.value ? [NAV_ROW, ...eventRows.value] : eventRows.value))

/** Which block of the page is visible. */
const view = computed(() => {
    if (scope.value.state === 'loading') return 'loading'
    if (scope.value.state === 'empty') return 'empty'
    if (scope.value.state === 'not-found') return 'not-found'
    if (state.status === 'error') return 'error'
    if (state.status !== 'ready') return 'loading'
    return state.events.length ? 'list' : 'empty'
})

const itemKey = (item) => item.key

// ── Scroll: rows inserted above the view must not move it ─────────────────────────────────────────────────
const scrollerRef = ref(null)
const scrollKeeper = createScrollKeeper(() => scrollerRef.value)

watch(items, async (next) => {
    const snapshot = scrollKeeper.capture()
    if (!snapshot) return
    await nextTick()
    scrollKeeper.restore(snapshot, new Map(next.map((row, index) => [row.key, index])))
}, { flush: 'pre' })

// ── Scroller: estimates, next page ────────────────────────────────────────────────────────────────────────

// A first height estimate per row type (day separator, event line, navigation copy) for the rows the scroller
// has not measured yet. It runs AFTER the render: the scroller only exists once the first page flipped the view
// to the list, and it is created again each time the list comes back. A row the scroller already measured keeps
// its measure (seeding would overwrite it, and no resize would correct it). `seeded` follows the list: it is
// emptied when the scroller goes away and trimmed to the keys still in the list.
const seeded = new Set()
function seedHeights() {
    const scroller = scrollerRef.value
    if (!scroller) {
        seeded.clear()
        return
    }
    const keys = new Set()
    for (const row of items.value) {
        keys.add(row.key)
        if (seeded.has(row.key)) continue
        seeded.add(row.key)
        if (scroller.getItemHeight(row.key) === undefined) scroller.seedItemHeight(row.key, estimateRowHeight(row))
    }
    for (const key of seeded) if (!keys.has(key)) seeded.delete(key)
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
    <div class="timeline-panel">
        <div class="timeline-band glass-sticky">
            <span class="timeline-band-label">Timeline</span>
        </div>

        <div v-if="view !== 'list'" class="timeline-state-area">
            <template v-if="compactHeight">
                <ProjectDetailNavList :project-id="projectId" class="timeline-nav-list" />
                <wa-divider class="timeline-nav-divider"></wa-divider>
            </template>
            <div v-if="view === 'loading'" class="timeline-state" role="status">
                <wa-spinner></wa-spinner>
                <span>Loading…</span>
            </div>
            <div v-else-if="view === 'empty'" class="timeline-state">No events yet</div>
            <div v-else-if="view === 'not-found'" class="timeline-state">
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
        </div>

        <div v-else class="timeline-list">
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
                <template #default="{ item }">
                    <template v-if="item.type === 'nav'">
                        <ProjectDetailNavList :project-id="projectId" class="timeline-nav-list" />
                        <wa-divider class="timeline-nav-divider"></wa-divider>
                    </template>
                    <DaySeparator v-else-if="item.type === 'day'" :label="item.label" :day-key="item.dayKey" />
                    <TimelineEventRow v-else :row="item" :context="context" />
                </template>
            </VirtualScroller>

            <div v-if="state.loadingMore" class="timeline-footer" role="status">
                <wa-spinner></wa-spinner>
            </div>
            <div v-else-if="state.loadError" class="timeline-footer timeline-footer--error">
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

/* The band of Stats' provider filter, outside the scroller: it never moves (design 3.3, 3.4). */
.timeline-band {
    position: relative;
    z-index: 2;
    flex: 0 0 auto;
    display: flex;
    justify-content: center;
    padding: var(--wa-space-xs) 0 var(--wa-space-m);
    --glass-sticky-bg: color-mix(in oklab, var(--surface-solid) 60%, transparent);
}

.timeline-band-label {
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
}

/* Without the blur the translucent layer would let the list show through unreadable: the card's colour. */
@media (prefers-reduced-transparency: reduce) {
    .timeline-band {
        --glass-sticky-bg: transparent;
        background: var(--panel-solid);
    }
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

.timeline-state-area {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.timeline-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-2xl) var(--wa-space-l);
    color: var(--wa-color-text-quiet);
}

/* The navigation copy exists only in compact-height mode (the page header omits it then). */
.timeline-nav-list {
    display: none;
}

:where(html.compact-height) .timeline-nav-list {
    display: flex;
    padding: var(--wa-space-s) var(--wa-space-m);
}

.timeline-nav-divider {
    --spacing: 0;
    --width: var(--divider-size);
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

<style>
/* "Reduce effects" has no blur either: the opaque card colour, as in the other no-blur modes. */
:root.reduce-effects .timeline-band {
    --glass-sticky-bg: transparent;
    background: var(--panel-solid);
}
</style>
