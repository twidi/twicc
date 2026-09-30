<script setup>
// Footer bar surfacing the session's current goal (the last entry of the
// ``Session.goals`` lifecycle history — see the backend ``providers/goals.py``
// docstring for the shape). Sits at the very top of the session-footer stack,
// above the pending-request form / hybrid terminal / composer.
//
// Unlike its footer siblings the resting state is the single-line collapsed
// bar; opening it (4th accordion panel in SessionItemsList) reveals a
// read-only panel: the current goal's statement, its earlier statements when
// it was restated (collapsed under a wa-details), and the session's previous
// (closed) goals (one wa-details each). The open panel also carries the goal
// actions: a stop button (sends ``/goal clear`` as if the user typed it) and a
// mini-composer (textarea + send) that prefixes its text with ``/goal `` to
// update the goal — or set a new one once the current goal is closed — without
// going through the message input. A closed goal (completed or manually
// cleared) can be dismissed with the cross, which hides the bar until a new
// goal opens.
import { computed, nextTick, ref, useId, watch } from 'vue'
import { useDataStore } from '../../stores/data'
import { sendWsMessage } from '../../composables/useWebSocket'
import { toast } from '../../composables/useToast'
import { useFooterBlockMotion } from '../../composables/useFooterMotion.js'
import { generateUUID } from '../../utils/crypto'
import { formatFullDateTime, formatRelative } from '../../utils/date'
import AppTooltip from '../ui/AppTooltip.vue'
import MarkdownContent from '../ui/MarkdownContent.vue'
import CollapsedBar from './CollapsedBar.vue'

const props = defineProps({
    sessionId: { type: String, required: true },
    projectId: { type: String, required: true },
    // Mirrors the composer's sending lock: while an answerable pending request
    // is up, answering it comes first — the goal actions are disabled too.
    sendingLocked: { type: Boolean, default: false },
})

// `request-open` asks the footer accordion to expand this panel (reducing the
// others); `request-collapse` is the minimize button (back to the composer).
const emit = defineEmits(['request-open', 'request-collapse'])

const store = useDataStore()

// Current goal — the parent v-ifs this component on the same getter, so it is
// never rendered without one; the guards below only cover the transient frame
// where the goal is dismissed/superseded before the parent reacts.
const goal = computed(() => store.getSessionCurrentGoal(props.sessionId))

// The session's earlier (closed) goals, newest first for the open panel.
// A leftover ``dismissed`` flag only ever hides the *bar*; a dismissed goal
// still shows in this history once a newer goal exists.
const previousGoals = computed(() => {
    const goals = store.getSession(props.sessionId)?.goals
    return goals?.length > 1 ? goals.slice(0, -1).reverse() : []
})

// ── Open / collapsed / maximized state (accordion-driven) ────────────────────
// Collapsed is the resting state — the inverse of the other footer panels.
// 'maximized' fills the whole session area (window-controls style, like the
// pending-request form); the maximize button flips between it and 'open'.
const viewState = ref('collapsed') // 'collapsed' | 'open' | 'maximized'
const isOpen = computed(() => viewState.value !== 'collapsed')
const isMaximized = computed(() => viewState.value === 'maximized')

// Accordion setter: only lifts the panel out of its collapsed bar — re-opening
// an already-maximized panel must not downgrade it to 'open' (mirrors the
// pending form's restoreIfMinimized).
function open() {
    if (!isOpen.value) viewState.value = 'open'
}

function collapse() {
    viewState.value = 'collapsed'
}

function toggleMaximized() {
    viewState.value = isMaximized.value ? 'open' : 'maximized'
}

// Footer motion (visual refresh step 7d): the single root animates its height on open /
// collapse, the block its inset on maximize / restore.
const wrapperRef = ref(null)
const blockRef = ref(null)
const footerShape = computed(() => isOpen.value ? 'open' : 'collapsed')
useFooterBlockMotion({
    wrapperRef,
    blockRef,
    shape: footerShape,
    maximized: isMaximized,
})

// ── Focus on (accordion-driven) open ─────────────────────────────────────────
// Order-independent like the other footer panels: if still collapsed when
// asked, defer until the accordion opens the panel. The panel's primary
// control is the update mini-composer at the bottom.
const bodyRef = ref(null)
let wantsFocus = false
function focusBodyNow() {
    nextTick(() => updateTextareaRef.value?.focus())
}
function requestFocus() {
    if (isOpen.value) focusBodyNow()
    else wantsFocus = true
}
watch(isOpen, (openNow) => {
    if (openNow && wantsFocus) {
        wantsFocus = false
        focusBodyNow()
    }
})

defineExpose({ open, collapse, requestFocus })

// ── Per-entry helpers (shared by the current goal and the history) ──────────
// Normalised display state: a manual /goal clear wins (the reducer only sets
// `cleared` on a goal that never completed), then completion, else active.
function entryStatusLabel(entry) {
    return entry?.cleared ? 'Stopped' : entry?.state === 'completed' ? 'Completed' : 'Active'
}
function entryStatusVariant(entry) {
    return entry?.cleared ? 'neutral' : entry?.state === 'completed' ? 'success' : 'brand'
}
// Successive full statements of a goal; the current one is the last.
function lastObjectiveOf(entry) {
    return entry?.objectives?.at(-1) ?? ''
}
// Earlier statements, newest first (read top-down as "how we got here").
function earlierObjectivesOf(entry) {
    return (entry?.objectives ?? []).slice(0, -1).reverse()
}
function firstLineOf(text) {
    const line = (text ?? '').split('\n').find((l) => l.trim())
    return line ? line.trim() : ''
}
function relativeUpdatedAt(entry) {
    return entry?.updated_at ? formatRelative(Date.parse(entry.updated_at)) : null
}

// ── Current goal state ───────────────────────────────────────────────────────
const isClosed = computed(() => !!goal.value?.cleared || goal.value?.state === 'completed')
const statusLabel = computed(() => entryStatusLabel(goal.value))
const statusVariant = computed(() => entryStatusVariant(goal.value))

// Provider-native nuance, shown only when it says more than the normalised
// state: Claude's met/unmet and Codex's active/complete map 1:1 onto it, but
// Codex's paused / blocked / usage_limited / budget_limited add real signal.
const TRIVIAL_RAW_STATES = new Set(['met', 'unmet', 'active', 'complete'])
const rawStateNuance = computed(() => {
    const raw = goal.value?.raw_state
    return raw && !TRIVIAL_RAW_STATES.has(raw) ? raw.replaceAll('_', ' ') : null
})

const currentObjective = computed(() => lastObjectiveOf(goal.value))
const earlierObjectives = computed(() => earlierObjectivesOf(goal.value))

// Single-line bar label: "Goal: " followed by the first non-empty line of the
// current statement, so the bar always names what it is at a glance.
const barLabel = computed(() => {
    const line = firstLineOf(currentObjective.value)
    return line ? `Goal: ${line}` : 'Goal'
})

// ── Dates (open header) ──────────────────────────────────────────────────────
const updatedAtMs = computed(() => (goal.value?.updated_at ? Date.parse(goal.value.updated_at) : null))
const createdAtMs = computed(() => (goal.value?.created_at ? Date.parse(goal.value.created_at) : null))
const updatedAgo = computed(() => (updatedAtMs.value ? formatRelative(updatedAtMs.value) : null))
const datesTooltip = computed(() => {
    const parts = []
    if (createdAtMs.value) parts.push(`Set ${formatFullDateTime(createdAtMs.value)}`)
    if (updatedAtMs.value) parts.push(`Updated ${formatFullDateTime(updatedAtMs.value)}`)
    return parts.join(' — ')
})

// ── Dismiss ──────────────────────────────────────────────────────────────────
// Only offered on a closed goal: hiding the bar of a goal the agent is still
// working toward would be misleading (stop/clear actions will come later).
async function dismiss() {
    const createdAt = goal.value?.created_at
    if (!createdAt) return
    try {
        await store.dismissSessionGoal(props.projectId, props.sessionId, createdAt)
    } catch (error) {
        toast.error(error.message || 'Failed to dismiss the goal')
    }
}

// ── Goal actions (stop / update via the normal send path) ────────────────────
// Both actions send a real ``/goal …`` slash command through the same WS
// ``send_message`` frame the composer uses (same shape as the ApiError resend):
// the command shows as a user message in the transcript and each provider's
// existing /goal handling does the rest (Claude CLI slash command — queued when
// the agent is busy; Codex handled by TwiCC's send path).
function sendGoalCommand(text) {
    const session = store.getSession(props.sessionId)
    const requestId = generateUUID()
    const payload = {
        type: 'send_message',
        session_id: props.sessionId,
        project_id: props.projectId,
        provider: session?.provider,
        text,
        permission_mode: session?.permission_mode ?? null,
        selected_model: session?.selected_model ?? null,
        effort: session?.effort ?? null,
        thinking_enabled: session?.thinking_enabled ?? null,
        claude_in_chrome: session?.claude_in_chrome ?? null,
        fast_mode: session?.fast_mode ?? null,
        context_max: session?.context_max ?? null,
        request_id: requestId,
    }
    if (!sendWsMessage(payload)) {
        toast.error('Not connected — please retry in a moment')
        return false
    }
    // Optimistic user bubble + failure tracking, like any composer send.
    store.registerOutgoingSend(props.sessionId, props.projectId, requestId, {
        text,
        medias: [],
        images: [],
        documents: [],
    })
    return true
}

// Stop the active goal: exactly what typing ``/goal clear`` would do.
function stopGoal() {
    if (props.sendingLocked) return
    sendGoalCommand('/goal clear')
}

// Mini-composer state. Same submit shortcut as the main composer (Cmd/Ctrl+Enter).
const updateText = ref('')
const updateTextareaRef = ref(null)
const canSubmitUpdate = computed(() => !props.sendingLocked && !!updateText.value.trim())

function submitUpdate() {
    const text = updateText.value.trim()
    if (!text || props.sendingLocked) return
    if (!sendGoalCommand(`/goal ${text}`)) return
    updateText.value = ''
    // Force-clear the Web Component too: Vue's :value.prop dedup can skip
    // re-pushing '' (same trap as the main composer).
    if (updateTextareaRef.value) updateTextareaRef.value.value = ''
}

function onUpdateKeydown(event) {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault()
        submitUpdate()
    }
}

// Unique IDs for tooltip anchoring
const minimizeToggleId = useId()
const maximizeToggleId = useId()
const dismissBarId = useId()
const dismissHeaderId = useId()
const stopGoalId = useId()
const submitUpdateId = useId()
const datesId = useId()
</script>

<template>
    <div v-if="goal" ref="wrapperRef" class="footer-block">
    <!-- Guarded together with the divider: during the transient frame where the
         goal vanishes before the parent unmounts this component, neither should
         linger. The single root is the footer motion's wrapper (its height animates). -->
    <wa-divider></wa-divider>
    <div ref="blockRef" class="goal-block" :class="{ open: isOpen, maximized: isMaximized }">
        <!-- Collapsed (resting) state: the same single-line bar as the other
             reduced footer panels, tinted by the goal state. -->
        <CollapsedBar
            v-if="!isOpen"
            icon="bullseye"
            :label="barLabel"
            :variant="statusVariant"
            expand-tooltip="Show the goal"
            @expand="emit('request-open')"
        >
            <template #trailing>
                <span class="goal-status-badge" :class="`goal-status-badge--${statusVariant}`">{{ statusLabel }}</span>
                <span v-if="rawStateNuance" class="goal-status-badge goal-status-badge--nuance">{{ rawStateNuance }}</span>
                <wa-button
                    v-if="isClosed"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="goal-dismiss-btn"
                    :id="dismissBarId"
                    @click.stop="dismiss"
                >
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
                <AppTooltip v-if="isClosed" :for="dismissBarId">Dismiss this goal</AppTooltip>
            </template>
        </CollapsedBar>
        <!-- Open state: header + read-only panel. -->
        <template v-else>
            <div class="goal-header" :class="`goal-header--${statusVariant}`">
                <wa-icon name="bullseye" class="goal-header-icon"></wa-icon>
                <span class="goal-header-title">Goal</span>
                <span class="goal-status-badge" :class="`goal-status-badge--${statusVariant}`">{{ statusLabel }}</span>
                <span v-if="rawStateNuance" class="goal-status-badge goal-status-badge--nuance">{{ rawStateNuance }}</span>
                <span v-if="updatedAgo" class="goal-dates" :id="datesId">{{ updatedAgo }}</span>
                <AppTooltip v-if="updatedAgo && datesTooltip" :for="datesId">{{ datesTooltip }}</AppTooltip>
                <!-- Same look as the session header's stop-process button, so
                     "stop the goal" reads instantly as a stop action. -->
                <wa-button
                    v-if="!isClosed"
                    variant="danger"
                    appearance="filled"
                    size="small"
                    :id="stopGoalId"
                    :disabled="sendingLocked"
                    @click="stopGoal"
                >
                    <wa-icon name="ban" label="Stop the goal"></wa-icon>
                </wa-button>
                <AppTooltip v-if="!isClosed" :for="stopGoalId">Stop the goal (sends /goal clear)</AppTooltip>
                <wa-button
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="goal-header-btn"
                    :id="minimizeToggleId"
                    @click="emit('request-collapse')"
                >
                    <wa-icon name="window-minimize" variant="classic"></wa-icon>
                </wa-button>
                <AppTooltip :for="minimizeToggleId">Minimize</AppTooltip>
                <wa-button
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="goal-header-btn"
                    :id="maximizeToggleId"
                    @click="toggleMaximized"
                >
                    <wa-icon :name="isMaximized ? 'compress' : 'expand'" variant="classic"></wa-icon>
                </wa-button>
                <AppTooltip :for="maximizeToggleId">{{ isMaximized ? 'Restore' : 'Maximize' }}</AppTooltip>
                <wa-button
                    v-if="isClosed"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="goal-header-btn"
                    :id="dismissHeaderId"
                    @click="dismiss"
                >
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
                <AppTooltip v-if="isClosed" :for="dismissHeaderId">Dismiss this goal</AppTooltip>
            </div>
            <div ref="bodyRef" class="goal-body" tabindex="-1">
                <!-- Current statement of the current goal. -->
                <MarkdownContent :source="currentObjective" :show-toolbar="false" />
                <!-- The current goal was restated: earlier statements, newest
                     first, folded so the panel opens on just the live statement. -->
                <wa-details v-if="earlierObjectives.length" class="goal-details" icon-placement="start">
                    <span slot="summary" class="goal-details-summary">
                        Earlier statements ({{ earlierObjectives.length }})
                    </span>
                    <div v-for="(objective, index) in earlierObjectives" :key="index" class="goal-earlier-statement">
                        <MarkdownContent :source="objective" :show-toolbar="false" />
                    </div>
                </wa-details>
                <!-- Closed goals that preceded the current one, newest first. -->
                <template v-if="previousGoals.length">
                    <div class="goal-section-heading">Previous goals</div>
                    <wa-details
                        v-for="(entry, index) in previousGoals"
                        :key="entry.created_at ?? index"
                        class="goal-details"
                        icon-placement="start"
                    >
                        <span slot="summary" class="goal-details-summary goal-details-summary--goal">
                            <span class="goal-status-badge" :class="`goal-status-badge--${entryStatusVariant(entry)}`">{{ entryStatusLabel(entry) }}</span>
                            <span class="goal-details-summary-label">{{ firstLineOf(lastObjectiveOf(entry)) }}</span>
                            <span v-if="relativeUpdatedAt(entry)" class="goal-dates">{{ relativeUpdatedAt(entry) }}</span>
                        </span>
                        <MarkdownContent :source="lastObjectiveOf(entry)" :show-toolbar="false" />
                        <template v-if="earlierObjectivesOf(entry).length">
                            <div class="goal-section-heading goal-section-heading--nested">Earlier statements</div>
                            <div v-for="(objective, objIndex) in earlierObjectivesOf(entry)" :key="objIndex" class="goal-earlier-statement">
                                <MarkdownContent :source="objective" :show-toolbar="false" />
                            </div>
                        </template>
                    </wa-details>
                </template>
            </div>
            <!-- Mini-composer: updates the open goal (or sets a new one once it
                 is closed) by sending "/goal <text>" through the normal send
                 path — no round-trip through the message input. -->
            <form class="goal-actions" @submit.prevent="submitUpdate">
                <wa-textarea
                    ref="updateTextareaRef"
                    class="goal-update-input"
                    size="small"
                    rows="1"
                    resize="auto"
                    :placeholder="isClosed ? 'Set a new goal…' : 'Update the goal…'"
                    :value.prop="updateText"
                    @input="updateText = $event.target.value"
                    @keydown="onUpdateKeydown"
                ></wa-textarea>
                <wa-button
                    variant="brand"
                    appearance="filled"
                    size="small"
                    :id="submitUpdateId"
                    :disabled="!canSubmitUpdate"
                    @click="submitUpdate"
                >
                    <wa-icon name="paper-plane"></wa-icon>
                </wa-button>
                <AppTooltip :for="submitUpdateId">{{ isClosed ? 'Set a new goal' : 'Update the goal' }} (sends /goal …)</AppTooltip>
            </form>
        </template>
    </div>
    </div>
</template>

<style scoped>
/* The single root (footer motion, step 7d): flow-root keeps a child's margin inside, so an
   animated height never clips it. */
.footer-block {
    display: flow-root;
}

wa-divider {
    --width: var(--divider-size);
    --spacing: 0;
}

.goal-block {
    display: flex;
    flex-direction: column;
    background: var(--wa-color-surface-default);
    &.open {
        gap: var(--wa-space-s);
        padding: var(--wa-space-s);
        /* Same default cap as the pending-request form. */
        max-height: 50dvh;
    }
    &.maximized {
        max-height: unset;
        position: absolute;
        inset: 0;
        /* Same stacking as the maximized pending form: above the composer
           (positioned in the same footer), below the drag/drop overlay. */
        z-index: 2;
    }
}

.goal-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    font-weight: 600;
}
.goal-header--brand { color: var(--wa-color-brand-60); }
.goal-header--success { color: var(--wa-color-success-60); }
.goal-header--neutral { color: var(--wa-color-text-quiet); }

.goal-header-title {
    flex: 1;
}

.goal-dates {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-normal);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
}

.goal-header-btn {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.goal-dismiss-btn {
    flex-shrink: 0;
}

.goal-status-badge {
    display: inline-flex;
    align-items: center;
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    padding: 2px var(--wa-space-xs);
    border-radius: var(--wa-border-radius-pill);
    line-height: 1;
    white-space: nowrap;
}
.goal-status-badge--brand {
    background: var(--wa-color-brand-fill-loud);
    color: var(--wa-color-brand-on-loud);
}
.goal-status-badge--success {
    background: var(--wa-color-success-fill-loud);
    color: var(--wa-color-success-on-loud);
}
.goal-status-badge--neutral {
    background: var(--wa-color-neutral-fill-loud);
    color: var(--wa-color-neutral-on-loud);
}
.goal-status-badge--nuance {
    background: var(--wa-color-warning-fill-loud);
    color: var(--wa-color-warning-on-loud);
}

.goal-body {
    overflow-y: auto;
    min-height: 0;
    outline: none;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.goal-section-heading {
    margin-top: var(--wa-space-s);
    padding-top: var(--wa-space-s);
    border-top: 1px solid var(--wa-color-surface-border);
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-text-quiet);
}
/* Inside a previous goal's details: separates its earlier statements from its
   final statement, without the section-level top border. */
.goal-section-heading--nested {
    margin-top: var(--wa-space-xs);
    padding-top: 0;
    border-top: none;
}

.goal-details {
    font-size: var(--wa-font-size-s);
    &::part(content) {
        padding-top: 0;
        display: flex;
        flex-direction: column;
        gap: var(--wa-space-s);
    }
}

.goal-details-summary {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
.goal-details-summary--goal {
    display: flex;
    min-width: 0;
    align-items: center;
    gap: var(--wa-space-xs);
}
.goal-details-summary-label {
    flex: 1;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--wa-color-text-normal);
}

.goal-actions {
    display: flex;
    align-items: flex-end;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}
.goal-update-input {
    flex: 1;
    min-width: 0;
}

.goal-earlier-statement {
    opacity: 0.7;
}
.goal-earlier-statement + .goal-earlier-statement {
    padding-top: var(--wa-space-s);
    border-top: 1px dashed var(--wa-color-surface-border);
}
</style>
