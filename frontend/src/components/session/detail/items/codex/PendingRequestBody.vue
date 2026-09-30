<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import AutoReviewDenialBody from './AutoReviewDenialBody.vue'
import { getProviderLabel } from '../../../../../providers'
import { useDataStore } from '../../../../../stores/data'
import { fileIconFor } from '../../../../../providers/utils/path'
import { canStealFocus } from '../../../../../utils/focusGuard'
import McpToolCallApprovalBody from './McpToolCallApprovalBody.vue'
import PlanImplementationBody from './PlanImplementationBody.vue'
import RequestUserInputBody from './RequestUserInputBody.vue'
import ElicitationFormBody from '../shared/ElicitationFormBody.vue'
import ElicitationUrlBody from '../shared/ElicitationUrlBody.vue'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
    sessionId: { type: String, required: true },
})
const emit = defineEmits(['submit'])

const denyButtonId = useId()
const cancelTurnButtonId = useId()
const approveButtonId = useId()
const approveMenuId = useId()
const approveOnceId = useId()
const approveForSessionId = useId()
const approveAddAllowRuleId = useId()
const approveAllowNetworkId = useId()
const approvePermsTurnId = useId()
const approvePermsSessionId = useId()

// Template ref for the Approve main button of the split button (auto-focused
// when the approval block appears). Only the legacy body (command / file /
// permissions / unknown fallback) autofocuses via this ref — self-contained
// bodies own their own focus (see their own ``.auto-focused`` element).
const approveButtonRef = ref(null)

// Gated by canStealFocus(): we don't yank focus if the user is typing in
// another field or has an overlay (dialog/popover/dropdown/text-selection
// comment) open. The matching outline is driven by CSS — see the style block.
function focusApproveButton() {
    if (selfContainedBody.value) return
    nextTick(() => {
        if (!canStealFocus()) return
        approveButtonRef.value?.focus()
    })
}

onMounted(focusApproveButton)

// Re-focus the Approve button when a new request takes over the slot (queued approvals).
watch(() => props.pendingRequest?.request_id, focusApproveButton)

// Codex tool_name — legacy kinds ('commandExecution' | 'fileChange' |
// 'permissions') rendered inline below, and the routing key for the
// self-contained bodies ('autoReviewDenial' | 'mcpToolCall' | 'planImplementation' |
// 'toolRequestUserInput' | 'elicitationForm' | 'elicitationUrl').
const toolName = computed(() => props.pendingRequest.tool_name || 'unknown')

// tool_names rendered by a self-contained sub-component that owns its whole
// body INCLUDING the action row (buttons differ per kind). Everything else
// keeps the legacy shared Approve/Deny/Cancel-turn row below.
const SELF_CONTAINED_BODIES = {
    autoReviewDenial: AutoReviewDenialBody,
    mcpToolCall: McpToolCallApprovalBody,
    planImplementation: PlanImplementationBody,
    toolRequestUserInput: RequestUserInputBody,
    elicitationForm: ElicitationFormBody,
    elicitationUrl: ElicitationUrlBody,
}
const selfContainedBody = computed(() => SELF_CONTAINED_BODIES[toolName.value] || null)

// Wire params (as injected by make_pending_request).
const toolInput = computed(() => props.pendingRequest.tool_input || {})

// fileChange-specific fields. For a fileChange approval the diff isn't in the
// wire params (spec §1.1.b) — it's spliced in as ``_item_payload`` (the
// ``item/started`` payload of the correlated ApplyPatch item).
const fileChanges = computed(() => {
    const payload = toolInput.value._item_payload
    if (!payload) return []
    const changes = payload.changes
    return Array.isArray(changes) ? changes : []
})

// Fallback for ``apply_patch`` run through the shell tool (``apply_patch <<'EOF'
// …``): Codex still asks for a ``fileChange`` approval, but the correlated
// ``item/started`` is a ``commandExecution`` ThreadItem, so ``_item_payload``
// carries ``command``/``commandActions``/``cwd`` instead of ``changes``. When we
// land in that state (fileChange approval, no usable changes, but a command
// payload), render the command body instead of an empty "0 files" banner. The
// wire decision stays a fileChange decision — only the body rendering switches.
const renderAsCommand = computed(
    () => toolName.value === 'fileChange'
        && fileChanges.value.length === 0
        && !!toolInput.value._item_payload?.command,
)

// Show the rich command body for native commandExecution approvals and for the
// apply_patch-via-shell fallback above.
const showCommandBody = computed(
    () => toolName.value === 'commandExecution' || renderAsCommand.value,
)

// Where the command fields live: native commandExecution approvals carry them
// in the wire params (spec §1.1.a); the fallback reads them off the indexed
// item payload.
const commandSource = computed(
    () => (renderAsCommand.value ? toolInput.value._item_payload : toolInput.value),
)

// commandExecution-specific fields. ``command``/``cwd``/``commandActions`` follow
// ``commandSource`` so the fallback pulls them from ``_item_payload``; the
// amendment / network fields only exist on a real commandExecution approval's
// params, so they stay on ``toolInput`` (absent → hidden in the fallback).
const command = computed(() => commandSource.value.command)
const cwd = computed(() => commandSource.value.cwd)
const reason = computed(() => toolInput.value.reason)
const commandActions = computed(() => commandSource.value.commandActions || [])
const networkApprovalContext = computed(() => toolInput.value.networkApprovalContext)
const proposedExecpolicyAmendment = computed(() => toolInput.value.proposedExecpolicyAmendment)
const proposedNetworkPolicyAmendments = computed(() => toolInput.value.proposedNetworkPolicyAmendments)

// permissions-specific fields
const requestedPermissions = computed(() => toolInput.value.permissions || {})

// Provider label for the session
const dataStore = useDataStore()
const providerLabel = computed(() =>
    getProviderLabel(dataStore.getSession(props.sessionId)?.provider),
)

// Group commandActions by type for the redesigned chips + detail sections
const groupedActions = computed(() => {
    const groups = { read: [], listFiles: [], search: [], unknown: [] }
    for (const action of commandActions.value) {
        const t = action?.type
        if (t === 'read') groups.read.push(action)
        else if (t === 'listFiles') groups.listFiles.push(action)
        else if (t === 'search') groups.search.push(action)
        else groups.unknown.push(action)
    }
    return groups
})

// Whether the "Cancel turn" button is available for this tool_name.
// Permissions don't have a "cancel" wire variant per spec §1.1.c.
const supportsCancelTurn = computed(
    () => toolName.value === 'commandExecution' || toolName.value === 'fileChange',
)

function emitApprove(variant) {
    // `variant` is one of:
    // - 'once' | 'forSession'      (command / file)
    // - 'addAllowRule'              (command only, requires proposedExecpolicyAmendment)
    // - 'allowNetwork'              (command only, requires proposedNetworkPolicyAmendments)
    // - 'turn' | 'session'          (permissions)
    if (toolName.value === 'permissions') {
        emit('submit', {
            tool_name: 'permissions',
            permissions: requestedPermissions.value,
            scope: variant === 'session' ? 'session' : 'turn',
        })
        return
    }
    if (variant === 'forSession') {
        emit('submit', { tool_name: toolName.value, decision: 'acceptForSession' })
        return
    }
    if (variant === 'addAllowRule') {
        emit('submit', {
            tool_name: toolName.value,
            decision: {
                acceptWithExecpolicyAmendment: {
                    execpolicy_amendment: proposedExecpolicyAmendment.value,
                },
            },
        })
        return
    }
    if (variant === 'allowNetwork') {
        emit('submit', {
            tool_name: toolName.value,
            decision: {
                applyNetworkPolicyAmendment: {
                    network_policy_amendment: proposedNetworkPolicyAmendments.value,
                },
            },
        })
        return
    }
    // 'once' (default)
    emit('submit', { tool_name: toolName.value, decision: 'accept' })
}

function handleDeny() {
    if (toolName.value === 'permissions') {
        emit('submit', {
            tool_name: 'permissions',
            permissions: {},
            scope: 'turn',
        })
    } else {
        emit('submit', { tool_name: toolName.value, decision: 'decline' })
    }
}

function handleCancelTurn() {
    emit('submit', { tool_name: toolName.value, decision: 'cancel' })
}

// Cmd/Ctrl+Enter: submit the form. Mirrors the MessageInput shortcut, dispatched
// here from a document-level listener because the body has a fragment root and
// can't carry a single @keydown attribute. We only act if the focus is inside
// the .pending-request-form, and if not already responding.
//
// Action depends on which control has focus:
//   - Deny button        → handleDeny
//   - Cancel turn button → handleCancelTurn (when the button is rendered)
//   - anything else      → emitApprove('once'), which maps to the first
//                          item of the Approve dropdown (Approve once for
//                          command/file, "For this turn" for permissions).
function onSubmitShortcut(e) {
    if (e.key !== 'Enter') return
    if (!(e.metaKey || e.ctrlKey)) return
    if (selfContainedBody.value) return
    const form = document.querySelector('.pending-request-form:not([data-footer-leaving] *)')
    if (!form || !form.contains(document.activeElement)) return
    if (props.isResponding) return

    const activeId = document.activeElement?.id
    if (activeId === denyButtonId) {
        e.preventDefault()
        e.stopPropagation()
        handleDeny()
        return
    }
    if (activeId === cancelTurnButtonId) {
        e.preventDefault()
        e.stopPropagation()
        handleCancelTurn()
        return
    }

    e.preventDefault()
    e.stopPropagation()
    emitApprove('once')
}

onMounted(() => {
    document.addEventListener('keydown', onSubmitShortcut)
})
onBeforeUnmount(() => {
    document.removeEventListener('keydown', onSubmitShortcut)
})
</script>

<template>
    <div class="codex-pending-body">
        <component
            :is="selfContainedBody"
            v-if="selfContainedBody"
            :pending-request="pendingRequest"
            :is-responding="isResponding"
            :session-id="sessionId"
            @submit="emit('submit', $event)"
        />
        <template v-else>
            <!-- commandExecution rich body (also the apply_patch-via-shell fallback) -->
            <template v-if="showCommandBody">
                <div class="codex-pending-section">
                    <!-- Fallback header: surface the shell command so the banner
                         isn't empty. Native commandExecution approvals keep their
                         original chip-first layout (renderAsCommand is false). -->
                    <div v-if="renderAsCommand" class="codex-pending-summary">
                        <span class="codex-summary-label">
                            {{ providerLabel }} wants to run a command
                        </span>
                    </div>
                    <!-- action chips first, at-a-glance summary -->
                    <div v-if="commandActions.length" class="codex-action-chips">
                        <wa-badge v-if="groupedActions.read.length" variant="neutral">
                            Read
                            <span v-if="groupedActions.read.length > 1" class="codex-chip-count">{{ groupedActions.read.length }}</span>
                        </wa-badge>
                        <wa-badge v-if="groupedActions.listFiles.length" variant="neutral">
                            List files
                            <span v-if="groupedActions.listFiles.length > 1" class="codex-chip-count">{{ groupedActions.listFiles.length }}</span>
                        </wa-badge>
                        <wa-badge v-if="groupedActions.search.length" variant="neutral">
                            Grep
                            <span v-if="groupedActions.search.length > 1" class="codex-chip-count">{{ groupedActions.search.length }}</span>
                        </wa-badge>
                        <wa-badge v-if="groupedActions.unknown.length" variant="neutral">
                            Shell
                            <span v-if="groupedActions.unknown.length > 1" class="codex-chip-count">{{ groupedActions.unknown.length }}</span>
                        </wa-badge>
                    </div>
                    <!-- reason below chips -->
                    <div v-if="reason" class="codex-pending-reason">
                        <wa-icon name="comment" variant="classic"></wa-icon>
                        <span>{{ reason }}</span>
                    </div>
                    <!-- detail sections: Read -->
                    <div v-if="groupedActions.read.length" class="codex-pending-section-block">
                        <span class="codex-summary-label">Read</span>
                        <ul class="codex-action-detail-list">
                            <li v-for="(action, idx) in groupedActions.read" :key="idx">
                                <img
                                    v-if="fileIconFor(action.path)"
                                    :src="fileIconFor(action.path)"
                                    alt=""
                                    class="codex-file-icon"
                                >
                                <code class="codex-file-path">{{ action.path }}</code>
                            </li>
                        </ul>
                    </div>
                    <!-- detail sections: List files -->
                    <div v-if="groupedActions.listFiles.length" class="codex-pending-section-block">
                        <span class="codex-summary-label">List files</span>
                        <ul class="codex-action-detail-list">
                            <li v-for="(action, idx) in groupedActions.listFiles" :key="idx">
                                <img
                                    v-if="fileIconFor(action.path)"
                                    :src="fileIconFor(action.path)"
                                    alt=""
                                    class="codex-file-icon"
                                >
                                <code class="codex-file-path">{{ action.path }}</code>
                            </li>
                        </ul>
                    </div>
                    <!-- detail sections: Grep -->
                    <div v-if="groupedActions.search.length" class="codex-pending-section-block">
                        <span class="codex-summary-label">Grep</span>
                        <ul class="codex-action-detail-list">
                            <li v-for="(action, idx) in groupedActions.search" :key="idx">
                                <code class="codex-search-query">{{ action.query || '(no query)' }}</code>
                                <span v-if="action.path">in</span>
                                <img
                                    v-if="action.path && fileIconFor(action.path)"
                                    :src="fileIconFor(action.path)"
                                    alt=""
                                    class="codex-file-icon"
                                >
                                <code v-if="action.path" class="codex-file-path">{{ action.path }}</code>
                            </li>
                        </ul>
                    </div>
                    <!-- command and cwd last -->
                    <div class="codex-pending-summary">
                        <span class="codex-summary-label">Command</span>
                        <code class="codex-summary-code">{{ command }}</code>
                    </div>
                    <div v-if="cwd" class="codex-pending-summary">
                        <span class="codex-summary-label">cwd</span>
                        <code class="codex-summary-code">{{ cwd }}</code>
                    </div>
                    <div v-if="networkApprovalContext" class="codex-pending-network">
                        <wa-icon name="globe" variant="classic"></wa-icon>
                        <span>
                            Wants network access to
                            <code>{{ networkApprovalContext.host }}</code>
                            via {{ networkApprovalContext.protocol || 'unknown' }}
                        </span>
                    </div>
                </div>
            </template>

            <!-- fileChange rich body -->
            <template v-else-if="toolName === 'fileChange'">
                <div class="codex-pending-section">
                    <!-- reason first -->
                    <div v-if="reason" class="codex-pending-reason">
                        <wa-icon name="comment" variant="classic"></wa-icon>
                        <span>{{ reason }}</span>
                    </div>
                    <div class="codex-pending-summary">
                        <span class="codex-summary-label">
                            {{ providerLabel }} wants to modify {{ fileChanges.length }} file{{ fileChanges.length === 1 ? '' : 's' }}
                        </span>
                    </div>
                    <ul v-if="fileChanges.length" class="codex-file-list">
                        <li v-for="(change, idx) in fileChanges" :key="idx" class="codex-file-row">
                            <wa-badge
                                :variant="change.kind?.type === 'delete' ? 'danger'
                                    : change.kind?.type === 'add' ? 'success' : 'neutral'"
                            >{{ change.kind?.type || 'update' }}</wa-badge>
                            <img
                                v-if="fileIconFor(change.path)"
                                :src="fileIconFor(change.path)"
                                alt=""
                                class="codex-file-icon"
                            >
                            <code class="codex-file-path">{{ change.path }}</code>
                        </li>
                    </ul>
                </div>
            </template>

            <!-- permissions rich body -->
            <template v-else-if="toolName === 'permissions'">
                <div class="codex-pending-section">
                    <!-- reason first -->
                    <div v-if="reason" class="codex-pending-reason">
                        <wa-icon name="comment" variant="classic"></wa-icon>
                        <span>{{ reason }}</span>
                    </div>
                    <div class="codex-pending-summary">
                        <span class="codex-summary-label">Requests additional permissions</span>
                    </div>
                    <ul class="codex-permission-list">
                        <li v-for="(value, key) in requestedPermissions" :key="key" class="codex-permission-row">
                            <code class="codex-permission-key">{{ key }}</code>
                            <span class="codex-permission-value">{{ JSON.stringify(value) }}</span>
                        </li>
                    </ul>
                </div>
            </template>

            <!-- Unknown tool_name safety net -->
            <template v-else>
                <div class="codex-pending-section"><em>Unknown tool_name: {{ toolName }}</em></div>
            </template>

            <!-- Shared action row. -->
            <div class="codex-pending-actions">
                <wa-button
                    :id="denyButtonId"
                    variant="danger"
                    appearance="outlined"
                    size="small"
                    :disabled="isResponding"
                    @click="handleDeny"
                >
                    <wa-icon slot="start" name="xmark" variant="classic"></wa-icon>
                    Deny
                </wa-button>
                <AppTooltip :for="denyButtonId">Refuse this action. Codex may try another approach.</AppTooltip>
                <wa-button
                    v-if="supportsCancelTurn"
                    :id="cancelTurnButtonId"
                    variant="neutral"
                    appearance="outlined"
                    size="small"
                    :disabled="isResponding"
                    @click="handleCancelTurn"
                >
                    <wa-icon slot="start" name="stop" variant="classic"></wa-icon>
                    Cancel turn
                </wa-button>
                <AppTooltip v-if="supportsCancelTurn" :for="cancelTurnButtonId">End this turn. Codex returns control to you. Different from Stop (which kills the agent).</AppTooltip>
                <wa-button-group label="Approve">
                    <wa-button
                        :id="approveButtonId"
                        ref="approveButtonRef"
                        class="auto-focused"
                        variant="brand"
                        size="small"
                        :disabled="isResponding"
                        @click="emitApprove('once')"
                    >
                        <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                        Approve
                    </wa-button>
                    <AppTooltip :for="approveButtonId">Approve this action.</AppTooltip>
                    <wa-dropdown placement="top-end">
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

                        <!-- command / file menu -->
                        <template v-if="toolName === 'commandExecution' || toolName === 'fileChange'">
                            <wa-dropdown-item :id="approveOnceId" @click="emitApprove('once')">
                                <wa-icon slot="icon" name="check" variant="classic"></wa-icon>
                                Once
                            </wa-dropdown-item>
                            <AppTooltip placement="left" :for="approveOnceId">Approve only this action.</AppTooltip>
                            <wa-dropdown-item :id="approveForSessionId" @click="emitApprove('forSession')">
                                <wa-icon slot="icon" name="rotate" variant="classic"></wa-icon>
                                For this session
                            </wa-dropdown-item>
                            <AppTooltip placement="left" :for="approveForSessionId">Approve and remember this decision for the rest of the session.</AppTooltip>
                            <wa-dropdown-item
                                v-if="toolName === 'commandExecution' && proposedExecpolicyAmendment"
                                :id="approveAddAllowRuleId"
                                @click="emitApprove('addAllowRule')"
                            >
                                <wa-icon slot="icon" name="plus" variant="classic"></wa-icon>
                                Add allow rule
                            </wa-dropdown-item>
                            <AppTooltip v-if="toolName === 'commandExecution' && proposedExecpolicyAmendment" placement="left" :for="approveAddAllowRuleId">Approve and add this command to the persistent allow list.</AppTooltip>
                            <wa-dropdown-item
                                v-if="toolName === 'commandExecution' && proposedNetworkPolicyAmendments"
                                :id="approveAllowNetworkId"
                                @click="emitApprove('allowNetwork')"
                            >
                                <wa-icon slot="icon" name="globe" variant="classic"></wa-icon>
                                Allow network access
                            </wa-dropdown-item>
                            <AppTooltip v-if="toolName === 'commandExecution' && proposedNetworkPolicyAmendments" placement="left" :for="approveAllowNetworkId">Approve and persist the network policy amendment.</AppTooltip>
                        </template>

                        <!-- permissions menu -->
                        <template v-else-if="toolName === 'permissions'">
                            <wa-dropdown-item :id="approvePermsTurnId" @click="emitApprove('turn')">
                                <wa-icon slot="icon" name="clock" variant="classic"></wa-icon>
                                For this turn
                            </wa-dropdown-item>
                            <AppTooltip placement="left" :for="approvePermsTurnId">Grant the requested permissions for the current turn only.</AppTooltip>
                            <wa-dropdown-item :id="approvePermsSessionId" @click="emitApprove('session')">
                                <wa-icon slot="icon" name="rotate" variant="classic"></wa-icon>
                                For this session
                            </wa-dropdown-item>
                            <AppTooltip placement="left" :for="approvePermsSessionId">Grant the requested permissions for the full session.</AppTooltip>
                        </template>
                    </wa-dropdown>
                </wa-button-group>
            </div>
        </template>
    </div>
</template>

<style scoped>
.codex-pending-body {
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
}

.codex-pending-reason {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text);
    font-size: var(--wa-font-size-m);
}

.codex-pending-network {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.codex-action-chips {
    display: flex;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}

.codex-chip-count {
    display: inline-block;
    margin-left: var(--wa-space-2xs);
    padding: 0 var(--wa-space-2xs);
    background: var(--wa-color-neutral-fill-loud);
    color: var(--wa-color-neutral-on-loud);
    border-radius: var(--wa-border-radius-pill);
    font-size: 0.85em;
    line-height: 1.4;
}

.codex-pending-section-block {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.codex-action-detail-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.codex-action-detail-list li {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.codex-search-query {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
}

.codex-file-list,
.codex-permission-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.codex-file-row,
.codex-permission-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.codex-file-icon {
    width: 1em;
    height: 1em;
    flex-shrink: 0;
}

.codex-file-path,
.codex-permission-key {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
    word-break: break-all;
}

.codex-permission-value {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
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
