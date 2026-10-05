<script setup>
// Orchestration tab content. Two trees, one switch (shown only when both exist):
//
//   - "sessions": the sessions spawned BY this session (the topology re-rooted on it), with a
//     "Spawned by" line linking to the parent's own Orchestration tab;
//   - "agents": the subagents this session launched, at any depth, live off the agent-link cache
//     (no fetch, poll or error state of its own). Called "subagent" throughout the UI, never just "agent".
//
// Sessions data: ``GET /api/projects/<pid>/sessions/<sid>/topology/`` returns the WHOLE spawn tree rooted
// at its top-level ancestor; this panel finds the current session's subtree in it (the payload is
// unchanged). While the tab is open the topology is polled every 15s, but only as long as at least one
// node of the payload is live (any process state other than ``dead``); the tab also force-fetches once on
// every (re)activation. Polling is a stop-gap until the tree is pushed over the WebSocket.
//
// Each time bar is relative to its direct parent's span (``computeTreeGeometry``); ``now`` is refreshed on each load and
// by a 30s timer that runs only while the tab is active and a displayed node is working.
import { ref, reactive, computed, watch, nextTick, onMounted, onUnmounted, useId } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import { useRoute } from 'vue-router'
import OrchestrationNode from './OrchestrationNode.vue'
import AgentTreeNode from './AgentTreeNode.vue'
import OrchestrationSummary from './OrchestrationSummary.vue'
import OrchestrationTabActivity from './OrchestrationTabActivity.vue'
import SegmentedControl from '../ui/SegmentedControl.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { agentForestCost } from '../../utils/agentTreeMetrics'
import {
    bucketOfProcessState, computeTimeline, computeTreeGeometry, cumulativeSeconds, countBuckets, findSubtree, flattenTree, isoMs, parentOf,
    reuseUnchangedMap, reuseUnchangedNodes, reuseUnchangedTree,
} from '../../utils/orchestrationView'
import { sessionRouteLocation } from '../../utils/sessionRoute'

const store = useDataStore()
const settingsStore = useSettingsStore()
const route = useRoute()
// Honour the global "Show costs" toggle, like the rest of the app.
const showCosts = computed(() => settingsStore.areCostsShown)

const props = defineProps({
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
    // Whether the session belongs to a spawned-session tree (``spawn_root``). The tab can also be here for
    // subagents alone, in which case there is no topology to fetch and the sessions view is not offered.
    hasSpawnTree: { type: Boolean, default: false },
    active: { type: Boolean, default: false },
})

// ── The two views ───────────────────────────────────────────────────────────
const hasAgents = computed(() => store.hasSubagents(props.sessionId))
const agentTree = computed(() => store.getAgentTree(props.sessionId))
// The tab label's indicators, repeated on the view switch's inactive segment.
const orchestrationActivity = computed(() => store.getOrchestrationActivity(props.sessionId))
const canSwitchView = computed(() => props.hasSpawnTree && hasAgents.value)
const VIEW_OPTIONS = [
    { value: 'sessions', label: 'Sessions', icon: 'diagram-project' },
    { value: 'agents', label: 'Subagents', icon: 'robot' },
]
// User choice, only honoured when both views exist; otherwise the available one wins.
const selectedView = ref('sessions')
const view = computed(() => {
    if (canSwitchView.value) return selectedView.value
    return props.hasSpawnTree ? 'sessions' : 'agents'
})

// ── Lazy mounting, kept alive ───────────────────────────────────────────────
// Rendering a long list (100+ subagents, 250 sessions) is synchronous: done in the task of the click, it
// would keep the browser from painting the switch's new selection until every card is rendered. So a view's
// body is mounted the first time it is selected, AFTER the paint (a light placeholder stands in meanwhile),
// then stays mounted and is only toggled with ``v-show``: going back is instant and the fresh data patches it.
// The view shown when the panel opens is mounted at once.
const mountedViews = reactive({ sessions: false, agents: false })
mountedViews[view.value] = true
let mountToken = 0
let unmounted = false
// Resolves once the browser has painted the current state: the first frame runs the render that follows the
// state change, the second runs after that frame has been presented.
const pendingFrames = new Set()
const frame = (callback) => {
    const id = requestAnimationFrame((time) => { pendingFrames.delete(id); callback(time) })
    pendingFrames.add(id)
}
const afterPaint = () => new Promise(resolve => frame(() => frame(resolve)))
watch(view, async (shown) => {
    if (mountedViews[shown]) return
    // Not a user switch (only one view exists): nothing to paint first.
    if (!canSwitchView.value) {
        mountedViews[shown] = true
        return
    }
    const token = ++mountToken
    await nextTick()
    await afterPaint()
    // Superseded by another switch, left for another view, or gone meanwhile: do not mount.
    if (unmounted || token !== mountToken || view.value !== shown) return
    mountedViews[shown] = true
})

const loading = ref(false)
const error = ref(null)
const topology = ref(null)
// Reference clock of the working nodes' bars (ms).
const now = ref(Date.now())

const AUTO_REFRESH_INTERVAL = 15000
const NOW_INTERVAL = 30000
let autoTimer = null
let nowTimer = null
// In-flight request controller, so a newer load can abort a still-pending one and always win.
let inFlightController = null

// ── Sessions view: the topology re-rooted on the current session ────────────
// Stable across reloads: an unchanged map keeps its identity, so the cards bound to it are not re-patched.
const nodesById = computed((previous) => {
    const map = {}
    for (const node of topology.value?.nodes ?? []) map[node.id] = node
    return reuseUnchangedMap(previous, map)
})
// ``null`` when the current session is not in the payload (a corrupt spawn edge): "No orchestration data."
const subtree = computed(() => {
    const tree = topology.value?.tree
    return tree && nodesById.value[props.sessionId] ? findSubtree(tree, props.sessionId) : null
})
const currentNode = computed(() => nodesById.value[props.sessionId] ?? null)
const sessionNodes = computed(() => (subtree.value ? flattenTree(subtree.value) : []))
const stateBucketOf = (id) => bucketOfProcessState(nodesById.value[id]?.process?.state ?? 'dead')
// Tiles count the DESCENDANTS (the current session is the first card, not a spawned session).
const sessionCounts = computed(() => countBuckets(sessionNodes.value.slice(1).map(n => stateBucketOf(n.id))))
// Cost of the current session and everything below it (equals the root card's Σ).
const sessionsCost = computed(() => currentNode.value?.subtree_total_cost ?? null)

// "Spawned by": the direct parent only. A hidden parent cannot be opened (plain text + crossed-out eye).
const parentNode = computed(() => parentOf(nodesById.value, props.sessionId))
const parentTitle = computed(() => {
    const t = parentNode.value?.session?.title
    return (t && t.trim()) ? t : (parentNode.value?.id ?? '').slice(0, 8)
})
const parentHidden = computed(() => parentNode.value?.session?.hidden === true)
const parentRoute = computed(() => (parentNode.value && !parentHidden.value
    ? sessionRouteLocation(
        { id: parentNode.value.id, project_id: parentNode.value.session.project_id },
        route,
        { tab: 'orchestration' },
    )
    : null))
// The note explains the crossed-out eye: shown iff a hidden session is displayed (parent or any card).
const showHiddenNote = computed(() => parentHidden.value
    || sessionNodes.value.some(n => nodesById.value[n.id]?.session?.hidden === true))

// ── Agents view ─────────────────────────────────────────────────────────────
const agentNodes = computed(() => agentTree.value.flatMap(flattenTree))
const agentIsRunning = (id) => !!store.getProcessState(id)
const agentCounts = computed(() => countBuckets(agentNodes.value.map(n => (agentIsRunning(n.id) ? 'working' : 'stopped'))))
const agentTotalCost = computed(() => agentForestCost(store, agentTree.value))
// Nothing to show yet: the ``/subagents/`` snapshot never landed and no agent is cached. Once it has, an empty
// list means "No subagent.".
const agentsPending = computed(() => !agentNodes.value.length && !store.areSubagentsLoaded(props.sessionId))

// ── Time bars: each node relative to its direct parent ──────────────────────
// The current session's own span anchors the first level in BOTH views: it is the first card of the sessions
// view, and an extra item (no card) of the subagents view. ``null`` while its row is not loaded.
const currentSessionItem = computed(() => {
    const row = store.getSession(props.sessionId)
    if (!row) return null
    return {
        id: props.sessionId,
        start: isoMs(row.created_at),
        end: isoMs(row.last_new_content_at),
        working: bucketOfProcessState(store.getProcessState(props.sessionId)?.state ?? 'dead') === 'working',
    }
})
// Each view has its OWN items and geometry, independent of the selected view: both trees stay mounted, and a
// switch must not re-patch them with the other view's bars.
const agentItems = computed(() => {
    const items = agentNodes.value.map(n => ({
        id: n.id,
        start: isoMs(n.entry?.startedAt),
        end: isoMs(n.entry?.stoppedAt ?? n.entry?.agentStoppedAt),
        working: agentIsRunning(n.id),
    }))
    return currentSessionItem.value ? [currentSessionItem.value, ...items] : items
})
const sessionItems = computed(() => sessionNodes.value.map(n => {
    const node = nodesById.value[n.id]
    return {
        id: n.id,
        start: isoMs(node?.session?.created_at),
        end: isoMs(node?.session?.last_new_content_at),
        working: stateBucketOf(n.id) === 'working',
    }
}))
const timelineItems = computed(() => (view.value === 'agents' ? agentItems.value : sessionItems.value))
// The global range only serves the cumulative-time tile (``rangeEnd``); the bars use the per-view geometry below.
const globalTimeline = computed(() => computeTimeline(timelineItems.value, now.value))
// Bar geometry: root = the re-rooted subtree (sessions view) or a virtual node standing for the current
// session (subagents view; with no item while its row is not loaded, the first level then ranges itself).
function geometryOf(getRoot, items) {
    return computed((previous) => {
        const root = getRoot()
        if (!root) return { geometry: {} }
        const itemsById = Object.fromEntries(items.value.map(item => [item.id, item]))
        // Per-node geometry objects (and the whole result) are reused when unchanged, like the nodes.
        const geometry = reuseUnchangedMap(previous?.geometry, computeTreeGeometry(root, itemsById, now.value))
        return geometry === previous?.geometry ? previous : { geometry }
    })
}
const agentsTimeline = geometryOf(() => ({ id: props.sessionId, children: agentTree.value }), agentItems)
const sessionsTimeline = geometryOf(() => subtree.value, sessionItems)
const hasWorkingNode = computed(() => timelineItems.value.some(item => item.working))

// The header's tiles. ``null`` while there is nothing to summarise (loading, error, no data).
const summary = computed(() => {
    // Every node below the current session: the current session (first card, or the extra item of the
    // subagents view) is never counted.
    const cumulative = cumulativeSeconds(timelineItems.value, globalTimeline.value.range?.end, props.sessionId)
    if (view.value === 'agents') {
        return { kind: 'agents', counts: agentCounts.value, cost: agentTotalCost.value, cumulativeSeconds: cumulative }
    }
    if (!subtree.value) return null
    return { kind: 'sessions', counts: sessionCounts.value, cost: sessionsCost.value, cumulativeSeconds: cumulative }
})

// Auto-refresh gate: the poll runs while at least one node of the WHOLE payload is not ``dead`` (a live
// ancestor or sibling keeps the parent line and the payload fresh), whatever is displayed.
const hasLiveNode = computed(() =>
    (topology.value?.nodes ?? []).some(n => (n.process?.state ?? 'dead') !== 'dead'),
)

// ``loading`` covers EVERY topology read in flight (user click, activation, silent poll tick): it drives the
// Refresh button's spinner and nothing else, so the tree already rendered stays on screen while it runs.
// ``silent`` ticks (background polls) keep the last good snapshot on failure and raise no error banner.
async function load({ silent = false } = {}) {
    if (!props.projectId || !props.sessionId) return
    if (!props.hasSpawnTree) return  // no spawned session: nothing to fetch
    if (inFlightController) inFlightController.abort()
    const controller = new AbortController()
    inFlightController = controller
    loading.value = true
    try {
        const url = `/api/projects/${encodeURIComponent(props.projectId)}/sessions/${encodeURIComponent(props.sessionId)}/topology/`
        const response = await fetch(url, { signal: controller.signal })
        if (!response.ok) {
            throw new Error(`Failed to load topology: ${response.status}`)
        }
        const payload = await response.json()
        const previous = topology.value
        // Unchanged nodes / subtrees keep their previous objects: Vue then patches only what changed.
        topology.value = previous
            ? { ...payload, nodes: reuseUnchangedNodes(previous.nodes, payload.nodes ?? []), tree: reuseUnchangedTree(previous.tree, payload.tree) }
            : payload
        now.value = Date.now()
        error.value = null
    } catch (e) {
        if (e.name === 'AbortError') return // superseded by a newer load
        console.error('Failed to load orchestration topology:', e)
        if (!silent || !topology.value) {
            error.value = 'Failed to load the orchestration topology.'
        }
    } finally {
        // A superseded (aborted) read leaves the flag to the newer one, which owns the controller now.
        if (inFlightController === controller) {
            inFlightController = null
            loading.value = false
        }
    }
}

function stopAuto() {
    if (autoTimer !== null) {
        clearInterval(autoTimer)
        autoTimer = null
    }
}
function syncAuto() {
    const shouldRun = props.active && hasLiveNode.value
    if (shouldRun && autoTimer === null) {
        autoTimer = setInterval(() => load({ silent: true }), AUTO_REFRESH_INTERVAL)
    } else if (!shouldRun) {
        stopAuto()
    }
}
watch([() => props.active, hasLiveNode], syncAuto, { immediate: true })

function stopNow() {
    if (nowTimer !== null) {
        clearInterval(nowTimer)
        nowTimer = null
    }
}
// The ``now`` timer: only while the tab is active and a displayed node is working.
function syncNow() {
    const shouldRun = props.active && hasWorkingNode.value
    if (shouldRun && nowTimer === null) {
        now.value = Date.now()
        nowTimer = setInterval(() => { now.value = Date.now() }, NOW_INTERVAL)
    } else if (!shouldRun) {
        stopNow()
    }
}
watch([() => props.active, hasWorkingNode], syncNow, { immediate: true })

// Refreshing the agent view re-reads the ``/subagents/`` snapshot: the tree itself is live over the
// WebSocket, but the per-agent numbers it carries (cost, turns, context, model) only move with a read.
// A counter: the activation read, the "agents appeared" read and a click can overlap.
const agentReads = ref(0)
// The Refresh button spins while ANY read is in flight (topology or subagents snapshot), whichever view is shown.
const refreshing = computed(() => loading.value || agentReads.value > 0)
async function refreshAgents() {
    agentReads.value++
    try {
        await store.fetchSubagentsState(props.projectId, props.sessionId)
    } finally {
        agentReads.value--
    }
}
function refresh() {
    return view.value === 'agents' ? refreshAgents() : load()
}

// Force a fresh read every time the tab becomes active, regardless of the poll condition.
watch(
    () => props.active,
    (active) => {
        if (!active) return
        load()
        if (hasAgents.value) refreshAgents()
    },
    { immediate: true },
)

// The tab can become active BEFORE what it shows has arrived: on a direct URL or a bookmark the session
// row (its ``spawn_root``, hence ``hasSpawnTree``) and the agent links land after the first render, and
// ``load()`` is a no-op without a spawn tree. Re-read as soon as each source becomes available while the
// tab is active; otherwise it would sit on an empty state until the next activation.
watch(() => props.hasSpawnTree, (has) => {
    if (has && props.active) load()
})
watch(hasAgents, (has) => {
    if (has && props.active) refreshAgents()
})

// ── Scroll-to-edge buttons ──────────────────────────────────────────────────
// Shown only when the list actually scrolls. The scrolling element is ``.orch-content`` in the wide layout
// and ``.orch-frame`` in the narrow one (below 480px the header scrolls away with the body), so both are
// checked; the one that does not scroll just reports no overflow. The trees are observed too: in the wide
// layout the content box keeps its size while the tree inside grows, so it would never notify by itself.
// Each view has its own tree ref. A hidden view (``display: none``) contributes no height to the scrollers
// and its observer reports a 0 size: that only triggers a harmless re-check, which reads the (visible)
// frame and content boxes, so the result always follows the shown view.
const frameEl = ref(null)
const contentEl = ref(null)
const sessionsTreeEl = ref(null)
const agentsTreeEl = ref(null)
const hasOverflow = ref(false)
const scrollBottomButtonId = useId()
const scrollTopButtonId = useId()

function updateOverflow() {
    hasOverflow.value = [frameEl.value, contentEl.value].some(
        el => el && el.scrollHeight > el.clientHeight + 1,
    )
}
useResizeObserver([frameEl, contentEl, sessionsTreeEl, agentsTreeEl], updateOverflow)
// Data changes that alter the list's height without necessarily resizing an observed box.
watch(
    [view, () => mountedViews.sessions, () => mountedViews.agents, loading, error, topology, () => sessionNodes.value.length, () => agentNodes.value.length],
    () => nextTick(updateOverflow),
    { flush: 'post' },
)
// Both views share the scrollers, so each view remembers its own scroll position: the position of the view
// being left is saved before the switch renders, and the newly shown view gets back its own (the top the
// first time).
const scrollMemory = { sessions: { content: 0, frame: 0 }, agents: { content: 0, frame: 0 } }
watch(view, (_shown, left) => {
    if (!left || !scrollMemory[left]) return
    scrollMemory[left].content = contentEl.value?.scrollTop ?? 0
    scrollMemory[left].frame = frameEl.value?.scrollTop ?? 0
}, { flush: 'pre' })
watch(view, (shown) => {
    const saved = scrollMemory[shown] ?? { content: 0, frame: 0 }
    if (contentEl.value) contentEl.value.scrollTop = saved.content
    if (frameEl.value) frameEl.value.scrollTop = saved.frame
}, { flush: 'post' })
onMounted(() => nextTick(updateOverflow))

function scrollListTo(toBottom) {
    for (const el of [contentEl.value, frameEl.value]) {
        el?.scrollTo({ top: toBottom ? el.scrollHeight : 0, behavior: 'smooth' })
    }
}

onUnmounted(() => {
    unmounted = true
    for (const id of pendingFrames) cancelAnimationFrame(id)
    pendingFrames.clear()
    stopAuto()
    stopNow()
    if (inFlightController) inFlightController.abort()
})
</script>

<template>
    <div class="orchestration-panel">
        <div ref="frameEl" class="orch-frame">
            <div class="orch-header">
                <div class="orch-toolbar">
                    <SegmentedControl
                        v-if="canSwitchView"
                        class="orch-view-switch"
                        label="Tree to show"
                        :model-value="view"
                        :options="VIEW_OPTIONS"
                        @update:model-value="selectedView = $event"
                    >
                        <!-- Only the view NOT shown: the other category, when it is busy. -->
                        <template #option-sessions>
                            <OrchestrationTabActivity v-if="view !== 'sessions'" class="orch-switch-activity" only="sessions" :activity="orchestrationActivity" />
                        </template>
                        <template #option-agents>
                            <OrchestrationTabActivity v-if="view !== 'agents'" class="orch-switch-activity" only="subagents" :activity="orchestrationActivity" />
                        </template>
                    </SegmentedControl>
                    <span v-else class="orch-mode-tag">
                        <wa-icon :name="view === 'agents' ? 'robot' : 'diagram-project'"></wa-icon>
                        {{ view === 'agents' ? 'Subagents' : 'Sessions' }}
                    </span>
                    <wa-button
                        size="small"
                        appearance="plain"
                        title="Refresh"
                        :loading="refreshing"
                        :disabled="refreshing"
                        @click="refresh()"
                    >
                        <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                        <span class="orch-refresh-label">Refresh</span>
                    </wa-button>
                </div>
                <OrchestrationSummary v-if="summary" v-bind="summary" :show-costs="showCosts" />
            </div>

            <div ref="contentEl" class="orch-content">
                <!-- Edge-to-edge scroll buttons, only when the list scrolls: "down" opens the list,
                     "up" closes it (its twin sits after the views). -->
                <Transition name="orch-scroll-fade">
                    <wa-button
                        v-if="hasOverflow"
                        :id="scrollBottomButtonId"
                        class="orch-scroll-btn orch-scroll-btn--top floating-over-text"
                        size="small"
                        variant="neutral"
                        appearance="filled"
                        aria-label="Scroll to bottom"
                        @click="scrollListTo(true)"
                    >
                        <wa-icon name="arrow-down"></wa-icon>
                    </wa-button>
                </Transition>
                <AppTooltip
                    v-if="hasOverflow"
                    :for="scrollBottomButtonId"
                    placement="left"
                >Scroll to bottom</AppTooltip>
                <!-- Both views stay mounted once shown (v-show); a view not mounted yet shows a placeholder. -->
                <div v-show="view === 'agents'" class="orch-view">
                    <div v-if="!mountedViews.agents || agentsPending" class="orch-state">
                        <wa-spinner></wa-spinner>
                        <span>Loading subagents…</span>
                    </div>
                    <div v-else-if="agentNodes.length" ref="agentsTreeEl" class="orch-tree">
                        <AgentTreeNode
                            v-for="node in agentTree"
                            :key="node.id"
                            :node="node"
                            :session-id="sessionId"
                            :project-id="projectId"
                            :timeline="agentsTimeline"
                        />
                    </div>
                    <div v-else class="orch-state orch-state-empty">
                        <wa-icon name="robot"></wa-icon>
                        <span>No subagent.</span>
                    </div>
                </div>
                <div v-show="view === 'sessions'" class="orch-view">
                    <!-- Spinner until the first snapshot lands (not only while a request is in flight: the first read can start late). -->
                    <div v-if="!mountedViews.sessions || (!topology && !error)" class="orch-state">
                        <wa-spinner></wa-spinner>
                        <span>Loading topology…</span>
                    </div>
                    <template v-else>
                        <!-- A failed read never replaces a tree already shown: the banner sits above it. -->
                        <wa-callout v-if="error" variant="danger" size="small">
                            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                            {{ error }}
                        </wa-callout>
                        <template v-if="subtree">
                            <div v-if="parentNode" class="orch-parent">
                                <wa-icon name="arrow-turn-up" class="orch-parent-icon"></wa-icon>
                                <span class="orch-parent-label">Spawned by</span>
                                <router-link v-if="parentRoute" :to="parentRoute" class="orch-parent-link">{{ parentTitle }}</router-link>
                                <template v-else>
                                    <span class="orch-parent-link">{{ parentTitle }}</span>
                                    <wa-icon name="eye-slash" label="Hidden session" title="Hidden session"></wa-icon>
                                </template>
                            </div>
                            <div v-if="showHiddenNote" class="orch-note">
                                Sessions marked <wa-icon name="eye-slash" class="orch-note-icon"></wa-icon> were created hidden by their parent and can't be opened.
                            </div>
                            <div ref="sessionsTreeEl" class="orch-tree">
                                <OrchestrationNode
                                    :node="subtree"
                                    :nodes-by-id="nodesById"
                                    :current-session-id="sessionId"
                                    :timeline="sessionsTimeline"
                                />
                            </div>
                            <div v-if="!subtree.children.length" class="orch-empty-line">
                                <wa-icon name="diagram-project"></wa-icon>
                                This session has not spawned any session.
                            </div>
                        </template>
                        <div v-else-if="topology" class="orch-state orch-state-empty">
                            <wa-icon name="sitemap"></wa-icon>
                            <span>No orchestration data.</span>
                        </div>
                    </template>
                </div>
                <Transition name="orch-scroll-fade">
                    <wa-button
                        v-if="hasOverflow"
                        :id="scrollTopButtonId"
                        class="orch-scroll-btn floating-over-text"
                        size="small"
                        variant="neutral"
                        appearance="filled"
                        aria-label="Scroll to top"
                        @click="scrollListTo(false)"
                    >
                        <wa-icon name="arrow-up"></wa-icon>
                    </wa-button>
                </Transition>
                <AppTooltip
                    v-if="hasOverflow"
                    :for="scrollTopButtonId"
                    placement="left"
                >Scroll to top</AppTooltip>
            </div>
        </div>
    </div>
</template>

<style scoped>
/* The status icon on the view switch's inactive segment sits 2px lower, on both segments. */
.orch-switch-activity {
    transform: translateY(2px);
}

/* The pane is a size container: narrow layouts follow ITS width (set by the dock layout), not the viewport. */
.orchestration-panel {
    container: orch / inline-size;
    height: 100%;
    min-height: 0;
    /* One card look for the node cards (.ocard) and the summary tiles (.osum-tile). Light: the
       translucent raised surface and the surface border, no shadow. */
    --orch-card-bg: color-mix(in oklab, var(--wa-color-surface-raised) 55%, transparent);
    --orch-card-border: var(--wa-color-surface-border);
    --orch-card-shadow: 0 0 transparent;
}

/* Dark: the raised surface vanishes over the dark veil, so the card is a lit brand tint over the
   opaque page surface (the idiom of the tool cards), with a brand hairline and the level-2 depth. */
.wa-dark .orchestration-panel {
    --orch-card-bg: color-mix(in oklab, var(--wa-color-brand-60) 24%, var(--surface-solid));
    --orch-card-border: color-mix(in oklab, var(--wa-color-brand-60) 55%, var(--surface-solid));
    --orch-card-shadow: var(--depth-2);
}

.orch-frame {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}

.orch-header {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    padding: var(--wa-space-s) var(--wa-space-m) var(--wa-space-m);
    border-bottom: var(--divider-size, 1px) solid var(--wa-color-surface-border);
}

.orch-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
}

.orch-view-switch {
    flex-shrink: 0;
}

.orch-mode-tag {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    font-weight: 600;
}

.orch-mode-tag wa-icon {
    color: var(--wa-color-brand-60);
}

.orch-content {
    /* Anchor of the floating "scroll to bottom" button (it scrolls with the content). */
    position: relative;
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--wa-space-m);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

/* The "Spawned by" line: the parent's title is the only link. */
.orch-parent {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
    padding: var(--wa-space-xs) var(--wa-space-s);
    border-radius: var(--wa-border-radius-m);
    border: 1px dashed var(--wa-color-brand-border-quiet);
    background: color-mix(in oklab, var(--wa-color-brand-fill-quiet) 30%, transparent);
    font-size: var(--wa-font-size-s);
}

.orch-parent-icon {
    flex: none;
    color: var(--wa-color-brand-60);
}

.orch-parent-label {
    /* The label never wraps: only the parent's title shrinks (ellipsis). */
    flex: none;
    white-space: nowrap;
    color: var(--wa-color-text-quiet);
}

.orch-parent-link {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 600;
    color: inherit;
    text-decoration: none;
}

a.orch-parent-link:hover {
    text-decoration: underline;
}

.orch-note {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.orch-note-icon {
    /* Inline reference to the hidden-session marker, in the flow of the text. */
    vertical-align: -0.1em;
    margin-inline: 0.1em;
}

.orch-view {
    /* A view's wrapper keeps the content's own column layout (v-show only toggles its display). */
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    flex: none;
}

.orch-tree {
    display: flex;
    flex-direction: column;
    /* Top-level roots: same gap as between sibling cards (2 * --orch-pad). */
    gap: calc(2 * var(--wa-space-2xs));
    line-height: 1.5;
}

.orch-empty-line {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.orch-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 200px;
    color: var(--wa-color-text-quiet);
}

.orch-state-empty {
    flex-direction: column;
    font-size: var(--wa-font-size-l);
}

/* Scroll-to-edge buttons: same look as the Plan tab's "scroll to top" (FilePane.vue
   .preview-action-btn / .preview-scroll-top-btn, scoped there, hence repeated here): a small round
   button, subtle at rest and solid on hover, on the translucent .floating-over-text surface
   (styles/transcript-tokens.css). Unlike it, they are in the flow, right-aligned. */
.orch-scroll-btn {
    align-self: flex-end;
    flex: none;
    opacity: 0.6;
    transition: opacity 0.15s ease;
}
/* The top button ("scroll to bottom") takes no line of its own: it floats over the top-right corner of the
   list, in the content's padding band, and scrolls away with the content. */
.orch-scroll-btn--top {
    position: absolute;
    top: var(--wa-space-2xs);
    right: var(--wa-space-2xs);
    z-index: 2;
}
.orch-scroll-btn:hover {
    opacity: 1;
}
.orch-scroll-btn::part(base) {
    border-radius: 50%;
    aspect-ratio: 1;
    padding: 0;
}
.orch-scroll-fade-enter-active,
.orch-scroll-fade-leave-active {
    transition: opacity 0.2s ease;
}
.orch-scroll-fade-enter-from,
.orch-scroll-fade-leave-to {
    opacity: 0 !important;
}

/* Narrow pane: the switch and Refresh go icon-only (labels stay for assistive tech), and the header is no
   longer fixed — it scrolls away with the body, so a short pane keeps room for the cards. */
@container orch (max-width: 420px) {
    .orch-view-switch :deep(.segmented-icon) {
        margin-inline-end: 0;
    }

    .orch-view-switch :deep(.segmented-label),
    .orch-refresh-label {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0 0 0 0);
        white-space: nowrap;
    }
}

@container orch (max-width: 480px) {
    .orch-frame {
        display: block;
        overflow: auto;
    }

    .orch-content {
        overflow: visible;
    }
}
</style>
