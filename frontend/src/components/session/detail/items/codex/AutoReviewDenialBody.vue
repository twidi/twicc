<script setup>
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { usePendingRequestSubmitShortcut } from '../../../../../composables/usePendingRequestSubmitShortcut'
import { canStealFocus } from '../../../../../utils/focusGuard'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])

const keepDeniedButtonId = useId()
const approveButtonId = useId()
const approveButtonRef = ref(null)

const input = computed(() => props.pendingRequest.tool_input || {})
const action = computed(() => input.value.action || {})
const actionType = computed(() => action.value.type || 'unknown')

const actionLabel = computed(() => ({
    command: 'Command',
    execve: 'Program execution',
    applyPatch: 'File changes',
    networkAccess: 'Network access',
    mcpToolCall: 'MCP tool call',
    requestPermissions: 'Additional permissions',
}[actionType.value] || 'Action'))

const riskVariant = computed(() => {
    if (input.value.riskLevel === 'critical' || input.value.riskLevel === 'high') return 'danger'
    if (input.value.riskLevel === 'medium') return 'warning'
    return 'neutral'
})

const commandText = computed(() => {
    if (actionType.value === 'command') return action.value.command
    if (actionType.value === 'execve') {
        const argv = Array.isArray(action.value.argv) ? action.value.argv : []
        return argv.length ? argv.join(' ') : action.value.program
    }
    return null
})

function approve() {
    emit('submit', { tool_name: 'autoReviewDenial', decision: 'accept' })
}

function keepDenied() {
    emit('submit', { tool_name: 'autoReviewDenial', decision: 'decline' })
}

function focusApprove() {
    nextTick(() => {
        if (!canStealFocus()) return
        approveButtonRef.value?.focus()
    })
}
onMounted(focusApprove)
watch(() => props.pendingRequest?.request_id, focusApprove)

usePendingRequestSubmitShortcut((event) => {
    event.preventDefault()
    event.stopPropagation()
    if (document.activeElement?.id === keepDeniedButtonId) keepDenied()
    else approve()
}, () => props.isResponding)
</script>

<template>
    <div v-scroll-shadow class="auto-review-denial-body">
        <div class="denial-section">
            <div class="denial-summary">
                <span class="summary-label">Auto-review denied this action</span>
                <wa-badge variant="neutral">{{ actionLabel }}</wa-badge>
                <wa-badge v-if="input.riskLevel" :variant="riskVariant">
                    {{ input.riskLevel }} risk
                </wa-badge>
            </div>

            <div v-if="input.rationale" class="rationale">
                <wa-icon name="shield-halved" variant="classic"></wa-icon>
                <span>{{ input.rationale }}</span>
            </div>

            <div v-if="commandText" class="detail-row">
                <span class="summary-label">Command</span>
                <code>{{ commandText }}</code>
            </div>
            <div v-if="action.cwd" class="detail-row">
                <span class="summary-label">cwd</span>
                <code>{{ action.cwd }}</code>
            </div>

            <div v-if="actionType === 'networkAccess'" class="detail-row">
                <span class="summary-label">Target</span>
                <code>{{ action.target || action.host }}</code>
                <span v-if="action.protocol || action.port" class="quiet">
                    {{ action.protocol || '' }}<template v-if="action.port"> · port {{ action.port }}</template>
                </span>
            </div>

            <template v-if="actionType === 'applyPatch'">
                <span class="summary-label">Files</span>
                <ul class="file-list">
                    <li v-for="file in action.files || []" :key="file"><code>{{ file }}</code></li>
                </ul>
            </template>

            <div v-if="actionType === 'mcpToolCall'" class="detail-row">
                <span class="summary-label">Tool</span>
                <code>{{ action.server }} · {{ action.toolTitle || action.toolName }}</code>
            </div>

            <details v-if="actionType === 'requestPermissions' || actionType === 'unknown'">
                <summary class="summary-label">Details</summary>
                <pre>{{ JSON.stringify(action.permissions || action, null, 2) }}</pre>
            </details>
        </div>

        <div class="denial-actions">
            <wa-button
                :id="keepDeniedButtonId"
                variant="danger"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="keepDenied"
            >
                <wa-icon slot="start" name="xmark" variant="classic"></wa-icon>
                Keep denied
            </wa-button>
            <AppTooltip :for="keepDeniedButtonId">Keep Auto-review's decision. Codex may use another approach.</AppTooltip>

            <wa-button
                :id="approveButtonId"
                ref="approveButtonRef"
                class="auto-focused"
                variant="brand"
                size="small"
                :disabled="isResponding"
                @click="approve"
            >
                <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                Approve and retry
            </wa-button>
            <AppTooltip :for="approveButtonId">Approve this exact action and ask Codex to retry it.</AppTooltip>
        </div>
    </div>
</template>

<style scoped>
.auto-review-denial-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.denial-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-s);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
}

.denial-summary,
.detail-row {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
}

.summary-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

.rationale {
    display: flex;
    align-items: flex-start;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text);
    font-size: var(--wa-font-size-m);
}

code,
pre {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}

pre {
    margin: var(--wa-space-xs) 0 0;
}

.quiet {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.file-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    padding: 0;
    margin: 0;
    list-style: none;
}

.denial-actions {
    display: flex;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
}

wa-button.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
</style>
