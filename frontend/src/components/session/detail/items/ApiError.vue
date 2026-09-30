<script setup>
import { computed } from 'vue'
import { parseApiErrorString, stripAnthropicDocsUrl } from '../../../../utils/errorParsing'
import { useDataStore } from '../../../../stores/data'
import { sendWsMessage } from '../../../../composables/useWebSocket'
import { generateUUID } from '../../../../utils/crypto'
import { getProviderHelpers, getProviderLabel } from '../../../../providers'
import { getParsedContent } from '../../../../utils/parsedContent'
import { PROCESS_STATE } from '../../../../constants'

const props = defineProps({
    data: {
        type: Object,
        required: true
    },
    // Context for the recovery affordance. Only meaningful for the terminal
    // (``isApiErrorMessage``) shape; intermediate retry-progress items never
    // get an affordance (recoveryMode stays null).
    projectId: {
        type: String,
        default: null
    },
    sessionId: {
        type: String,
        default: null
    },
    lineNum: {
        type: Number,
        default: null
    }
})

const store = useDataStore()

const provider = computed(() =>
    props.data?.provider || store.getSession(props.sessionId)?.provider || null
)
const providerLabel = computed(() => getProviderLabel(provider.value))

// Image-only messages carry no text to resend; fall back to a minimal nudge so
// the resumed agent re-answers the attachments it already has in context.
const RESEND_FALLBACK_TEXT = 'Please continue.'

// Sent to the agent for the 'retry' (mid-turn error) case. The ``<twicc-…>``
// prefix makes the watcher classify it SYSTEM, so it never surfaces as a user
// message (same hidden channel as the cron-restart messages); the strip of any
// injected ``<twicc:context>`` block runs first, so the persisted text still
// starts with ``<twicc-resume>``. The agent reads the instruction and picks the
// interrupted turn back up.
const RESUME_SYSTEM_MESSAGE =
    '<twicc-resume>You were interrupted by a transient server error before you '
    + 'could finish your turn. Continue from exactly where you left off — pick '
    + 'the work back up; do not start over.</twicc-resume>'

/**
 * Extract error info from the "bastard" API error format.
 *
 * This format has isApiErrorMessage=true but type="assistant".
 * The error is serialized in content[0].text as: "API Error: 500 {json...}"
 *
 * @param {Object} data - The parsed JSON data
 * @returns {Object} Extracted error info
 */
function extractBastardErrorInfo(data) {
    const content = data?.message?.content
    const textContent = content?.[0]?.text || ''

    const parsed = parseApiErrorString(textContent)
    if (parsed) {
        return { ...parsed, retryAttempt: null, maxRetries: null }
    }

    // Fallback: display raw text
    return {
        type: 'unknown_error',
        message: textContent || 'Unknown error',
        status: null,
        retryAttempt: null,
        maxRetries: null
    }
}

const errorInfo = computed(() => {
    // Codex native turn errors and legacy normalized rollout markers share
    // the same error fields. Native errors keep their turn-completion payload.
    if (props.data?.type === 'twicc_provider_error'
        || (props.data?.provider === 'codex' && props.data?.isApiErrorMessage === true)) {
        return {
            type: props.data?.error?.type || 'unknown_error',
            message: props.data?.error?.message || 'Unknown error',
            status: null,
            retryAttempt: null,
            maxRetries: null,
        }
    }

    // "Bastard" format: error is in content[0].text as a string
    if (props.data?.isApiErrorMessage) {
        return extractBastardErrorInfo(props.data)
    }

    // Classic format: nested error object
    const error = props.data?.error?.error?.error
    return {
        type: error?.type || 'unknown_error',
        message: stripAnthropicDocsUrl(error?.message || 'Unknown error'),
        status: props.data?.error?.status,
        retryAttempt: props.data?.retryAttempt,
        maxRetries: props.data?.maxRetries
    }
})

// Recovery affordance — only on a TERMINAL error (isApiErrorMessage) that killed
// the last turn (never on intermediate retry-progress items, the classic shape
// with retryAttempt). The store getter picks 'resend' | 'retry' | null and adds
// the "still the last turn, nothing else in flight" guards.
const recoveryMode = computed(() =>
    (props.data?.isApiErrorMessage === true && props.sessionId != null && props.lineNum != null)
        ? store.apiErrorRecovery(props.sessionId, props.lineNum)
        : null
)

/** Text of the user message that drove the failed turn (closest one before
 *  this api_error). Empty when it is an image-only message or not loaded. */
function resolveResendText() {
    const items = store.getSessionItems(props.sessionId)
    const helpers = getProviderHelpers(store.getSession(props.sessionId)?.provider)
    for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i]
        if (!it) continue  // read-only shares back a sparse (line_num-keyed) array
        if (it.line_num >= props.lineNum) continue
        if (it.kind === 'user_message') {
            return (helpers?.extractUserMessageText(getParsedContent(it)) || '').trim()
        }
    }
    return ''
}

/**
 * Re-drive the failed turn by re-sending its original text only. Attachments
 * are NOT re-sent: the delivered message (text + images) already lives in the
 * agent's context — restored from its own JSONL on resume (SDK) or held by the
 * live CLI (hybrid) — so resending them would only duplicate the images.
 * Carries the session's CURRENT settings untouched (the backend overwrites the
 * stored bundle with the payload, so omitting them would reset forced settings
 * to "use global default"), mirroring the composer / failed-send Retry.
 */
function resend() {
    const session = store.getSession(props.sessionId)
    const text = resolveResendText() || RESEND_FALLBACK_TEXT
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
    // WebSocket down: keep the error + button, the user can resend later.
    if (!sendWsMessage(payload)) return
    store.registerOutgoingSend(props.sessionId, props.projectId, requestId, {
        text,
        medias: [],
        images: [],
        documents: [],
    })
}

/**
 * Ask the agent to resume a turn that died MID-flight (no clean user message to
 * re-send). Delivers the system-classified ``<twicc-resume>`` instruction
 * through the normal send path but WITHOUT an optimistic user bubble — the
 * message must never surface. Carries the session's current settings untouched
 * (same reset trap as resend). An optimistic STARTING state hides the button and
 * gives immediate feedback until the agent's continuation lands.
 */
function retry() {
    const session = store.getSession(props.sessionId)
    const payload = {
        type: 'send_message',
        session_id: props.sessionId,
        project_id: props.projectId,
        provider: session?.provider,
        text: RESUME_SYSTEM_MESSAGE,
        permission_mode: session?.permission_mode ?? null,
        selected_model: session?.selected_model ?? null,
        effort: session?.effort ?? null,
        thinking_enabled: session?.thinking_enabled ?? null,
        claude_in_chrome: session?.claude_in_chrome ?? null,
        fast_mode: session?.fast_mode ?? null,
        context_max: session?.context_max ?? null,
    }
    // WebSocket down: keep the error + button, the user can retry later.
    if (!sendWsMessage(payload)) return
    // No optimistic bubble (the <twicc-resume> text must not show). The
    // optimistic STARTING hides the button at once and shows a spinner until the
    // real process state arrives; the agent's continuation then replaces it.
    store.setProcessState(props.sessionId, props.projectId, PROCESS_STATE.STARTING)
}
</script>

<template>
    <div class="api-error">
        <wa-callout variant="danger" appearance="outlined" size="small">
            <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
            <div class="error-content">
                <div class="error-message">Error from {{ providerLabel }}: {{ errorInfo.message }}</div>
                <div v-if="errorInfo.status || errorInfo.retryAttempt" class="error-details">
                    <span v-if="errorInfo.status">Status: {{ errorInfo.status }}</span>
                    <span v-if="errorInfo.status && errorInfo.retryAttempt" class="separator">•</span>
                    <span v-if="errorInfo.retryAttempt">
                        Retry {{ errorInfo.retryAttempt }}/{{ errorInfo.maxRetries }}
                    </span>
                </div>
            </div>
            <div v-if="recoveryMode" class="recovery-actions">
                <wa-button
                    v-if="recoveryMode === 'resend'"
                    size="small"
                    variant="danger"
                    appearance="outlined"
                    @click="resend"
                >
                    <wa-icon slot="start" name="paper-plane"></wa-icon>
                    Resend your message
                </wa-button>
                <wa-button
                    v-else
                    size="small"
                    variant="danger"
                    appearance="outlined"
                    @click="retry"
                >
                    <wa-icon slot="start" name="rotate-right"></wa-icon>
                    Retry
                </wa-button>
            </div>
        </wa-callout>
    </div>
</template>

<style scoped>
.api-error {
    padding-top: var(--wa-space-l);
}

.error-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.error-message {
    color: inherit;
    font-weight: bold;
}

.error-details {
    font-size: var(--wa-font-size-xs);
    opacity: 0.8;
}

.separator {
    margin: 0 var(--wa-space-2xs);
}

.recovery-actions {
    display: flex;
    justify-content: flex-end;
    margin-top: var(--wa-space-l);
}
</style>
