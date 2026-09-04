<script setup>
import { computed, ref, watch, nextTick, onMounted, provide, inject } from 'vue'
import VirtualScroller from '../components/virtual-scroller/VirtualScroller.vue'
import SessionItem from '../components/session/detail/SessionItem.vue'
import GroupToggle from '../components/session/detail/GroupToggle.vue'
import ChatNavToolbar from '../components/session/detail/ChatNavToolbar.vue'
import DaySeparator from '../components/session/detail/items/DaySeparator.vue'
import { useChatNavigation } from '../composables/useChatNavigation'
import { useDataStore } from '../stores/data'          // aliased → dataStoreShim
import { useSettingsStore } from '../stores/settings'  // aliased → settingsStoreShim
import { getParsedContent, hasContent } from '../utils/parsedContent'
import { useDebounceFn } from '@vueuse/core'
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

const pending = ref(null)
const flush = useDebounceFn(async () => {
    const lines = pending.value; pending.value = null
    await loadLines(lines)
}, 120)

function onUpdate({ visibleStartIndex, visibleEndIndex }) {
    const vis = visualItems.value
    if (!vis?.length) return
    const lo = Math.max(0, visibleStartIndex - BUFFER)
    const hi = Math.min(vis.length - 1, visibleEndIndex + BUFFER)
    const need = []
    for (let i = lo; i <= hi; i++) {
        const vi = vis[i]
        if (vi && !vi.isDaySeparator && !hasContent(vi)) need.push(vi.lineNum)
    }
    if (need.length) { pending.value = need; flush() }
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
    // Loaded directly rather than through `flush`: its debounce drops the promise
    // of a superseded call, which would leave this await hanging forever.
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
    <div class="session-items-list share-items-list">
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
            @update="onUpdate"
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
                        :is-block-end="item.isBlockEnd || false" />
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
                    @toggle-suffix="toggleGroup(item.suffixGroupHead)" />
            </template>
        </VirtualScroller>

        <ChatNavToolbar
            v-show="navHasNavigation"
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
