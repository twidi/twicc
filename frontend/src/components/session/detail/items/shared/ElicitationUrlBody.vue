<script setup>
// ElicitationUrlBody.vue (shared) — body sub-component for an
// ``elicitationUrl`` pending request (request_type ``ask_user_question``,
// mode ``url``) — an MCP server asking the user to open an external page
// (e.g. an OAuth authorization flow) and confirm once done. Used by BOTH
// providers' PendingRequestBody (Codex and Claude Code).
//
// Wire params (tool_input): { serverName, mode: 'url', message, url,
//   elicitationId } — Codex passes its McpServerElicitationRequestParams
// verbatim, Claude's elicitation bridge maps its snake_case fields onto the
// same names. (elicitationId is an internal correlation id, never displayed.)
//
// Self-contained: like the sibling elicitation body, this component owns
// its entire body including the action row (Cancel / Decline / Done).

import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { canStealFocus } from '../../../../../utils/focusGuard'
import { toast } from '../../../../../composables/useToast'
import { usePendingRequestSubmitShortcut } from '../../../../../composables/usePendingRequestSubmitShortcut'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
    sessionId: { type: String, required: true },
})
const emit = defineEmits(['submit'])

const cancelButtonId = useId()
const declineButtonId = useId()
const doneButtonId = useId()
const copyUrlId = useId()
const openLinkId = useId()

// Wire params.
const input = computed(() => props.pendingRequest.tool_input || {})
const serverName = computed(() => input.value.serverName || 'unknown server')
const message = computed(() => input.value.message || '')
const url = computed(() => input.value.url || '')

// Only http(s) URLs are ever rendered clickable — anything else (malformed,
// or another scheme) is shown as inert text.
const isValidUrl = computed(() => {
    if (!url.value) return false
    try {
        const parsed = new URL(url.value)
        return parsed.protocol === 'http:' || parsed.protocol === 'https:'
    } catch {
        return false
    }
})

function copyUrl() {
    navigator.clipboard.writeText(url.value)
    toast.success('URL copied to clipboard', { duration: 2000 })
}

function done() {
    if (props.isResponding) return
    emit('submit', { tool_name: 'elicitationUrl', action: 'accept' })
}
function decline() {
    if (props.isResponding) return
    emit('submit', { tool_name: 'elicitationUrl', action: 'decline' })
}
function cancel() {
    if (props.isResponding) return
    emit('submit', { tool_name: 'elicitationUrl', action: 'cancel' })
}

// Auto-focus Done (same gating as the sibling bodies).
const doneButtonRef = ref(null)
function focusDone() {
    nextTick(() => {
        if (!canStealFocus()) return
        doneButtonRef.value?.focus()
    })
}
onMounted(focusDone)
watch(() => props.pendingRequest?.request_id, focusDone)

usePendingRequestSubmitShortcut((e) => {
    e.preventDefault()
    e.stopPropagation()
    done()
}, () => props.isResponding)
</script>

<template>
    <div v-scroll-shadow class="elicitation-url-body">
        <div class="elicit-section">
            <div class="elicit-summary">
                <span class="elicit-summary-label">MCP URL request</span>
                <wa-badge variant="neutral">{{ serverName }}</wa-badge>
            </div>
            <div v-if="message" class="elicit-reason">
                <span>{{ message }}</span>
            </div>

            <div v-if="url" class="elicitation-url-row">
                <code
                    v-if="isValidUrl"
                    :id="copyUrlId"
                    class="elicit-summary-code elicitation-url-code"
                    @click="copyUrl"
                >
                    <wa-icon name="copy" variant="classic"></wa-icon>
                    {{ url }}
                </code>
                <code v-else class="elicit-summary-code elicitation-url-code elicitation-url-inert">{{ url }}</code>
                <AppTooltip v-if="isValidUrl" :for="copyUrlId">Click to copy</AppTooltip>

                <wa-button
                    v-if="isValidUrl"
                    :id="openLinkId"
                    :href="url"
                    target="_blank"
                    rel="noopener noreferrer"
                    variant="neutral"
                    appearance="outlined"
                    size="small"
                >
                    <wa-icon slot="start" name="up-right-from-square" variant="classic"></wa-icon>
                    Open link
                </wa-button>
                <AppTooltip v-if="isValidUrl" :for="openLinkId">Open this URL in a new tab.</AppTooltip>
            </div>
        </div>

        <div class="elicit-actions">
            <wa-button
                :id="cancelButtonId"
                variant="neutral"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="cancel"
            >
                <wa-icon slot="start" name="ban" variant="classic"></wa-icon>
                Cancel
            </wa-button>
            <AppTooltip :for="cancelButtonId">Dismiss without answering.</AppTooltip>

            <wa-button
                :id="declineButtonId"
                variant="danger"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="decline"
            >
                <wa-icon slot="start" name="xmark" variant="classic"></wa-icon>
                Decline
            </wa-button>
            <AppTooltip :for="declineButtonId">Refuse this request.</AppTooltip>

            <wa-button
                :id="doneButtonId"
                ref="doneButtonRef"
                class="auto-focused"
                variant="brand"
                size="small"
                :disabled="isResponding"
                @click="done"
            >
                <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                Done
            </wa-button>
            <AppTooltip :for="doneButtonId">Confirm you completed the flow in the opened page.</AppTooltip>
        </div>
    </div>
</template>

<style scoped>
.elicitation-url-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.elicit-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-s);
}

.elicit-summary {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.elicit-summary-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

.elicit-summary-code {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
    background: var(--wa-color-neutral-fill-quiet);
    padding: 2px var(--wa-space-2xs);
    border-radius: var(--wa-border-radius-s);
    word-break: break-all;
    white-space: pre-wrap;
}

.elicit-reason {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text);
    font-size: var(--wa-font-size-m);
}

.elicitation-url-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.elicitation-url-code {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    flex: 1 1 auto;
    min-width: min-content;
    cursor: pointer;
}

.elicitation-url-code:not(.elicitation-url-inert):hover {
    background: var(--wa-color-neutral-fill-normal);
}

.elicitation-url-inert {
    cursor: default;
    color: var(--wa-color-text-quiet);
}

.elicit-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

/* Always show the focus outline on the Done button. Default :focus-visible
   would skip mouse and programmatic focus, which hides the indicator we
   want for this primary action.
   We use :focus-within (not :focus) because wa-button delegates focus to an
   inner element in its shadow DOM; the host doesn't carry :focus, but the
   browser keeps :focus-within accurate via activeElement. */
wa-button.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
</style>
