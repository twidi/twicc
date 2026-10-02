<script setup>
import { computed, ref, watch, nextTick, onMounted, onBeforeUnmount, provide, inject, unref } from 'vue'
import VirtualScroller from '../components/virtual-scroller/VirtualScroller.vue'
import SessionItem from '../components/session/detail/SessionItem.vue'
import GroupToggle from '../components/session/detail/GroupToggle.vue'
import ChatNavToolbar from '../components/session/detail/ChatNavToolbar.vue'
import DaySeparator from '../components/session/detail/items/DaySeparator.vue'
import ChatSkeleton from '../components/session/detail/ChatSkeleton.vue'
import { useChatNavigation } from '../composables/useChatNavigation'
import { useChatReveal } from '../composables/useChatReveal.js'
import { useDataStore } from '../stores/data'          // aliased → dataStoreShim
import { useSettingsStore } from '../stores/settings'  // aliased → settingsStoreShim
import { getParsedContent, hasContent } from '../utils/parsedContent'
import { collectMissingScrollerLines, sameScrollerLoadCandidates } from '../utils/scrollerLoadWindow.js'
import { isSessionNotReadyError } from './shims/shareApi'

const props = defineProps({
    projectId: { type: String, default: 'share' },
    sessionId: { type: String, required: true },
    parentSessionId: { type: String, default: null },
    lastLine: { type: Number, required: true },
})

const store = useDataStore()
const settings = useSettingsStore()
const scrollerRef = ref(null)
const preparationPending = ref(false)
// The first load: the rows that fill meanwhile stay hidden under the skeleton (same
// delay and hold as the SPA, utils/chatReveal.js).
const initialLoading = ref(true)
const reveal = useChatReveal(() => initialLoading.value)
const INITIAL = 100, BUFFER = 40, MIN_ITEM = 40

const visualItems = computed(() => store.getSessionVisualItems(props.sessionId))

async function loadInitial() {
    try {
        const ranges = []
        if (props.lastLine <= INITIAL) ranges.push([1, props.lastLine])
        else if (props.parentSessionId) ranges.push([1, INITIAL])
        else ranges.push([props.lastLine - INITIAL + 1, props.lastLine])
        const qs = new URLSearchParams()
        for (const [lo, hi] of ranges) qs.append('range', `${lo}:${hi}`)
        const [metadata] = await Promise.all([
            store.loadSessionMetadata(props.projectId, props.sessionId, props.parentSessionId),
            store.loadSessionItemsRanges(props.projectId, props.sessionId, ranges, props.parentSessionId),
            // Completion state of every visible tool call — without it every tool
            // renders as running (resultCount 0). Live updates then flow via WS.
            store.fetchToolStates(props.projectId, props.sessionId, props.parentSessionId),
        ])
        if (metadata) {
            // Metadata first initializes the array; the ranges call above already
            // added content for the initial window — re-apply metadata then let the
            // content fill (order-independent because both recompute).
            store.initSessionItemsFromMetadata(props.sessionId, metadata)
            await store.loadSessionItemsRanges(props.projectId, props.sessionId, ranges, props.parentSessionId)
        }
    } catch (error) {
        if (isSessionNotReadyError(error)) {
            preparationPending.value = true
            return
        }
        throw error
    } finally {
        initialLoading.value = false
    }
}
onMounted(loadInitial)

async function loadLines(lines) {
    if (!lines?.length) return
    // Coalesce contiguous line numbers into ranges.
    const sorted = [...new Set(lines)].sort((a, b) => a - b)
    const ranges = []; let s = sorted[0], e = sorted[0]
    for (let i = 1; i < sorted.length; i++) {
        if (sorted[i] === e + 1) e = sorted[i]
        else { ranges.push([s, e]); s = e = sorted[i] }
    }
    ranges.push([s, e])
    await store.loadSessionItemsRanges(props.projectId, props.sessionId, ranges, props.parentSessionId)
}

// Share gaps have local ownership; neither geometry churn nor failed requests retry themselves.
const gapMounted = ref(false)
const gapRange = ref(null)
const gapMeasurementRevision = ref(0)
let gapGeneration = 0
let gapTimer = null
let gapRequest = null
let gapRetryPending = false
let attemptedGap = []
const gapReady = computed(() => {
    // DOM height is not reactive. Measured updates and native scrolls refresh this cached decision.
    gapMeasurementRevision.value
    return gapMounted.value && !initialLoading.value && !preparationPending.value &&
        !!scrollerRef.value && !unref(scrollerRef.value.suspended) && scrollerRef.value.getScrollState().clientHeight > 0
})
const missingLines = computed(previous => {
    if (!gapReady.value || !gapRange.value) return []
    const lines = collectMissingScrollerLines(visualItems.value ?? [], gapRange.value, BUFFER)
    return previous && sameScrollerLoadCandidates(previous, lines) ? previous : lines
})
const gapMembership = computed(previous => {
    if (!gapReady.value || !gapRange.value) return []
    const items = visualItems.value ?? []
    const { start, end } = gapRange.value
    const keys = items.slice(Math.max(0, start - BUFFER), Math.min(items.length, end + BUFFER + 1))
        .map(item => item?.lineNum)
    return previous && sameScrollerLoadCandidates(previous, keys) ? previous : keys
})
// Read layout again at the action boundary: observer delivery can lag a hidden DOM.
function canLoadGap() {
    return gapReady.value && scrollerRef.value.getScrollState().clientHeight > 0
}

function cancelGapTimer() {
    clearTimeout(gapTimer)
    gapTimer = null
}
function scheduleGapLoad(retry = false) {
    if (!canLoadGap() || !missingLines.value.length) { cancelGapTimer(); return }
    if (gapRequest) { gapRetryPending ||= retry; return }
    if (!retry && sameScrollerLoadCandidates(attemptedGap, missingLines.value)) return
    if (!gapTimer) gapTimer = setTimeout(executeGapLoad, 120)
}
async function executeGapLoad() {
    cancelGapTimer()
    if (!canLoadGap() || gapRequest || !missingLines.value.length) return
    const lines = [...missingLines.value]
    attemptedGap = lines
    const owner = { generation: gapGeneration }
    gapRequest = owner
    gapRetryPending = false
    let succeeded = false
    try {
        await loadLines(lines)
        succeeded = true
    } catch {
        // Preserve the initial preparation policy. Later gaps wait for a new opportunity.
    } finally {
        const current = canLoadGap() && owner.generation === gapGeneration
        gapRequest = null
        if (canLoadGap() && (gapRetryPending || (current && succeeded &&
            !sameScrollerLoadCandidates(lines, missingLines.value)))) scheduleGapLoad(gapRetryPending)
        gapRetryPending = false
    }
}
// A changed availability window permits retry, including a previously filled gap.
// Equal candidate snapshots stay stable, so failed/no-progress gaps do not self-loop.
watch(missingLines, () => scheduleGapLoad(true))
watch(gapMembership, () => scheduleGapLoad(true))
watch([gapReady, () => props.projectId, () => props.sessionId, () => props.parentSessionId], () => {
    gapGeneration++
    cancelGapTimer()
    attemptedGap = []
    gapRetryPending = false
    if (gapReady.value) scheduleGapLoad(true)
}, { flush: 'sync' })
onMounted(() => { gapMounted.value = true })
onBeforeUnmount(() => { gapMounted.value = false; cancelGapTimer() })

function onUpdate({ visibleStartIndex, visibleEndIndex }) {
    gapMeasurementRevision.value++
    if (gapRange.value?.start === visibleStartIndex && gapRange.value?.end === visibleEndIndex) return
    gapRange.value = { start: visibleStartIndex, end: visibleEndIndex }
    scheduleGapLoad(true)
}
function onShareScroll() {
    gapMeasurementRevision.value++
    scheduleGapLoad(true)
}

function toggleGroup(head) { store.toggleExpandedGroup(props.sessionId, head) }

// The reused components inject these; provide the media rewrite + the tool-result
// fetch seam (both share-mode). parentSessionId routes subagent tool-results.
const shareApi = inject('shareApi')
provide('fetchToolResult', (lineNum, toolId, parentSessionId) =>
    shareApi.fetchToolResults(lineNum, toolId, parentSessionId || null))
// Bound to THIS list's session context (root vs subagent) so the reused Edit /
// apply_patch diff can pull its ceiling-filtered tool_result line by tool id.
provide('fetchBackendPatchItems', (toolId) =>
    shareApi.fetchBackendPatchItems(toolId, props.parentSessionId || null))
provide('rewriteContentMediaUrl', (url) => {
    // /artifacts/<sid>/<file> → /share/<t>/media/<file> when sid === shared session.
    const m = /^\/artifacts\/([^/]+)\/([^/?#]+)$/.exec(url)
    if (m && m[1] === props.sessionId) return shareApi.mediaUrl(m[2])
    if (m) return null       // a different session's artifact — not shared
    return url
})

// The reused settings store has a recompute watcher; the shim doesn't, so rebuild
// the visual items when the viewer changes the display mode or timestamp toggle.
watch(() => [settings.displayMode, settings.areMessageTimestampsShown],
    () => store.recomputeVisualItems(props.sessionId))

// ── Navigation toolbar (extremes + block by block) ───────────────────────────

const scrollerElement = computed(() => scrollerRef.value?.$el ?? null)

/**
 * Bring one item to the top of the viewport. Items around the target are loaded
 * first: landing on a screen of placeholders would let them grow under the
 * viewport and drag the scroll position away.
 */
async function scrollToItem(lineNum, offset = 0) {
    const vis = visualItems.value
    const index = vis?.findIndex((vi) => vi.lineNum === lineNum) ?? -1
    if (index === -1) return

    const lo = Math.max(0, index - BUFFER)
    const hi = Math.min(vis.length - 1, index + BUFFER)
    const need = []
    for (let i = lo; i <= hi; i++) {
        const vi = vis[i]
        if (vi && !vi.isDaySeparator && !hasContent(vi)) need.push(vi.lineNum)
    }
    // Navigation awaits its own content load before positioning the target.
    if (need.length) {
        await loadLines(need)
        await nextTick()
    }
    await scrollerRef.value?.scrollToKey(lineNum, { align: 'start', offset })
}

const {
    hasNavigation: navHasNavigation,
    canGoTop: navCanGoTop,
    canGoPrev: navCanGoPrev,
    canGoNext: navCanGoNext,
    canGoBottom: navCanGoBottom,
    goTop: navGoTop,
    goPrevBlock: navGoPrevBlock,
    goNextBlock: navGoNextBlock,
    goBottom: navGoBottom,
} = useChatNavigation({
    scrollerRef,
    visualItems,
    scrollToItem,
    scrollToEdge: (edge) => scrollerRef.value?.scrollToEdge(edge),
})
</script>

<template>
    <div class="session-items-list share-items-list panel-card" :aria-busy="reveal.hidden.value ? 'true' : null">
        <wa-callout v-if="preparationPending" variant="neutral" class="share-banner">
            This shared session is being prepared. Refresh this page later.
        </wa-callout>
        <VirtualScroller
            v-else
            ref="scrollerRef"
            :items="visualItems"
            :item-key="(item) => item.lineNum"
            :min-item-height="MIN_ITEM"
            :buffer="5000"
            :unload-buffer="10000"
            :prevent-auto-scroll-to-bottom="!!parentSessionId"
            class="session-items"
            :class="{ 'initial-scrolling': reveal.hidden.value }"
            @update="onUpdate"
            @scroll="onShareScroll"
        >
            <template #default="{ item }">
                <DaySeparator v-if="item.isDaySeparator" :label="item.dayLabel" :day-key="item.dayKey" />
                <div v-else-if="!hasContent(item)"
                     :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd }"
                     :style="{ minHeight: MIN_ITEM + 'px' }"></div>
                <template v-else-if="item.isGroupHead">
                    <GroupToggle
                        :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd && !item.isExpanded }"
                        :expanded="item.isExpanded" :item-count="item.groupSize" :comments-count="0"
                        @toggle="toggleGroup(item.lineNum)" />
                    <SessionItem v-if="item.isExpanded" :class="{ 'is-block-end': item.isBlockEnd }"
                        :content="getParsedContent(item)" :kind="item.kind" :synthetic-kind="null"
                        :project-id="projectId" :session-id="sessionId" :parent-session-id="parentSessionId"
                        :line-num="item.lineNum" :externally-grouped="item.externallyGrouped || false"
                        :is-block-end="item.isBlockEnd || false"
                        :is-live-timestamp-anchor="item.isLiveTimestampAnchor || false" />
                </template>
                <SessionItem v-else
                    :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd }"
                    :content="getParsedContent(item)" :kind="item.kind" :synthetic-kind="null"
                    :project-id="projectId" :session-id="sessionId" :parent-session-id="parentSessionId"
                    :line-num="item.lineNum" :externally-grouped="item.externallyGrouped || false"
                    :group-head="item.groupHead" :group-tail="item.groupTail"
                    :prefix-expanded="item.prefixExpanded || false" :suffix-expanded="item.suffixExpanded || false"
                    :detail-toggle-for="item.detailToggleFor ?? null"
                    :is-block-start="item.isBlockStart || false" :is-block-end="item.isBlockEnd || false"
                    :is-live-timestamp-anchor="item.isLiveTimestampAnchor || false"
                    @toggle-suffix="toggleGroup(item.suffixGroupHead)" />
            </template>
        </VirtualScroller>

        <!-- One skeleton instance per reveal phase, over the first load (see SessionItemsList). -->
        <Transition name="chat-skeleton" type="transition" :key="reveal.phaseId.value">
            <ChatSkeleton
                v-if="reveal.hidden.value && !preparationPending"
                class="chat-skeleton-overlay"
                :visible="reveal.skeletonShown.value"
                :align="parentSessionId ? 'start' : 'end'"
            />
        </Transition>

        <ChatNavToolbar
            v-show="navHasNavigation && !reveal.hidden.value"
            :can-go-top="navCanGoTop"
            :can-go-prev="navCanGoPrev"
            :can-go-next="navCanGoNext"
            :can-go-bottom="navCanGoBottom"
            :scroll-element="scrollerElement"
            @top="navGoTop"
            @prev="navGoPrevBlock"
            @next="navGoNextBlock"
            @bottom="navGoBottom"
        />
    </div>
</template>

<style scoped>
.chat-skeleton-overlay {
    position: absolute;
    inset: 0;
}

/* Hidden under the skeleton while the first load fills the rows; when the class goes,
   visibility switches at once and the opacity fades in (as in SessionItemsList). */
.session-items {
    transition: opacity var(--motion-dur-2) var(--motion-ease);
}
.session-items.initial-scrolling {
    visibility: hidden;
    opacity: 0;
}
</style>
