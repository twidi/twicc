<script setup>
// Orchestration tab content. Two trees, one selector (shown only when both
// exist — see "The two views" below):
//
//   - "sessions": the spawned-session tree, described here;
//   - "agents": the subagents this session launched, at any depth. Live off the
//     agent-link cache, so nothing below (fetch, poll, error state) applies to
//     it — see AgentTreeNode.
//
// The sessions tree: the full spawned-session tree (``spawned_by``
// links) rooted at the session's top-level ancestor, fetched from
// ``/api/projects/<pid>/sessions/<sid>/topology/`` (the same engine as
// ``twicc topology``).
//
// Auto-refresh: while the tab is open we poll the topology every 15s, but only
// as long as at least one node in the tree is still live (any process state
// other than ``dead``). The moment the whole tree is stopped, polling halts; it
// resumes on its own if a node comes back to life on a later fetch. The tab also
// force-fetches once on every (re)activation, independently of that condition.
// A small Live/Stopped indicator in the toolbar mirrors this state. (Polling is
// a stop-gap until the tree is pushed over the WebSocket.)
import { ref, computed, watch, onUnmounted } from 'vue'
import OrchestrationNode from './OrchestrationNode.vue'
import AgentTreeNode from './AgentTreeNode.vue'
import CostDisplay from '../ui/CostDisplay.vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { agentForestCost } from '../../utils/agentTreeMetrics'

const store = useDataStore()
const settingsStore = useSettingsStore()
// Honour the global "Show costs" toggle, like the rest of the app.
const showCosts = computed(() => settingsStore.areCostsShown)

const props = defineProps({
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
    // Whether the session belongs to a spawned-session tree (``spawn_root``).
    // The tab can also be here for subagents alone, in which case there is no
    // topology to fetch and the sessions view is not offered.
    hasSpawnTree: { type: Boolean, default: false },
    active: { type: Boolean, default: false },
})

// ── The two views ───────────────────────────────────────────────────────────
// "sessions": the spawned-session topology (TwiCC sessions created by other
// sessions). "agents": the subagents this session launched, at any depth.
// A session can have either, or both; the selector only appears with both.
// The agent tree is scoped to THIS session, whereas the session tree covers the
// whole spawn tree — deliberate: an agent belongs to the session that ran it.
const hasAgents = computed(() => store.hasSubagents(props.sessionId))
const agentTree = computed(() => store.getAgentTree(props.sessionId))
const agentCount = computed(() => {
    const count = (nodes) => nodes.reduce((n, node) => n + 1 + count(node.children), 0)
    return count(agentTree.value)
})
const runningAgentCount = computed(() => {
    const count = (nodes) => nodes.reduce(
        (n, node) => n + (store.getProcessState(node.id) ? 1 : 0) + count(node.children), 0,
    )
    return count(agentTree.value)
})
// All-inclusive cost of the agent tree, resolved exactly like each node's own.
const agentTotalCost = computed(() => agentForestCost(store, agentTree.value))
const canSwitchView = computed(() => props.hasSpawnTree && hasAgents.value)
// User choice, only honoured when both views exist; otherwise the available one
// wins (a session that loses its last agent must not stay on an empty view).
const selectedView = ref('sessions')
const view = computed(() => {
    if (canSwitchView.value) return selectedView.value
    return props.hasSpawnTree ? 'sessions' : 'agents'
})

const loading = ref(false)
const error = ref(null)
const topology = ref(null)

// Auto-refresh cadence. Temporary fixed poll until the topology is pushed over
// the WebSocket. Kept in a constant so the timer and the indicator copy agree.
const AUTO_REFRESH_INTERVAL = 15000
let autoTimer = null
// In-flight request controller, so a newer load (manual Refresh or tab
// activation) can abort a still-pending one and always win — no stale snapshot
// clobbering a fresher one.
let inFlightController = null

const nodesById = computed(() => {
    const map = {}
    for (const node of topology.value?.nodes ?? []) {
        map[node.id] = node
    }
    return map
})
const rootTree = computed(() => topology.value?.tree ?? null)
const nodeCount = computed(() => topology.value?.node_count ?? 0)
// All-inclusive cost of the whole tree (the root's subtree cost).
const totalCost = computed(() => topology.value?.total_cost ?? null)

// At-a-glance activity breakdown by process state. Buckets: working
// (starting / assistant_turn), awaiting (awaiting_user_input), idle (user_turn),
// stopped (dead). Only non-empty buckets are surfaced.
const ACTIVITY_BUCKETS = [
    { label: 'working', states: ['starting', 'assistant_turn'] },
    { label: 'awaiting', states: ['awaiting_user_input'] },
    { label: 'idle', states: ['user_turn'] },
    { label: 'stopped', states: ['dead'] },
]
const activity = computed(() => {
    const counts = {}
    for (const node of topology.value?.nodes ?? []) {
        const state = node.process?.state ?? 'dead'
        counts[state] = (counts[state] ?? 0) + 1
    }
    return ACTIVITY_BUCKETS
        .map(b => ({ label: b.label, count: b.states.reduce((n, s) => n + (counts[s] ?? 0), 0) }))
        .filter(b => b.count > 0)
})

// Parenthetical shown after the node count, shared by both trees: "All stopped"
// when every node shares one state, otherwise the per-state breakdown
// ("2 working · 9 stopped"). Empty when there is nothing to count.
function summarizeActivity(buckets) {
    const present = buckets.filter(b => b.count > 0)
    if (present.length === 0) return ''
    if (present.length === 1) return `All ${present[0].label}`
    return present.map(b => `${b.count} ${b.label}`).join(' · ')
}
const activitySummary = computed(() => summarizeActivity(activity.value))
// The agent equivalent. An agent has only two states — it works, or it is done
// — so the same phrasing yields "All working", "All stopped", or the mix.
const agentActivitySummary = computed(() => summarizeActivity([
    { label: 'working', count: runningAgentCount.value },
    { label: 'stopped', count: agentCount.value - runningAgentCount.value },
]))

// Auto-refresh gate: the tree is "live" while at least one node (root included)
// is not ``dead``. This single signal drives both the poll timer and the
// toolbar Live/Stopped indicator.
const hasLiveNode = computed(() =>
    (topology.value?.nodes ?? []).some(n => (n.process?.state ?? 'dead') !== 'dead'),
)

// ``silent`` ticks (background polls) never touch ``loading`` and keep the last
// good snapshot on failure, so the tree never flashes a spinner or error banner
// under the user. Manual Refresh and tab-activation loads are non-silent.
async function load({ silent = false } = {}) {
    if (!props.projectId || !props.sessionId) return
    if (!props.hasSpawnTree) return  // no spawned session: nothing to fetch
    // A newer load supersedes any in-flight one (e.g. a manual Refresh landing
    // on top of a background tick).
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
        error.value = null
    } catch (e) {
        if (e.name === 'AbortError') return // superseded by a newer load
        console.error('Failed to load orchestration topology:', e)
        // A failed background tick keeps the last good snapshot on screen; only
        // surface the error banner when there is nothing to fall back to.
        if (!silent || !topology.value) {
            error.value = 'Failed to load the orchestration topology.'
        }
    } finally {
        if (inFlightController === controller) inFlightController = null
        if (!silent) loading.value = false
    }
}

// Start/stop the poll timer reactively: it runs only while the tab is active
// AND the tree still has a live node. When the last node dies the timer stops;
// if a node comes back to life on a later fetch, it restarts on its own.
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

// Refreshing the agent view re-reads the ``/subagents/`` snapshot: the tree
// itself is live over the WebSocket, but the per-agent numbers it carries (cost,
// turns, context) only move with a read.
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

// Force a fresh read every time the tab becomes active, regardless of the poll
// condition or any snapshot already held. Both views: each one's data can have
// moved while the tab was hidden.
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
    if (inFlightController) inFlightController.abort()
})
</script>

<template>
    <div class="orchestration-panel">
        <div class="orch-header">
            <div class="orch-toolbar">
                <div class="orch-toolbar-meta">
                    <span class="orch-toolbar-title">{{ view === 'agents' ? 'Agent tree' : 'Orchestration tree' }}</span>
                    <template v-if="view === 'agents'">
                        <span class="orch-meta-item">{{ agentCount }} agent{{ agentCount > 1 ? 's' : '' }}<template v-if="agentActivitySummary"> ({{ agentActivitySummary }})</template></span>
                        <span v-if="showCosts && agentTotalCost != null" class="orch-meta-item">
                            <CostDisplay :cost="agentTotalCost" /> total
                        </span>
                    </template>
                    <template v-else>
                        <span v-if="nodeCount" class="orch-meta-item">{{ nodeCount }} session{{ nodeCount > 1 ? 's' : '' }}<template v-if="activitySummary"> ({{ activitySummary }})</template></span>
                        <span v-if="showCosts && totalCost != null" class="orch-meta-item">
                            <CostDisplay :cost="totalCost" /> total
                        </span>
                    </template>
                </div>
                <div class="orch-toolbar-actions">
                    <wa-button-group v-if="canSwitchView" label="Tree to show" class="orch-view-switch">
                        <wa-button
                            size="small"
                            :variant="view === 'sessions' ? 'brand' : 'neutral'"
                            :appearance="view === 'sessions' ? 'filled' : 'outlined'"
                            @click="selectedView = 'sessions'"
                        >
                            <wa-icon slot="start" name="diagram-project"></wa-icon>
                            Sessions
                        </wa-button>
                        <wa-button
                            size="small"
                            :variant="view === 'agents' ? 'brand' : 'neutral'"
                            :appearance="view === 'agents' ? 'filled' : 'outlined'"
                            @click="selectedView = 'agents'"
                        >
                            <wa-icon slot="start" name="robot"></wa-icon>
                            Agents
                        </wa-button>
                    </wa-button-group>
                    <span
                        v-if="view === 'sessions' && nodeCount"
                        class="orch-autorefresh"
                        :class="hasLiveNode ? 'is-live' : 'is-stopped'"
                        :title="hasLiveNode
                            ? 'Auto-refreshing every 15s while sessions are live'
                            : 'Auto-refresh stopped — every session is stopped'"
                    >
                        <span v-if="hasLiveNode" class="orch-autorefresh-dot"></span>
                        <wa-icon v-else name="circle-stop"></wa-icon>
                        {{ hasLiveNode ? 'Live' : 'Stopped' }}
                    </span>
                    <wa-button
                        size="small"
                        appearance="plain"
                        :loading="refreshing"
                        :disabled="refreshing"
                        @click="refresh()"
                    >
                        <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                        Refresh
                    </wa-button>
                </div>
            </div>
            <div v-if="view === 'sessions'" class="orch-note">
                Sessions marked <wa-icon name="eye-slash" class="orch-note-icon"></wa-icon> were created hidden by their parent and can't be opened.
            </div>
        </div>

        <div class="orch-content">
            <template v-if="view === 'agents'">
                <div v-if="agentCount" class="orch-tree">
                    <AgentTreeNode
                        v-for="node in agentTree"
                        :key="node.id"
                        :node="node"
                        :session-id="sessionId"
                        :project-id="projectId"
                    />
                </div>
                <div v-else class="orch-state orch-state-empty">
                    <wa-icon name="robot"></wa-icon>
                    <span>No agent.</span>
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
                <div v-else-if="rootTree" class="orch-tree">
                    <OrchestrationNode
                        :node="rootTree"
                        :nodes-by-id="nodesById"
                        :current-session-id="sessionId"
                    />
                </div>
                <div v-else class="orch-state orch-state-empty">
                    <wa-icon name="sitemap"></wa-icon>
                    <span>No orchestration data.</span>
                </div>
            </template>
        </div>
    </div>
</template>

<style scoped>
.orchestration-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
}

.orch-header {
    flex-shrink: 0;
    padding: var(--wa-space-s) var(--wa-space-m);
    border-bottom: var(--divider-size, 1px) solid var(--wa-color-surface-border);
}

.orch-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    column-gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.orch-toolbar-actions {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex-shrink: 0;
}

/* Auto-refresh state indicator. ``is-live`` shows a green pulsing dot + "Live";
   ``is-stopped`` shows a neutral circle-stop + "Stopped" (the whole tree is
   dead, so polling has halted). The dot pulse matches the awaiting-user pulse
   used on the nodes (1.5s, 1→0.3). */
.orch-autorefresh {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
}

.orch-autorefresh.is-live {
    color: var(--wa-color-success-60);
}

.orch-autorefresh-dot {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: var(--wa-color-success-60);
    animation: orch-autorefresh-pulse 1.5s ease-in-out infinite;
}

@keyframes orch-autorefresh-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

.orch-view-switch {
    flex-shrink: 0;
}

.orch-note {
    margin-top: var(--wa-space-2xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.orch-note-icon {
    /* Inline reference to the hidden-session marker, in the flow of the text. */
    vertical-align: -0.1em;
    margin-inline: 0.1em;
}

.orch-toolbar-meta {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    column-gap: var(--wa-space-xs);
    min-width: 0;
}

.orch-toolbar-title {
    font-weight: 600;
}

.orch-meta-item {
    /* Plain inline flow (not inline-flex) so "$ 0.60 total" keeps the natural
       space between the amount and the label. */
    font-weight: 400;
    color: var(--wa-color-text-quiet);
}

/* Middot separator before each meta item (after the title) */
.orch-meta-item::before {
    content: '·';
    margin-right: var(--wa-space-xs);
}

.orch-content {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: var(--wa-space-m);
}

/* Custom tree: all connector geometry (vertical lines + elbows) lives in
   OrchestrationNode. This is just the scroll-area root. */
.orch-tree {
    line-height: 1.5;
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
</style>
