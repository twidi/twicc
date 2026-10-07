<script setup>
// One session card of the Orchestration tree, rendered recursively. The card: title (link) + process-state
// icon, agent-settings summary + project, time bar, dates/duration/turns/context with the cost at the right,
// annotations. The left border carries the state bucket. Hidden sessions are dimmed and not linked.
// Non-hidden titles link to the session; the layout is shared with AgentTreeNode through treeNode.css.
// Self-references for recursion via filename.
import { ref, inject } from 'vue'
import { useRoute } from 'vue-router'
import CostDisplay from '../ui/CostDisplay.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import AgentSettingsSummaryView from '../message/AgentSettingsSummaryView.vue'
import ProjectBadge from '../project/ProjectBadge.vue'
import OrchestrationAnnotations from './OrchestrationAnnotations.vue'
import OrchestrationTimeBar from './OrchestrationTimeBar.vue'
import { getProviderHelpers, getProviderStore } from '../../providers'
import { formatDate, formatDuration } from '../../utils/date'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import { backgroundShellsRunningPhrase, userTurnBackgroundShellCount } from '../../utils/backgroundWork'
import { BUCKET_BORDER_COLORS, bucketOfProcessState, flattenTree } from '../../utils/orchestrationView'
import { useSettingsStore } from '../../stores/settings'
import { SESSION_TREE_CONTEXT } from './orchestrationKeys.js'
import { useVisibleComputed } from './useVisibleComputed.js'

const settingsStore = useSettingsStore()
const route = useRoute()

const props = defineProps({
    // Id-only tree node: { id, children: [...] }
    node: { type: Object, required: true },
    // Id of the session the Orchestration tab belongs to (outlined).
    currentSessionId: { type: String, default: null },
})
// Each computed returns only this card's value. Unchanged values prevent an
// unrelated map update from rendering the card. Stopped durations do not read now.
const { active, nodesById, timeline, now } = inject(SESSION_TREE_CONTEXT)
const visibleComputed = useVisibleComputed(active)
const showCosts = visibleComputed(() => settingsStore.areCostsShown)

// Process-state vocabulary from the topology payload. ``dead`` shows NO icon (a stopped session is the
// common, expected state). The label stays as the tooltip and accessible name; no text is drawn.
const PROCESS_STATUS = {
    starting:            { label: 'Starting',        icon: 'hourglass-start', color: 'var(--wa-color-warning-60)' },
    assistant_turn:      { label: 'Assistant turn',  icon: 'robot',           color: 'var(--wa-color-blue-60)',    pulse: 'work' },
    awaiting_user_input: { label: 'Awaiting input',  icon: 'hand',            color: 'var(--wa-color-warning-60)', pulse: 'pending' },
    user_turn:           { label: 'User turn',       icon: 'check',           color: 'var(--wa-color-success-60)' },
    dead:                { label: 'Stopped',         icon: null,              color: 'var(--wa-color-neutral-50)' },
}

const nodeData = visibleComputed(() => nodesById.value[props.node.id] ?? null)
const isCurrent = visibleComputed(() => props.node.id === props.currentSessionId)
const isHidden = visibleComputed(() => nodeData.value?.session?.hidden === true)

// Preserve the current frame (all-projects vs single-project prefix + project filter + workspace).
const sessionRoute = visibleComputed(() => sessionRouteLocation(
    { id: props.node.id, project_id: nodeData.value?.session?.project_id },
    route,
))

const projectId = visibleComputed(() => nodeData.value?.session?.project_id ?? null)
const title = visibleComputed(() => {
    const t = nodeData.value?.session?.title
    return (t && t.trim()) ? t : props.node.id.slice(0, 8)
})
const provider = visibleComputed(() => nodeData.value?.session?.provider ?? null)

// The agent-settings summary: unchanged. The model's version is always shown (derive "family-version"
// from the RESOLVED model so a bare "opus" reads "Opus 4.7").
const summaryParts = visibleComputed(() => {
    const helpers = provider.value ? getProviderHelpers(provider.value) : null
    if (!helpers) return []
    const s = nodeData.value.session
    const pStore = provider.value ? getProviderStore(provider.value) : null
    const m = s.model
    const modelForSummary = (m && m.family && m.version)
        ? `${m.family}-${m.version}`
        : (s.selected_model ?? null)
    const state = {
        selected: {
            selected_model: modelForSummary,
            permission_mode: s.permission_mode ?? null,
            effort: s.effort ?? null,
            thinking_enabled: s.thinking_enabled ?? null,
            claude_in_chrome: s.claude_in_chrome ?? null,
            fast_mode: s.fast_mode ?? null,
            context_max: s.context_max ?? null,
        },
        defaults: {
            selected_model: pStore?.defaultModel,
            permission_mode: pStore?.defaultPermissionMode,
            effort: pStore?.defaultEffort,
            thinking_enabled: pStore?.defaultThinking,
            claude_in_chrome: pStore?.defaultClaudeInChrome,
            fast_mode: pStore?.defaultFastMode,
            context_max: pStore?.defaultContextMax,
        },
    }
    return helpers.getSummaryParts(state) ?? []
})

const ownCost = visibleComputed(() => nodeData.value?.session?.total_cost ?? null)
const cumulativeCost = visibleComputed(() => nodeData.value?.subtree_total_cost ?? null)
const hasChildren = visibleComputed(() => (props.node.children?.length ?? 0) > 0)
const descendantCount = visibleComputed(() => flattenTree(props.node).length - 1)

const annotations = visibleComputed(() => nodeData.value?.session?.annotations ?? null)
const hasAnnotations = visibleComputed(() => {
    const a = annotations.value
    return !!a && typeof a === 'object' && Object.keys(a).length > 0
})

const processState = visibleComputed(() => nodeData.value?.process?.state ?? 'dead')
const bucket = visibleComputed(() => bucketOfProcessState(processState.value))
const isWorking = visibleComputed(() => bucket.value === 'working')
const borderColor = visibleComputed(() => BUCKET_BORDER_COLORS[bucket.value])

const status = visibleComputed(() => {
    const base = PROCESS_STATUS[processState.value] ?? PROCESS_STATUS.dead
    // A finished turn with a shell the agent left running: terminal icon, same green, breathing.
    const shells = userTurnBackgroundShellCount(nodeData.value?.process)
    if (shells) return { ...base, icon: 'terminal', pulse: 'shell', label: `${base.label} — ${backgroundShellsRunningPhrase(shells)}` }
    return base
})

// ── Dates (spec 5.1) ────────────────────────────────────────────────────────
function fmtDate(iso) {
    if (!iso) return null
    const ms = Date.parse(iso)
    return Number.isNaN(ms) ? null : formatDate(ms / 1000, { smart: true })
}
const startLabel = visibleComputed(() => fmtDate(nodeData.value?.session?.created_at))
// A working node ends "now"; otherwise the last assistant message synced is the "finished" proxy.
const endLabel = visibleComputed(() => (isWorking.value ? 'now' : fmtDate(nodeData.value?.session?.last_new_content_at)))
// Working nodes use the panel's shared clock; only positive spans have a duration.
const durationLabel = visibleComputed(() => {
    const c = nodeData.value?.session?.created_at
    const f = isWorking.value ? now.value : Date.parse(nodeData.value?.session?.last_new_content_at)
    if (!c) return null
    const sec = (f - Date.parse(c)) / 1000
    return sec > 0 ? formatDuration(sec) : null
})
const turnsLabel = visibleComputed(() => nodeData.value?.session?.user_message_count ?? null)

// ── Context window ring: same data and rules as the SessionHeader. ───────────
const providerHelpers = visibleComputed(() => (provider.value ? getProviderHelpers(provider.value) : null))
const contextMax = visibleComputed(() => {
    const s = nodeData.value?.session
    const helpers = providerHelpers.value
    if (!s || !helpers) return null
    return helpers.getEffectiveContextMax(s)
})
const contextUsagePercentage = visibleComputed(() => {
    const usage = nodeData.value?.session?.context_usage
    const max = contextMax.value
    if (usage == null || !max) return null
    return Math.round((usage / max) * 100)
})
const contextUsageTooltip = visibleComputed(() => {
    const max = contextMax.value
    if (max == null) return null
    const label = providerHelpers.value?.getChoiceLabel('context_max', max) || `${Math.round(max / 1000)}K`
    return `Context window usage (${label} max)`
})
const contextUsageColor = visibleComputed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    if (pct > 70) return 'var(--wa-color-danger)'
    if (pct > 50) return 'var(--wa-color-warning)'
    return 'var(--glow-context-ring)'
})

const geometry = visibleComputed(() => timeline.value?.geometry?.[props.node.id] ?? null)
const barTitle = visibleComputed(() => (startLabel.value
    ? (endLabel.value ? `${startLabel.value} → ${endLabel.value}` : startLabel.value)
    : null))

// Expand/collapse this node's children (default expanded). Local: it changes nothing else on the page.
const expanded = ref(true)
</script>

<template>
    <div class="onode">
        <div
            class="ocard"
            :class="{ 'is-current': isCurrent, 'is-hidden': isHidden }"
            :style="{ '--ocard-border': borderColor }"
        >
            <div class="ocard-head">
                <span class="ocard-title">
                    <router-link v-if="!isHidden" :to="sessionRoute" class="orch-title-link ocard-title-text" :title="title">{{ title }}</router-link>
                    <span v-else class="ocard-title-text" :title="title">{{ title }}</span>
                    <wa-icon
                        v-if="isHidden"
                        name="eye-slash"
                        label="Hidden session"
                        title="Hidden session"
                        class="orch-hidden-icon"
                    ></wa-icon>
                    <wa-icon
                        v-if="status.icon"
                        :name="status.icon"
                        :style="{ color: status.color }"
                        :title="status.label"
                        :label="status.label"
                        class="orch-status-icon"
                        :class="status.pulse === 'work' ? 'robot-working' : (status.pulse ? `orch-status-icon--pulse-${status.pulse}` : null)"
                    ></wa-icon>
                    <span v-if="isCurrent" class="orch-current-tag">current</span>
                </span>
                <button
                    v-if="hasChildren"
                    type="button"
                    class="ocard-toggle"
                    :aria-expanded="expanded"
                    :aria-label="expanded ? 'Collapse' : 'Expand'"
                    @click="expanded = !expanded"
                >
                    <span>{{ descendantCount }}</span>
                    <wa-icon :name="expanded ? 'chevron-down' : 'chevron-right'"></wa-icon>
                </button>
            </div>

            <div class="ocard-settings">
                <span class="ocard-settings-main">
                    <AgentSettingsSummaryView :provider="provider" :parts="summaryParts" :mark-forced="false" />
                </span>
                <span v-if="projectId" class="project-badge-slot">
                    <ProjectBadge :project-id="projectId" :use-directory-for-unnamed="true" />
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
                            :id="`orch-context-${node.id}`"
                            class="onode-context-ring"
                            :value="Math.min(contextUsagePercentage, 100)"
                            :style="{ '--indicator-color': contextUsageColor }"
                        ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring>
                        <AppTooltip :for="`orch-context-${node.id}`">{{ contextUsageTooltip }}</AppTooltip>
                    </span>
                </span>
                <span v-if="showCosts" class="orch-cost">
                    <CostDisplay :cost="ownCost" />
                    <span v-if="hasChildren" class="orch-cost-sub" title="Cumulative cost including spawned children">
                        Σ <CostDisplay :cost="cumulativeCost" />
                    </span>
                </span>
            </div>

            <OrchestrationAnnotations v-if="hasAnnotations" :annotations="annotations" />
        </div>

        <div v-if="hasChildren && expanded" class="onode-kids">
            <OrchestrationNode
                v-for="child in node.children"
                :key="child.id"
                :node="child"
                :current-session-id="currentSessionId"
            />
        </div>
    </div>
</template>

<style scoped src="./treeNode.css"></style>
