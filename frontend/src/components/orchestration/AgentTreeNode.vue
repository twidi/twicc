<script setup>
// One node of the subagent tree, rendered recursively. Same visual language and
// the exact same connector geometry as OrchestrationNode (both import
// ``treeNode.css``): title + status, own cost with the parent's cumulative sum,
// dates, duration, turns and the context-usage ring. What an agent has no
// equivalent of — agent settings, project, annotations — is simply absent.
//
// The tree comes from the agent-link cache (``buildAgentTree``), which is kept
// live by the WS ``agent_link_created`` / ``agent_stopped`` events, so this view
// needs no polling of its own.
//
// Numbers come from the agent's own ``Session`` row when it is loaded (live,
// refreshed by ``session_updated``), else from the ``metrics`` block the
// ``/subagents/`` snapshot carries — historical agents have no row in the store.
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppTooltip from '../ui/AppTooltip.vue'
import CostDisplay from '../ui/CostDisplay.vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { getProviderHelpers } from '../../providers'
import { getAgentDisplay } from '../../utils/agentLabel'
import { agentCost, agentSubtreeCost } from '../../utils/agentTreeMetrics'
import { formatDate, formatDuration } from '../../utils/date'
import { sessionRouteLocation } from '../../utils/sessionRoute'

const store = useDataStore()
const settingsStore = useSettingsStore()
const route = useRoute()

// Honour the global "Show costs" toggle, like the rest of the app.
const showCosts = computed(() => settingsStore.areCostsShown)

const props = defineProps({
    // Tree node from ``buildAgentTree``: { id, entry, children: [...] }
    node: { type: Object, required: true },
    // The session that owns the tree (the root of every agent route here).
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
})

const entry = computed(() => props.node.entry)
const hasChildren = computed(() => (props.node.children?.length ?? 0) > 0)

// The name the launcher gave this agent (see utils/agentLabel.js); ``Subagent
// "<short id>"`` when nothing named it — this tab says "subagent" throughout,
// to tell these apart from the sessions in the other tree.
const label = computed(() => {
    const { name, isFallback } = getAgentDisplay(props.node.id, store)
    return isFallback ? `Subagent "${name}"` : name
})

// A running agent carries a process state — real, or the synthetic one the
// agent-link cache maintains. Same signal as the subagent tab's indicator.
const isRunning = computed(() => !!store.getProcessState(props.node.id))

const status = computed(() => (isRunning.value
    ? { label: 'Working', icon: 'robot', color: 'var(--wa-color-blue-60)', pulse: true }
    : { label: 'Stopped', icon: 'circle-stop', color: 'var(--wa-color-neutral-50)', pulse: false }))

// Opening an agent goes through the regular subagent route (the same one the
// in-chat "View Agent" button uses), so it lands in a normal agent tab.
const agentRoute = computed(() => sessionRouteLocation(
    { id: props.sessionId, project_id: props.projectId },
    route,
    { subagentId: props.node.id },
))

// ── Numbers ─────────────────────────────────────────────────────────────────
const ownCost = computed(() => agentCost(store, props.node.id, entry.value?.metrics))
const cumulativeCost = computed(() => agentSubtreeCost(store, props.node))

const turnsLabel = computed(() => {
    const row = store.getSession(props.node.id)
    return row?.user_message_count ?? entry.value?.metrics?.userMessageCount ?? null
})

// Context window: an agent has no settings of its own — it runs inside its
// launcher's session, so the window is the root session's effective one (the
// same value the SessionHeader ring uses). Usage is the agent's own.
const contextUsage = computed(() => {
    const row = store.getSession(props.node.id)
    return row?.context_usage ?? entry.value?.metrics?.contextUsage ?? null
})
const contextMax = computed(() => store.getEffectiveContextMax(props.sessionId))
const contextUsagePercentage = computed(() => {
    const usage = contextUsage.value
    const max = contextMax.value
    if (usage == null || !max) return null
    return Math.round((usage / max) * 100)
})
const contextUsageTooltip = computed(() => {
    const max = contextMax.value
    if (max == null) return null
    const provider = store.getSessionProvider(props.sessionId)
    const helpers = provider ? getProviderHelpers(provider) : null
    const label = helpers?.getChoiceLabel('context_max', max) || `${Math.round(max / 1000)}K`
    return `Context window usage (${label} max)`
})
// Same thresholds and indicator growth as the header and the session tree.
const contextUsageColor = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    if (pct > 70) return 'var(--wa-color-danger)'
    if (pct > 50) return 'var(--wa-color-warning)'
    return 'var(--wa-color-primary)'
})
const contextUsageIndicatorWidth = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    const multiplier = Math.min(1 + (pct / 80), 1.5)
    return `calc(var(--track-width) * ${multiplier.toFixed(2)})`
})

// ── Dates ───────────────────────────────────────────────────────────────────
function fmtDate(iso) {
    if (!iso) return null
    const ms = Date.parse(iso)
    return Number.isNaN(ms) ? null : formatDate(ms / 1000, { smart: true })
}

// The launch is the spawning tool_use's timestamp; the end is the persisted
// completion when there is one, else the agent's own last idle boundary.
const finishedAt = computed(() => entry.value?.stoppedAt ?? entry.value?.agentStoppedAt ?? null)
const startedLabel = computed(() => fmtDate(entry.value?.startedAt))
const finishedLabel = computed(() => (isRunning.value ? null : fmtDate(finishedAt.value)))
const durationLabel = computed(() => {
    const from = entry.value?.startedAt
    const to = finishedAt.value
    if (!from || !to || isRunning.value) return null
    const sec = (Date.parse(to) - Date.parse(from)) / 1000
    return sec > 0 ? formatDuration(sec) : null
})

const expanded = ref(true)
</script>

<template>
    <div class="onode" :class="{ 'has-children': hasChildren, 'is-expanded': expanded }">
        <div class="onode-row">
            <div class="onode-rail">
                <button
                    v-if="hasChildren"
                    type="button"
                    class="onode-chevron"
                    :aria-label="expanded ? 'Collapse' : 'Expand'"
                    @click="expanded = !expanded"
                >
                    <wa-icon :name="expanded ? 'chevron-down' : 'chevron-right'"></wa-icon>
                </button>
            </div>
            <div class="onode-body">
                <div class="onode-head">
                    <router-link :to="agentRoute" class="orch-title orch-title-link">{{ label }}</router-link>
                    <wa-icon
                        :name="status.icon"
                        :style="{ color: status.color }"
                        :title="status.label"
                        :label="status.label"
                        class="orch-status-icon"
                        :class="status.pulse ? 'robot-working' : null"
                    ></wa-icon>
                    <span v-if="entry?.isBackground" class="atree-badge" title="Launched in the background">background</span>
                    <span v-if="showCosts" class="orch-cost">
                        <CostDisplay :cost="ownCost" />
                        <span
                            v-if="hasChildren"
                            class="orch-cost-sub"
                            title="Cumulative cost including spawned agents"
                        >
                            Σ <CostDisplay :cost="cumulativeCost" />
                        </span>
                    </span>
                </div>
                <div v-if="startedLabel" class="onode-dates">
                    Started {{ startedLabel }}<template v-if="finishedLabel"> · Finished {{ finishedLabel }}</template><template v-if="durationLabel"> · <wa-icon auto-width name="clock" variant="regular"></wa-icon> {{ durationLabel }}</template><template v-if="turnsLabel"> · <wa-icon auto-width name="comment" variant="regular"></wa-icon> {{ turnsLabel }}</template><template v-if="contextUsagePercentage != null"><wa-progress-ring
                        :id="`atree-context-${node.id}`"
                        class="onode-context-ring"
                        :value="Math.min(contextUsagePercentage, 100)"
                        :style="{
                            '--indicator-color': contextUsageColor,
                            '--indicator-width': contextUsageIndicatorWidth,
                        }"
                    ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring><AppTooltip :for="`atree-context-${node.id}`">{{ contextUsageTooltip }}</AppTooltip></template>
                </div>
            </div>
        </div>

        <div v-if="hasChildren && expanded" class="onode-kids">
            <AgentTreeNode
                v-for="child in node.children"
                :key="child.id"
                :node="child"
                :session-id="sessionId"
                :project-id="projectId"
            />
        </div>
    </div>
</template>

<style scoped src="./treeNode.css"></style>

<style scoped>
.atree-badge {
    font-size: var(--wa-font-size-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--wa-color-text-quiet);
    border: 1px solid var(--wa-color-neutral-border-normal);
    border-radius: var(--wa-border-radius-s);
    padding: 0 var(--wa-space-2xs);
    align-self: center;
}
</style>
