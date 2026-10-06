<script setup>
import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { canStealFocus } from '../../../../../utils/focusGuard'
import { usePendingRequestSubmitShortcut } from '../../../../../composables/usePendingRequestSubmitShortcut'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
    sessionId: { type: String, required: true },
})
const emit = defineEmits(['submit'])

const denyButtonId = useId()
const approveButtonId = useId()
const approveMenuId = useId()
const approveOnceId = useId()
const approveSessionId = useId()
const approveAlwaysId = useId()

// Wire params: McpServerElicitationRequestParams (mode=form, approval-tagged).
const input = computed(() => props.pendingRequest.tool_input || {})
const serverName = computed(() => input.value.serverName || 'unknown server')
const message = computed(() => input.value.message || '')
const meta = computed(() => {
    const m = input.value._meta
    return (m && typeof m === 'object') ? m : {}
})

// ``persist`` advertises which remember variants Codex will honour:
// "session" | "always" | ["session","always"] | absent.
const persistOptions = computed(() => {
    const p = meta.value.persist
    if (Array.isArray(p)) return p.filter((v) => typeof v === 'string')
    return typeof p === 'string' ? [p] : []
})
const canPersistSession = computed(() => persistOptions.value.includes('session'))
const canPersistAlways = computed(() => persistOptions.value.includes('always'))
const hasApproveMenu = computed(() => canPersistSession.value || canPersistAlways.value)

const toolTitle = computed(() => meta.value.tool_title)
const toolDescription = computed(() => meta.value.tool_description)
// Pre-rendered params: [{name, value, displayName}] — preferred display.
// Malformed (null / non-object) entries are dropped so they can't throw at render.
const paramsDisplay = computed(() => {
    const list = meta.value.tool_params_display
    if (!Array.isArray(list)) return []
    return list.filter((p) => p && typeof p === 'object')
})
// Raw arguments fallback when no rendered display is provided.
const rawParams = computed(() => {
    if (paramsDisplay.value.length) return null
    const p = meta.value.tool_params
    return (p && typeof p === 'object' && Object.keys(p).length) ? p : null
})

function displayValue(value) {
    return typeof value === 'string' ? value : JSON.stringify(value)
}

function approve(persist) {
    const payload = { tool_name: 'mcpToolCall', action: 'accept' }
    if (persist) payload.persist = persist
    emit('submit', payload)
}
function deny() {
    emit('submit', { tool_name: 'mcpToolCall', action: 'decline' })
}

// Auto-focus Approve (same gating as the legacy body).
const approveButtonRef = ref(null)
function focusApprove() {
    nextTick(() => {
        if (!canStealFocus()) return
        approveButtonRef.value?.focus()
    })
}
onMounted(focusApprove)
watch(() => props.pendingRequest?.request_id, focusApprove)

usePendingRequestSubmitShortcut((e) => {
    e.preventDefault()
    e.stopPropagation()
    if (document.activeElement?.id === denyButtonId) {
        deny()
    } else {
        approve()
    }
}, () => props.isResponding)
</script>

<template>
    <div v-scroll-shadow class="mcp-approval-body">
        <div class="codex-pending-section">
            <div class="codex-pending-summary">
                <span class="codex-summary-label">MCP tool call</span>
                <wa-badge variant="neutral">
                    {{ serverName }}<template v-if="toolTitle"> · {{ toolTitle }}</template>
                </wa-badge>
            </div>
            <div v-if="message" class="codex-pending-reason">
                <span>{{ message }}</span>
            </div>
            <div v-if="toolDescription" class="mcp-description">{{ toolDescription }}</div>

            <ul v-if="paramsDisplay.length" class="mcp-param-list">
                <li v-for="(param, idx) in paramsDisplay" :key="idx" class="mcp-param-row">
                    <code class="mcp-param-name">{{ param.displayName || param.name }}</code>
                    <span class="mcp-param-value">{{ displayValue(param.value) }}</span>
                </li>
            </ul>
            <details v-else-if="rawParams" class="mcp-raw-params">
                <summary class="codex-summary-label">Arguments</summary>
                <pre class="codex-summary-code">{{ JSON.stringify(rawParams, null, 2) }}</pre>
            </details>
        </div>

        <div class="codex-pending-actions">
            <wa-button
                :id="denyButtonId"
                variant="danger"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="deny"
            >
                <wa-icon slot="start" name="xmark" variant="classic"></wa-icon>
                Deny
            </wa-button>
            <AppTooltip :for="denyButtonId">Refuse this tool call. Codex may try another approach.</AppTooltip>

            <wa-button-group label="Approve">
                <wa-button
                    :id="approveButtonId"
                    ref="approveButtonRef"
                    class="auto-focused"
                    variant="brand"
                    size="small"
                    :disabled="isResponding"
                    @click="approve()"
                >
                    <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                    Approve
                </wa-button>
                <AppTooltip :for="approveButtonId">Approve this tool call.</AppTooltip>

                <wa-dropdown v-if="hasApproveMenu" placement="top-end">
                    <wa-button
                        :id="approveMenuId"
                        slot="trigger"
                        variant="brand"
                        size="small"
                        :disabled="isResponding"
                    >
                        <wa-icon name="chevron-up" label="More approve options" variant="classic"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="approveMenuId">More approve options.</AppTooltip>

                    <wa-dropdown-item :id="approveOnceId" :disabled="isResponding" @click="approve()">
                        <wa-icon slot="icon" name="check" variant="classic"></wa-icon>
                        Once
                    </wa-dropdown-item>
                    <AppTooltip placement="left" :for="approveOnceId">Approve only this call.</AppTooltip>
                    <wa-dropdown-item v-if="canPersistSession" :id="approveSessionId" :disabled="isResponding" @click="approve('session')">
                        <wa-icon slot="icon" name="rotate" variant="classic"></wa-icon>
                        For this session
                    </wa-dropdown-item>
                    <AppTooltip v-if="canPersistSession" placement="left" :for="approveSessionId">Approve and remember for the rest of the session.</AppTooltip>
                    <wa-dropdown-item v-if="canPersistAlways" :id="approveAlwaysId" :disabled="isResponding" @click="approve('always')">
                        <wa-icon slot="icon" name="infinity" variant="classic"></wa-icon>
                        Always
                    </wa-dropdown-item>
                    <AppTooltip v-if="canPersistAlways" placement="left" :for="approveAlwaysId">Approve and never ask again for this tool.</AppTooltip>
                </wa-dropdown>
            </wa-button-group>
        </div>
    </div>
</template>

<style scoped>
.mcp-approval-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.codex-pending-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-s);
}

.codex-pending-summary {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.codex-summary-label {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

.codex-summary-code {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
    background: var(--wa-color-neutral-fill-quiet);
    padding: 2px var(--wa-space-2xs);
    border-radius: var(--wa-border-radius-s);
    word-break: break-all;
    white-space: pre-wrap;
}

.codex-pending-reason {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text);
    font-size: var(--wa-font-size-m);
}

.mcp-description {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.mcp-param-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.mcp-param-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.mcp-param-name {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
    word-break: break-all;
}

.mcp-param-value {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.mcp-raw-params summary {
    cursor: pointer;
}

.codex-pending-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

/* Always show the focus outline on the Approve main button of the split
   button. Default :focus-visible would skip mouse and programmatic focus,
   which hides the indicator we want for this primary action.
   We use :focus-within (not :focus) because wa-button delegates focus to an
   inner element in its shadow DOM; the host doesn't carry :focus, but the
   browser keeps :focus-within accurate via activeElement. */
wa-button.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
</style>
