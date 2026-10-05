<script setup>
// One subagent card of the Orchestration tree, rendered recursively. Same card as OrchestrationNode
// (treeNode.css): title + working robot, the model the subagent last used, time bar, dates/duration/turns/
// context with the cost at the right. What an agent has no equivalent of — settings, project, annotations —
// is simply absent. A stopped subagent shows no icon; there is no ``background`` badge.
//
// The tree comes from the agent-link cache (``buildAgentTree``), kept live by the WS ``agent_link_created`` /
// ``agent_stopped`` events, so this view needs no polling of its own. Numbers come from the agent's own
// ``Session`` row when it is loaded (live), else from the ``metrics`` block / ``model`` of the ``/subagents/``
// snapshot — historical agents have no row in the store.
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import AppTooltip from '../ui/AppTooltip.vue'
import CostDisplay from '../ui/CostDisplay.vue'
import ProviderIcon from '../ui/ProviderIcon.vue'
import OrchestrationTimeBar from './OrchestrationTimeBar.vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { getProviderHelpers } from '../../providers'
import { getAgentDisplay } from '../../utils/agentLabel'
import { agentCost, agentSubtreeCost } from '../../utils/agentTreeMetrics'
import { formatDate, formatDuration } from '../../utils/date'
import { agentModelLabel, BUCKET_BORDER_COLORS, flattenTree } from '../../utils/orchestrationView'
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
    // { geometry: { [id]: { left, width, live } } } from computeTimeline.
    timeline: { type: Object, default: () => ({ geometry: {} }) },
})

const entry = computed(() => props.node.entry)
const hasChildren = computed(() => (props.node.children?.length ?? 0) > 0)
const descendantCount = computed(() => flattenTree(props.node).length - 1)

// The name the launcher gave this agent (see utils/agentLabel.js); ``Subagent "<short id>"`` when nothing
// named it — this tab says "subagent" throughout, to tell these apart from the sessions in the other tree.
const label = computed(() => {
    const { name, isFallback } = getAgentDisplay(props.node.id, store)
    return isFallback ? `Subagent "${name}"` : name
})

// A running agent carries a process state — real, or the synthetic one the agent-link cache maintains.
const isRunning = computed(() => !!store.getProcessState(props.node.id))
const borderColor = computed(() => BUCKET_BORDER_COLORS[isRunning.value ? 'working' : 'stopped'])

// Opening an agent goes through the regular subagent route (the one the in-chat "View Agent" button uses).
const agentRoute = computed(() => sessionRouteLocation(
    { id: props.sessionId, project_id: props.projectId },
    route,
    { subagentId: props.node.id },
))

// ── Model (spec 5.4): the subagent's own last used model ─────────────────────
const provider = computed(() => store.getSessionProvider(props.sessionId))
const modelLabel = computed(() => agentModelLabel(store.getSession(props.node.id)?.model ?? entry.value?.model))

// ── Numbers ─────────────────────────────────────────────────────────────────
const ownCost = computed(() => agentCost(store, props.node.id, entry.value?.metrics))
const cumulativeCost = computed(() => agentSubtreeCost(store, props.node))

const turnsLabel = computed(() => {
    const row = store.getSession(props.node.id)
    return row?.user_message_count ?? entry.value?.metrics?.userMessageCount ?? null
})

// Context window: an agent has no settings of its own — it runs inside its launcher's session, so the
// window is the root session's effective one. Usage is the agent's own.
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
    const helpers = provider.value ? getProviderHelpers(provider.value) : null
    const label = helpers?.getChoiceLabel('context_max', max) || `${Math.round(max / 1000)}K`
    return `Context window usage (${label} max)`
})
const contextUsageColor = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    if (pct > 70) return 'var(--wa-color-danger)'
    if (pct > 50) return 'var(--wa-color-warning)'
    return 'var(--glow-context-ring)'
})

// ── Dates ───────────────────────────────────────────────────────────────────
function fmtDate(iso) {
    if (!iso) return null
    const ms = Date.parse(iso)
    return Number.isNaN(ms) ? null : formatDate(ms / 1000, { smart: true })
}

// The launch is the spawning tool_use's timestamp; the end is the persisted completion when there is one,
// else the agent's own last idle boundary. A running agent ends "now" and shows no duration.
const finishedAt = computed(() => entry.value?.stoppedAt ?? entry.value?.agentStoppedAt ?? null)
const startLabel = computed(() => fmtDate(entry.value?.startedAt))
const endLabel = computed(() => (isRunning.value ? 'now' : fmtDate(finishedAt.value)))
const durationLabel = computed(() => {
    const from = entry.value?.startedAt
    const to = finishedAt.value
    if (!from || !to || isRunning.value) return null
    const sec = (Date.parse(to) - Date.parse(from)) / 1000
    return sec > 0 ? formatDuration(sec) : null
})

const geometry = computed(() => props.timeline?.geometry?.[props.node.id] ?? null)
const barTitle = computed(() => (startLabel.value
    ? (endLabel.value ? `${startLabel.value} → ${endLabel.value}` : startLabel.value)
    : null))

const expanded = ref(true)
</script>

<template>
    <div class="onode">
        <div class="ocard" :style="{ '--ocard-border': borderColor }">
            <div class="ocard-head">
                <span class="ocard-title">
                    <router-link :to="agentRoute" class="orch-title-link">{{ label }}</router-link>
                    <wa-icon
                        v-if="isRunning"
                        name="robot"
                        title="Working"
                        label="Working"
                        class="orch-status-icon robot-working"
                        style="color: var(--wa-color-blue-60)"
                    ></wa-icon>
                </span>
                <button
                    v-if="hasChildren"
                    type="button"
                    class="ocard-toggle"
                    :aria-expanded="expanded"
                    :aria-label="expanded ? 'Collapse' : 'Expand'"
                    @click="expanded = !expanded"
                >
                    <span v-if="!expanded">{{ descendantCount }}</span>
                    <wa-icon :name="expanded ? 'chevron-down' : 'chevron-right'"></wa-icon>
                </button>
            </div>

            <div v-if="modelLabel" class="ocard-settings" title="Last used model">
                <span class="ocard-model">
                    <ProviderIcon :provider="provider" />
                    {{ modelLabel }}
                </span>
            </div>

            <OrchestrationTimeBar v-if="geometry" :geometry="geometry" :title="barTitle" />

            <div class="ocard-facts">
                <span class="ocard-facts-main">
                    <span v-if="startLabel" class="ocard-dates">
                        <wa-icon auto-width name="calendar" variant="regular"></wa-icon>
                        {{ startLabel }}
                        <template v-if="endLabel">
                            <wa-icon auto-width name="arrow-right" class="ocard-arrow"></wa-icon>
                            {{ endLabel }}
                        </template>
                    </span>
                    <span v-if="startLabel && durationLabel">
                        <wa-icon auto-width name="clock" variant="regular"></wa-icon> {{ durationLabel }}
                    </span>
                    <span v-if="turnsLabel">
                        <wa-icon auto-width name="comment" variant="regular"></wa-icon> {{ turnsLabel }}
                    </span>
                    <span v-if="contextUsagePercentage != null">
                        <wa-progress-ring
                            :id="`atree-context-${node.id}`"
                            class="onode-context-ring"
                            :value="Math.min(contextUsagePercentage, 100)"
                            :style="{ '--indicator-color': contextUsageColor }"
                        ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>
                        <AppTooltip :for="`atree-context-${node.id}`">{{ contextUsageTooltip }}</AppTooltip>
                    </span>
                </span>
                <span v-if="showCosts" class="orch-cost">
                    <CostDisplay :cost="ownCost" />
                    <span v-if="hasChildren" class="orch-cost-sub" title="Cumulative cost including spawned agents">
                        Σ <CostDisplay :cost="cumulativeCost" />
                    </span>
                </span>
            </div>
        </div>

        <div v-if="hasChildren && expanded" class="onode-kids">
            <AgentTreeNode
                v-for="child in node.children"
                :key="child.id"
                :node="child"
                :session-id="sessionId"
                :project-id="projectId"
                :timeline="timeline"
            />
        </div>
    </div>
</template>

<style scoped src="./treeNode.css"></style>

<style scoped>
.ocard-model {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-style: italic;
    text-transform: capitalize;
}
</style>
