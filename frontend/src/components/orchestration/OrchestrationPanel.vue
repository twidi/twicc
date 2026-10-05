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
// The time bars share one range computed here (``computeTimeline``); ``now`` is refreshed on each load and
// by a 30s timer that runs only while the tab is active and a displayed node is working.
import { ref, computed, watch, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import OrchestrationNode from './OrchestrationNode.vue'
import AgentTreeNode from './AgentTreeNode.vue'
import OrchestrationSummary from './OrchestrationSummary.vue'
import OrchestrationTabActivity from './OrchestrationTabActivity.vue'
import SegmentedControl from '../ui/SegmentedControl.vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { agentForestCost } from '../../utils/agentTreeMetrics'
import {
    bucketOfProcessState, computeTimeline, cumulativeSeconds, countBuckets, findSubtree, flattenTree, isoMs, parentOf,
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
const nodesById = computed(() => {
    const map = {}
    for (const node of topology.value?.nodes ?? []) map[node.id] = node
    return map
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

// ── Time bars: one range per view, over every displayed node ────────────────
const timelineItems = computed(() => (view.value === 'agents'
    ? agentNodes.value.map(n => ({
        id: n.id,
        start: isoMs(n.entry?.startedAt),
        end: isoMs(n.entry?.stoppedAt ?? n.entry?.agentStoppedAt),
        working: agentIsRunning(n.id),
    }))
    : sessionNodes.value.map(n => {
        const node = nodesById.value[n.id]
        return {
            id: n.id,
            start: isoMs(node?.session?.created_at),
            end: isoMs(node?.session?.last_new_content_at),
            working: stateBucketOf(n.id) === 'working',
        }
    })))
const timeline = computed(() => computeTimeline(timelineItems.value, now.value))
const hasWorkingNode = computed(() => timelineItems.value.some(item => item.working))

// The header's tiles. ``null`` while there is nothing to summarise (loading, error, no data).
const summary = computed(() => {
    const spanSeconds = timeline.value.range?.spanSeconds ?? null
    // Every node below the current session: every subagent, or every session but the current one.
    const cumulative = cumulativeSeconds(
        timelineItems.value,
        timeline.value.range?.end,
        view.value === 'agents' ? null : props.sessionId,
    )
    if (view.value === 'agents') {
        return { kind: 'agents', counts: agentCounts.value, cost: agentTotalCost.value, spanSeconds, cumulativeSeconds: cumulative }
    }
    if (!subtree.value) return null
    return { kind: 'sessions', counts: sessionCounts.value, cost: sessionsCost.value, spanSeconds, cumulativeSeconds: cumulative }
})

// Auto-refresh gate: the poll runs while at least one node of the WHOLE payload is not ``dead`` (a live
// ancestor or sibling keeps the parent line and the payload fresh), whatever is displayed.
const hasLiveNode = computed(() =>
    (topology.value?.nodes ?? []).some(n => (n.process?.state ?? 'dead') !== 'dead'),
)

// ``silent`` ticks (background polls) never touch ``loading`` and keep the last good snapshot on failure,
// so the tree never flashes a spinner or error banner under the user.
async function load({ silent = false } = {}) {
    if (!props.projectId || !props.sessionId) return
    if (!props.hasSpawnTree) return  // no spawned session: nothing to fetch
    if (inFlightController) inFlightController.abort()
    const controller = new AbortController()
    inFlightController = controller
    if (!silent) loading.value = true
    try {
        const url = `/api/projects/${encodeURIComponent(props.projectId)}/sessions/${encodeURIComponent(props.sessionId)}/topology/`
        const response = await fetch(url, { signal: controller.signal })
        if (!response.ok) {
            throw new Error(`Failed to load topology: ${response.status}`)
        }
        topology.value = await response.json()
        now.value = Date.now()
        error.value = null
    } catch (e) {
        if (e.name === 'AbortError') return // superseded by a newer load
        console.error('Failed to load orchestration topology:', e)
        if (!silent || !topology.value) {
            error.value = 'Failed to load the orchestration topology.'
        }
    } finally {
        if (inFlightController === controller) inFlightController = null
        if (!silent) loading.value = false
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
const agentsLoading = ref(false)
const refreshing = computed(() => (view.value === 'agents' ? agentsLoading.value : loading.value))
async function refreshAgents() {
    agentsLoading.value = true
    try {
        await store.fetchSubagentsState(props.projectId, props.sessionId)
    } finally {
        agentsLoading.value = false
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

onUnmounted(() => {
    stopAuto()
    stopNow()
    if (inFlightController) inFlightController.abort()
})
</script>

<template>
    <div class="orchestration-panel">
        <div class="orch-frame">
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

            <div class="orch-content">
                <template v-if="view === 'agents'">
                    <div v-if="agentNodes.length" class="orch-tree">
                        <AgentTreeNode
                            v-for="node in agentTree"
                            :key="node.id"
                            :node="node"
                            :session-id="sessionId"
                            :project-id="projectId"
                            :timeline="timeline"
                        />
                    </div>
                    <div v-else class="orch-state orch-state-empty">
                        <wa-icon name="robot"></wa-icon>
                        <span>No subagent.</span>
                    </div>
                </template>
                <template v-else>
                    <div v-if="loading && !topology" class="orch-state">
                        <wa-spinner></wa-spinner>
                        <span>Loading topology…</span>
                    </div>
                    <wa-callout v-else-if="error" variant="danger" size="small">
                        <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                        {{ error }}
                    </wa-callout>
                    <template v-else-if="subtree">
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
                        <div class="orch-tree">
                            <OrchestrationNode
                                :node="subtree"
                                :nodes-by-id="nodesById"
                                :current-session-id="sessionId"
                                :timeline="timeline"
                            />
                        </div>
                        <div v-if="!subtree.children.length" class="orch-empty-line">
                            <wa-icon name="diagram-project"></wa-icon>
                            This session has not spawned any session.
                        </div>
                    </template>
                    <div v-else class="orch-state orch-state-empty">
                        <wa-icon name="sitemap"></wa-icon>
                        <span>No orchestration data.</span>
                    </div>
                </template>
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
    color: var(--wa-color-brand-60);
}

.orch-parent-label {
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
