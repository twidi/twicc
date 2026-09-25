<script setup>
// One node of the orchestration tree, rendered recursively as a custom tree
// (we dropped Web Awesome's <wa-tree>: its indent guides assume single-line
// items and start the vertical line *below* the node's content, which breaks
// with our multi-line blocks). Here the connectors are drawn ourselves:
//   - a vertical line starts at a parent's chevron centre and runs down to its
//     LAST child's elbow (not through the last child's own subtree),
//   - each child gets a horizontal elbow from that line to its content.
// The node's block (title + status, settings summary, cost, annotations) is
// unchanged from before. Non-hidden titles link to the session; hidden ones
// get a crossed-out eye and no link. Self-references for recursion via filename.
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import CostDisplay from '../ui/CostDisplay.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import JsonHumanView from '../json/JsonHumanView.vue'
import AgentSettingsSummaryView from '../message/AgentSettingsSummaryView.vue'
import ProjectBadge from '../project/ProjectBadge.vue'
import { getProviderHelpers, getProviderStore } from '../../providers'
import { formatDate, formatDuration } from '../../utils/date'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import { useSettingsStore } from '../../stores/settings'

const settingsStore = useSettingsStore()
const route = useRoute()

// Honour the global "Show costs" toggle, like every other cost display in the
// app (SessionHeader, ProjectCard, SessionListItem, …). The orchestration tree
// used to be the lone exception, leaking costs even when they were hidden.
const showCosts = computed(() => settingsStore.areCostsShown)

const props = defineProps({
    // Id-only tree node: { id, children: [...] }
    node: { type: Object, required: true },
    // Map of session id -> full topology node (session, process, metrics)
    nodesById: { type: Object, required: true },
    // Id of the session the Orchestration tab belongs to (highlighted).
    currentSessionId: { type: String, default: null },
})

// Process-state vocabulary from the topology payload (5 virtual states, cf.
// _process_state.project_virtual_state). ``dead`` here just means "no live
// process" — the common, expected state for a finished session — so it is
// neutral grey rather than the alarming red used elsewhere.
// ``pulse`` mirrors the live indicators used everywhere else: the robot is animated
// while the agent works (``work`` → the shared ``robot-working``) and the hand pulses
// while a request awaits the user (AggregatedProcessIndicator's ``pending-pulse``,
// 1.5s). Static states carry no ``pulse``.
const PROCESS_STATUS = {
    starting:            { label: 'Starting',        icon: 'hourglass-start',    color: 'var(--wa-color-warning-60)' },
    assistant_turn:      { label: 'Assistant turn',  icon: 'robot',              color: 'var(--wa-color-blue-60)',    pulse: 'work' },
    awaiting_user_input: { label: 'Awaiting input',  icon: 'hand',               color: 'var(--wa-color-warning-60)', pulse: 'pending' },
    user_turn:           { label: 'User turn',       icon: 'check',              color: 'var(--wa-color-success-60)' },
    dead:                { label: 'Stopped',         icon: 'circle-stop',        color: 'var(--wa-color-neutral-50)' },
}

const nodeData = computed(() => props.nodesById[props.node.id] ?? null)

const isCurrent = computed(() => props.node.id === props.currentSessionId)

const isHidden = computed(() => nodeData.value?.session?.hidden === true)

// Preserve the current frame (all-projects vs single-project prefix + current
// project filter + workspace); only the session id changes.
const sessionRoute = computed(() => sessionRouteLocation(
    { id: props.node.id, project_id: nodeData.value?.session?.project_id },
    route,
))

// Project the node's session was launched in. Rendered through the shared
// ProjectBadge (color dot + name); ``use-directory-for-unnamed`` shows the full
// directory path for projects that have no user-given name, instead of just the
// folder basename.
const projectId = computed(() => nodeData.value?.session?.project_id ?? null)

const title = computed(() => {
    const t = nodeData.value?.session?.title
    return (t && t.trim()) ? t : props.node.id.slice(0, 8)
})

const provider = computed(() => nodeData.value?.session?.provider ?? null)

const summaryParts = computed(() => {
    const helpers = provider.value ? getProviderHelpers(provider.value) : null
    if (!helpers) return []
    const s = nodeData.value.session
    const pStore = provider.value ? getProviderStore(provider.value) : null
    // Orchestration always shows the model's version — even when the setting is
    // a bare "latest" family (e.g. "opus", which the shared summary renders as
    // just "Opus"). Derive "family-version" from the session's RESOLVED model so
    // the label reads "Opus 4.7"; fall back to the raw setting when no resolved
    // model is known (e.g. a session that hasn't run yet).
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

const ownCost = computed(() => nodeData.value?.session?.total_cost ?? null)
const cumulativeCost = computed(() => nodeData.value?.subtree_total_cost ?? null)
const hasChildren = computed(() => (props.node.children?.length ?? 0) > 0)

const annotations = computed(() => nodeData.value?.session?.annotations ?? null)
const hasAnnotations = computed(() => {
    const a = annotations.value
    return !!a && typeof a === 'object' && Object.keys(a).length > 0
})

const status = computed(() => {
    const state = nodeData.value?.process?.state ?? 'dead'
    return PROCESS_STATUS[state] ?? PROCESS_STATUS.dead
})

// Lifecycle dates. ``created_at`` is the session's creation; ``last_new_content_at``
// (last assistant message synced) is the best available "finished working" proxy
// — see the field rationale in the topology view. ISO strings → seconds for
// formatDate.
function fmtDate(iso) {
    if (!iso) return null
    const ms = Date.parse(iso)
    return Number.isNaN(ms) ? null : formatDate(ms / 1000, { smart: true })
}
const createdLabel = computed(() => fmtDate(nodeData.value?.session?.created_at))
const finishedLabel = computed(() => fmtDate(nodeData.value?.session?.last_new_content_at))

// Wall-clock span between creation and last output (the two dates shown).
const durationLabel = computed(() => {
    const c = nodeData.value?.session?.created_at
    const f = nodeData.value?.session?.last_new_content_at
    if (!c || !f) return null
    const sec = (Date.parse(f) - Date.parse(c)) / 1000
    return sec > 0 ? formatDuration(sec) : null
})

// Number of message turns (user messages). Backed by Session.n, which defaults
// to 0; same field/icon the SessionHeader uses for its turns count. Rendered
// only when > 0 (a session that hasn't produced a turn yet shows nothing).
const turnsLabel = computed(() => nodeData.value?.session?.user_message_count ?? null)

// Context window usage ring — same data and rules as the SessionHeader. The
// provider helper reads only fields present in the topology payload
// (context_max, selected_model, context_usage) plus provider-store defaults,
// so it resolves the effective window from the serialized session alone,
// without the row being loaded in the data store.
const providerHelpers = computed(() => (provider.value ? getProviderHelpers(provider.value) : null))

const contextMax = computed(() => {
    const s = nodeData.value?.session
    const helpers = providerHelpers.value
    if (!s || !helpers) return null
    return helpers.getEffectiveContextMax(s)
})

const contextUsagePercentage = computed(() => {
    const usage = nodeData.value?.session?.context_usage
    const max = contextMax.value
    if (usage == null || !max) return null
    return Math.round((usage / max) * 100)
})

// Tooltip text — resolve the choice label through the session's own provider
// helpers (so Codex etc. render their own context_max catalogue), falling back
// to a rounded "XK" label.
const contextUsageTooltip = computed(() => {
    const max = contextMax.value
    if (max == null) return null
    const helpers = providerHelpers.value
    const label = helpers?.getChoiceLabel('context_max', max) || `${Math.round(max / 1000)}K`
    return `Context window usage (${label} max)`
})

// Indicator color by threshold, mirroring the header.
const contextUsageColor = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    if (pct > 70) return 'var(--wa-color-danger)'
    if (pct > 50) return 'var(--wa-color-warning)'
    return 'var(--wa-color-primary)'
})

// Indicator width multiplier (1x at 0%, up to 1.5x at 80%+), mirroring the header.
const contextUsageIndicatorWidth = computed(() => {
    const pct = contextUsagePercentage.value
    if (pct == null) return null
    const multiplier = Math.min(1 + (pct / 80), 1.5)
    return `calc(var(--track-width) * ${multiplier.toFixed(2)})`
})

// Expand/collapse this node's children (default expanded).
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
                    <router-link
                        v-if="!isHidden"
                        :to="sessionRoute"
                        class="orch-title orch-title-link"
                        :class="{ 'is-current': isCurrent }"
                    >{{ title }}</router-link>
                    <span
                        v-else
                        class="orch-title"
                        :class="{ 'is-current': isCurrent }"
                    >{{ title }}</span>
                    <wa-icon
                        v-if="isHidden"
                        name="eye-slash"
                        label="Hidden session"
                        title="Hidden session"
                        class="orch-hidden-icon"
                    ></wa-icon>
                    <wa-icon
                        :name="status.icon"
                        :style="{ color: status.color }"
                        :title="status.label"
                        :label="status.label"
                        class="orch-status-icon"
                        :class="status.pulse === 'work' ? 'robot-working' : (status.pulse ? `orch-status-icon--pulse-${status.pulse}` : null)"
                    ></wa-icon>
                    <span v-if="isCurrent" class="orch-current-badge">current</span>
                    <span v-if="showCosts" class="orch-cost">
                        <CostDisplay :cost="ownCost" />
                        <span
                            v-if="hasChildren"
                            class="orch-cost-sub"
                            title="Cumulative cost including spawned children"
                        >
                            Σ <CostDisplay :cost="cumulativeCost" />
                        </span>
                    </span>
                </div>
                <div class="onode-summary">
                    <AgentSettingsSummaryView :provider="provider" :parts="summaryParts" :mark-forced="false" />
                </div>
                <div v-if="projectId" class="onode-project">
                    <ProjectBadge :project-id="projectId" :use-directory-for-unnamed="true" />
                </div>
                <div v-if="createdLabel" class="onode-dates">
                    Created {{ createdLabel }}<template v-if="finishedLabel"> · Finished {{ finishedLabel }}</template><template v-if="durationLabel"> · <wa-icon auto-width name="clock" variant="regular"></wa-icon> {{ durationLabel }}</template><template v-if="turnsLabel"> · <wa-icon auto-width name="comment" variant="regular"></wa-icon> {{ turnsLabel }}</template><template v-if="contextUsagePercentage != null"><wa-progress-ring
                        :id="`orch-context-${node.id}`"
                        class="onode-context-ring"
                        :value="Math.min(contextUsagePercentage, 100)"
                        :style="{
                            '--indicator-color': contextUsageColor,
                            '--indicator-width': contextUsageIndicatorWidth,
                        }"
                    ><span class="wa-font-weight-bold">{{ contextUsagePercentage }}%</span></wa-progress-ring><AppTooltip :for="`orch-context-${node.id}`">{{ contextUsageTooltip }}</AppTooltip></template>
                </div>
                <div v-if="hasAnnotations" class="onode-annotations">
                    <JsonHumanView :value="annotations" />
                </div>
            </div>
        </div>

        <div v-if="hasChildren && expanded" class="onode-kids">
            <OrchestrationNode
                v-for="child in node.children"
                :key="child.id"
                :node="child"
                :nodes-by-id="nodesById"
                :current-session-id="currentSessionId"
            />
        </div>
    </div>
</template>

<style scoped src="./treeNode.css"></style>

<style scoped>
.orch-hidden-icon {
    flex-shrink: 0;
    align-self: center;
    color: var(--wa-color-text-quiet);
}

.orch-current-badge {
    font-size: var(--wa-font-size-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--wa-color-brand-60);
    border: 1px solid var(--wa-color-brand-60);
    border-radius: var(--wa-border-radius-s);
    padding: 0 var(--wa-space-2xs);
    align-self: center;
}

.onode-summary {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.onode-project {
    margin-top: var(--wa-space-3xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}


.onode-annotations {
    margin-top: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s);
}
</style>
