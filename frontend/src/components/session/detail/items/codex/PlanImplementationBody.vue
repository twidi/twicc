<script setup>
// PlanImplementationBody.vue (codex) — body sub-component for a
// ``planImplementation`` pending request (request_type ``ask_user_question``).
//
// TwiCC-owned post-plan prompt: raised by the agent when a Plan
// collaboration-mode turn delivers its final plan (the ``<proposed_plan>``
// message right above this form). Mirrors the official Codex TUI's
// "Implement this plan?" menu. The TUI's "clear context and implement" is
// reframed as "implement in a new session": TwiCC is multi-session, so
// nothing is cleared — a fresh session is seeded with the plan and this one
// stays in Plan mode with its full history.
//
// Self-contained: owns its entire body including the action row. The wire
// decision is interpreted by the backend agent
// (``CodexAgent._prompt_plan_implementation``): ``implement`` switches the
// thread back to Default collaboration mode and runs the fixed
// "Implement the plan." turn; ``stay`` and ``newSession`` keep Plan mode and
// return control — for ``newSession`` the whole fresh-session flow lives
// HERE (plan extraction, draft creation, immediate send, navigation).

import { nextTick, onMounted, ref, useId, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { useDataStore } from '../../../../../stores/data'
import { sendWsMessage } from '../../../../../composables/useWebSocket'
import { toast } from '../../../../../composables/useToast'
import { usePendingRequestSubmitShortcut } from '../../../../../composables/usePendingRequestSubmitShortcut'
import { canStealFocus } from '../../../../../utils/focusGuard'
import { generateUUID } from '../../../../../utils/crypto'
import { getParsedContent } from '../../../../../utils/parsedContent'
import { splitProposedPlan } from '../../../../../providers/codex/proposedPlan'
import { agentMessageText } from '../../../../../providers/codex/canonical'
import { PROVIDER } from '../../../../../constants'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
    sessionId: { type: String, required: true },
})
const emit = defineEmits(['submit'])

// First message of the fresh session: adapted from the framing the official
// TUI uses for its "clear context and implement" choice
// (codex-rs/tui/src/chatwidget/plan_implementation.rs), minus its "in a
// fresh context" phrasing — a new TwiCC session needs no such notion.
// Followed by the full plan markdown.
const NEW_SESSION_PREFIX = 'A previous agent produced the plan below to accomplish the user\'s task. '
    + 'Implement it: treat the plan as the source of user intent, re-read '
    + 'files as needed, and carry the work through implementation and '
    + 'verification.'

const dataStore = useDataStore()
const route = useRoute()
const router = useRouter()

const stayButtonId = useId()
const newSessionButtonId = useId()
const implementButtonId = useId()
const implementButtonRef = ref(null)

function implement() {
    emit('submit', { tool_name: 'planImplementation', decision: 'implement' })
}

function stay() {
    emit('submit', { tool_name: 'planImplementation', decision: 'stay' })
}

/**
 * Latest proposed plan of this session, read off the loaded items — the
 * same source the transcript renders (the plan-bearing assistant message is
 * one of the very last items, so it is necessarily loaded). Mirrors the
 * TUI's ``latest_proposed_plan_markdown``.
 */
function latestProposedPlan() {
    const items = dataStore.getSessionItems(props.sessionId)
    for (let i = items.length - 1; i >= 0; i--) {
        const item = items[i]
        if (item?.kind !== 'assistant_message') continue
        const text = agentMessageText(getParsedContent(item))
        const plan = splitProposedPlan(text)?.plan
        if (plan) return plan
    }
    return null
}

function implementInNewSession() {
    const plan = latestProposedPlan()
    const current = dataStore.getSession(props.sessionId)
    if (!plan || !current?.project_id) {
        // The plan message should be right above this form; not finding it
        // means the store has not ingested it (yet). Keep the request open.
        toast.error('Could not find the proposed plan in this session — please retry')
        return
    }

    // 1. Answer the pending request: this session settles idle, still in
    //    Plan mode, its history intact.
    emit('submit', { tool_name: 'planImplementation', decision: 'newSession' })

    // 2. Fresh session in the same project, seeded with the plan and this
    //    session's agent-settings bundle (the draft defaults are overridden
    //    both on the draft row — for the UI — and in the send payload — the
    //    values that actually create the session).
    const projectId = current.project_id
    const draftId = dataStore.createDraftSession(projectId)
    dataStore.setDraftProvider(draftId, PROVIDER.CODEX)
    dataStore.setDraftAgentSettings(draftId, current)

    const text = `${NEW_SESSION_PREFIX}\n\n${plan}`
    const requestId = generateUUID()
    const sent = sendWsMessage({
        type: 'send_message',
        session_id: draftId,
        project_id: projectId,
        provider: PROVIDER.CODEX,
        text,
        permission_mode: current.permission_mode ?? null,
        selected_model: current.selected_model ?? null,
        effort: current.effort ?? null,
        thinking_enabled: current.thinking_enabled ?? null,
        claude_in_chrome: current.claude_in_chrome ?? null,
        fast_mode: current.fast_mode ?? null,
        context_max: current.context_max ?? null,
        hybrid: false,
        layout: dataStore.getSession(draftId)?.layout || {},
        request_id: requestId,
    })
    if (!sent) {
        dataStore.deleteDraftSession(draftId)
        toast.error('Not connected — please retry in a moment')
        return
    }

    // Optimistic user bubble + failure tracking, like any composer send,
    // then promote the draft (kept in store until the backend binds the
    // canonical Codex id via session_bound).
    dataStore.registerOutgoingSend(draftId, projectId, requestId, {
        text,
        medias: [],
        images: undefined,
        documents: undefined,
    })
    dataStore.deleteDraftSession(draftId, { keepInStore: true })

    // 3. Navigate to the fresh session, preserving the current route mode
    //    (single-project vs "All projects") and query params.
    router.push({
        name: route.name === 'projects-session' ? 'projects-session' : 'session',
        params: { ...route.params, sessionId: draftId },
        query: route.query,
    })
}

function focusImplement() {
    nextTick(() => {
        if (!canStealFocus()) return
        implementButtonRef.value?.focus()
    })
}
onMounted(focusImplement)
watch(() => props.pendingRequest?.request_id, focusImplement)

usePendingRequestSubmitShortcut((event) => {
    event.preventDefault()
    event.stopPropagation()
    const activeId = document.activeElement?.id
    if (activeId === stayButtonId) stay()
    else if (activeId === newSessionButtonId) implementInNewSession()
    else implement()
}, () => props.isResponding)
</script>

<template>
    <div class="plan-implementation-body">
        <div class="plan-section">
            <span class="summary-label">Implement this plan?</span>
            <span class="plan-hint">
                Codex proposed the plan above. Implement it now, or keep refining it in Plan mode.
            </span>
        </div>

        <div class="plan-actions">
            <wa-button
                :id="stayButtonId"
                variant="neutral"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="stay"
            >
                <wa-icon slot="start" name="comments" variant="classic"></wa-icon>
                No, stay in Plan mode
            </wa-button>
            <AppTooltip :for="stayButtonId">Continue planning with the model.</AppTooltip>

            <wa-button
                :id="newSessionButtonId"
                variant="brand"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="implementInNewSession"
            >
                <wa-icon slot="start" name="plus" variant="classic"></wa-icon>
                Yes, implement in a new session
            </wa-button>
            <AppTooltip :for="newSessionButtonId">Start a fresh session seeded with the plan; this one stays in Plan mode.</AppTooltip>

            <wa-button
                :id="implementButtonId"
                ref="implementButtonRef"
                class="auto-focused"
                variant="brand"
                size="small"
                :disabled="isResponding"
                @click="implement"
            >
                <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                Yes, implement this plan
            </wa-button>
            <AppTooltip :for="implementButtonId">Switch to Default and start coding.</AppTooltip>
        </div>
    </div>
</template>

<style scoped>
.plan-implementation-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.plan-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-s);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
}

.summary-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

.plan-hint {
    font-size: var(--wa-font-size-m);
}

.plan-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

/* Keep the focus outline visible on the primary button even for mouse /
   programmatic focus (same rationale as PendingRequestBody). */
wa-button.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
</style>
