<script setup>
// PendingRequestBody.vue (claude_code) — body sub-component for pending requests.
//
// Handles two request types:
// - tool_approval: Shows tool name, formatted parameters, permission suggestions,
//   edit mode, deny reason input, and action buttons.
// - ask_user_question: Shows questions with selectable options and an "Other"
//   free-text input.
// Plus the MCP elicitation tool_names (elicitationForm / elicitationUrl),
// routed to the self-contained shared bodies (same components as Codex) —
// those own their entire body including the action row.
//
// This component does NOT own:
// - The card outer wrapper (<wa-divider>, container div)
// - The shared header (icon + title + count badge + expand toggle)
// - The dispatch via respondToPendingRequest
//
// Instead, each button handler emits ('submit', payload) and the parent shell
// is responsible for dispatching the response.

import { ref, computed, reactive, watch, nextTick, onMounted, onBeforeUnmount, useId } from 'vue'
import JsonHumanView from '../../../../json/JsonHumanView.vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import ElicitationFormBody from '../shared/ElicitationFormBody.vue'
import ElicitationUrlBody from '../shared/ElicitationUrlBody.vue'
import { getLanguageFromPath } from '../../../../../utils/languages'
import { canStealFocus } from '../../../../../utils/focusGuard'
import { usePendingRequestDraft } from '../../../../../composables/usePendingRequestDraft'

// Per-tool overrides for JsonHumanView (display type or visibility).
// Only keys that need an override (not auto-detected) are listed.
// Language is added dynamically by toolOverrides based on the file path in tool input.
const TOOL_OVERRIDES_BASE = {
    Bash: {
        command: { valueType: 'string-code' },
    },
    Write: {
        content: { valueType: 'string-code' },
    },
    Edit: {
        old_string: { valueType: 'string-code' },
        new_string: { valueType: 'string-code' },
    },
    NotebookEdit: {
        new_source: { valueType: 'string-code' },
    },
    // ExitPlanMode: the CLI injects ``planFilePath`` (the internal plan-file
    // location) into the tool input alongside ``plan``. It's an implementation
    // detail the user has no reason to see or edit — and editing it has no
    // effect anyway (the backend plan-file write reads the *original* path).
    // Hide it so only the plan content is shown and, in "Approve with changes",
    // is the only editable field.
    ExitPlanMode: {
        planFilePath: { hidden: true },
    },
}

// Base overrides for permission suggestion JsonHumanView: the destination field is rendered as a select.
const BASE_SUGGESTION_OVERRIDES = {
    destination: {
        valueType: 'select',
        options: ['userSettings', 'projectSettings', 'localSettings', 'session'],
    },
}

/**
 * Compute overrides for a given suggestion, merging base overrides with dynamic ones.
 * If the suggestion contains _ruleContentOptions, the ruleContent field inside rules[0]
 * gets a select override. The _ruleContentOptions key itself is hidden.
 */
function suggestionOverrides(suggestion) {
    const options = suggestion?._ruleContentOptions
    if (!options?.length) return { ...BASE_SUGGESTION_OVERRIDES, _ruleContentOptions: { hidden: true } }
    return {
        ...BASE_SUGGESTION_OVERRIDES,
        _ruleContentOptions: { hidden: true },
        rules: {
            children: {
                0: {
                    children: {
                        ruleContent: {
                            valueType: 'select',
                            options,
                        },
                    },
                },
            },
        },
    }
}

// Maps tool names to the key in tool input that contains the file path.
const TOOL_PATH_KEYS = {
    Write: 'file_path',
    Edit: 'file_path',
    NotebookEdit: 'notebook_path',
}

// Human-readable labels for the permission_mode wire values.
const MODE_LABELS = {
    default: 'Default',
    auto: 'Auto',
    acceptEdits: 'Accept all edits',
    bypassPermissions: 'Bypass permissions',
    plan: 'Plan',
    dontAsk: "Don't ask",
}

function formatModeLabel(mode) {
    return MODE_LABELS[mode] || mode
}

// Per-option help text shown on a mode the trust floor makes unavailable
// (untrusted project). Mirrors the agent-settings popover wording.
const DISABLED_MODE_REASON = 'Unavailable on untrusted projects.'

const props = defineProps({
    pendingRequest: {
        type: Object,
        required: true,
    },
    isResponding: {
        type: Boolean,
        default: false,
    },
    // Declared even though unused locally: the shell passes session-id to every body,
    // and our fragment-root template can't absorb it as a fallthrough attribute (Vue would warn).
    sessionId: {
        type: String,
        required: true,
    },
})

const emit = defineEmits(['submit'])

// ============================================================================
// Button IDs (for tooltip anchoring)
// ============================================================================

const denyButtonId = useId()
const approveWithChangesButtonId = useId()
const approveButtonId = useId()
const submitQuestionsButtonId = useId()
const submitQuestionsMenuId = useId()
const submitPartiallyItemId = useId()
const cancelQuestionsItemId = useId()

// ============================================================================
// Tool approval state
// ============================================================================

// Deny reason text (optional)
const denyReason = ref('')

// Whether the deny reason input is shown
const showDenyReason = ref(false)

// Template ref for the deny reason input
const denyReasonInputRef = ref(null)

// Template ref for the Approve button (auto-focused when the approval block appears)
const approveButtonRef = ref(null)

// Whether the form is in "approve with changes" edit mode
const isEditing = ref(false)

// Deep copy of tool input being edited (null when not editing)
const editedToolInput = ref(null)

// Set of suggestion indices that the user has checked (for permission suggestions)
const checkedSuggestions = reactive(new Set())

// Edited copies of permission suggestions (only entries modified by the user)
const editedSuggestions = reactive(new Map())

// Mode picked from the setMode suggestion's select (null = keep current mode)
const selectedMode = ref(null)

// ============================================================================
// Ask user question state
// ============================================================================

// Selections per question index: maps question index to selected option label(s)
// For single-select: string (the selected label) or null
// For multi-select: Set of selected labels
const questionSelections = reactive({})

// "Other" text per question index
const otherTexts = reactive({})

// Whether "Other" is the active choice for each question
const otherActive = reactive({})

// Template refs for "Other" inputs (keyed by question index)
const otherInputRefs = ref({})

// ============================================================================
// Shared
// ============================================================================

// Request type for conditional rendering
const requestType = computed(() => props.pendingRequest.request_type)

// tool_names rendered by a self-contained sub-component that owns its whole
// body INCLUDING the action row (same pattern as the Codex body). MCP
// elicitations arrive with these tool_names (request_type ask_user_question);
// everything else keeps the legacy request_type-driven rendering below.
const SELF_CONTAINED_BODIES = {
    elicitationForm: ElicitationFormBody,
    elicitationUrl: ElicitationUrlBody,
}
const selfContainedBody = computed(
    () => SELF_CONTAINED_BODIES[props.pendingRequest.tool_name] || null,
)

// Reset state when the pending request changes (e.g., a new one arrives after the previous was resolved)
watch(() => props.pendingRequest?.request_id, () => {
    // Tool approval state
    denyReason.value = ''
    showDenyReason.value = false
    isEditing.value = false
    editedToolInput.value = null
    checkedSuggestions.clear()
    editedSuggestions.clear()
    selectedMode.value = null
    // Ask user question state
    Object.keys(questionSelections).forEach(k => delete questionSelections[k])
    Object.keys(otherTexts).forEach(k => delete otherTexts[k])
    Object.keys(otherActive).forEach(k => delete otherActive[k])
    // Re-focus the primary target for the new request
    focusPrimaryTarget()
})

// Auto-focus the primary target when the pending request block appears or a new
// request arrives:
//   - tool_approval     → Approve button (approveButtonRef)
//   - ask_user_question → first option-card of the first question
//     (already marked with the `auto-focused` class in the template)
// Gated by canStealFocus(): we don't yank focus when the user is typing in
// another field or has an overlay (dialog/popover/dropdown/text-selection
// comment) open. The matching outline is driven by CSS — see the style block.
function focusPrimaryTarget() {
    // Self-contained bodies own their own focus (see their `.auto-focused` element).
    if (selfContainedBody.value) return
    if (!['tool_approval', 'ask_user_question'].includes(requestType.value)) return
    nextTick(() => {
        if (!canStealFocus()) return
        if (requestType.value === 'tool_approval') {
            approveButtonRef.value?.focus()
        } else {
            document.querySelector('.pending-request-form:not([data-footer-leaving] *) .option-card.auto-focused')?.focus()
        }
    })
}

// Focus the current "primary" element of the form unconditionally (no canStealFocus
// gating). Used to follow user-initiated mode transitions (cancelling deny / edit)
// so the next Cmd+Enter still lands on the right action. Relies on the
// `auto-focused` marker class which is mutually exclusive across the modes (only
// one element carries it at any given time).
function focusFormPrimary() {
    nextTick(() => {
        document.querySelector('.pending-request-form:not([data-footer-leaving] *) .auto-focused')?.focus()
    })
}

// Focus the first editable widget of the JSON editor when entering "Approve with
// changes" mode — the user just opted into editing, so they want to be in the
// fields, not on the submit button. Prefer a CodeMirror surface when present
// (multi-line strings such as Bash command or Edit old/new_string are the
// payload the user typically wants to tweak); fall back to the first scalar
// widget (wa-input / wa-textarea / wa-select) otherwise, and finally to the
// brand button if nothing editable is found.
//
// CodeMirror mounts in its own onMounted (one tick after Vue commits the
// template), so we briefly retry to give it a chance to appear before falling
// back. Tools known to use code editors gate the retry; other tools (e.g. Read)
// skip it and focus the first scalar widget immediately.
const TOOLS_WITH_CODE_EDITOR = new Set(['Bash', 'Write', 'Edit', 'NotebookEdit'])
function focusEditedContent(retries = 10) {
    nextTick(() => {
        const details = document.querySelector('.pending-request-form:not([data-footer-leaving] *) .pending-request-details')
        if (!details) {
            focusFormPrimary()
            return
        }
        const expectCodeMirror = TOOLS_WITH_CODE_EDITOR.has(toolName.value)
        if (expectCodeMirror) {
            const cm = details.querySelector('.cm-content')
            if (cm) {
                cm.focus()
                return
            }
            if (retries > 0) {
                setTimeout(() => focusEditedContent(retries - 1), 30)
                return
            }
            // Code editor never showed up: fall through to scalar widgets.
        }
        const fallback = details.querySelector('wa-input, wa-textarea, wa-select, [contenteditable="true"]')
        if (fallback) {
            fallback.focus()
        } else {
            focusFormPrimary()
        }
    })
}

onMounted(focusPrimaryTarget)

// Cmd/Ctrl+Enter: submit the form. Mirrors the MessageInput shortcut, dispatched
// here from a document-level listener because the body has a fragment root and
// can't carry a single @keydown attribute. We only act if the focus is inside
// the .pending-request-form (so the shortcut doesn't fire from unrelated inputs)
// and if the primary action isn't disabled.
//
// Action depends on the current state:
//   - ask_user_question        → Submit (requires every question answered)
//   - tool_approval, deny mode → Deny (sends the typed reason, if any)
//   - tool_approval, edit mode → "Approve with changes"
//   - tool_approval, initial   → Deny if focus is on the Deny button (sent
//                                directly without going through the reason
//                                input), otherwise Approve.
function onSubmitShortcut(e) {
    if (e.key !== 'Enter') return
    if (!(e.metaKey || e.ctrlKey)) return
    // Self-contained bodies register their own Cmd/Ctrl+Enter handler
    // (usePendingRequestSubmitShortcut) — never double-fire from here.
    if (selfContainedBody.value) return
    const form = document.querySelector('.pending-request-form:not([data-footer-leaving] *)')
    if (!form || !form.contains(document.activeElement)) return
    if (props.isResponding) return

    if (requestType.value === 'ask_user_question') {
        if (!canSubmitQuestions.value) return
        e.preventDefault()
        e.stopPropagation()
        handleSubmitQuestions()
        return
    }

    if (requestType.value !== 'tool_approval') return

    if (showDenyReason.value) {
        e.preventDefault()
        e.stopPropagation()
        handleDeny()
        return
    }

    if (isEditing.value) {
        e.preventDefault()
        e.stopPropagation()
        handleApproveWithChanges()
        return
    }

    // Initial mode: Deny on focused Deny button (sent directly, no reason);
    // Approve otherwise.
    if (document.activeElement?.id === denyButtonId) {
        e.preventDefault()
        e.stopPropagation()
        emit('submit', {
            request_type: 'tool_approval',
            decision: 'deny',
            message: 'User denied this action',
        })
        return
    }

    e.preventDefault()
    e.stopPropagation()
    handleApprove()
}

onMounted(() => {
    document.addEventListener('keydown', onSubmitShortcut)
})
onBeforeUnmount(() => {
    document.removeEventListener('keydown', onSubmitShortcut)
})

// ============================================================================
// Tool approval computed
// ============================================================================

// Tool name (raw, used for override lookup)
const toolName = computed(() => props.pendingRequest.tool_name || 'Unknown tool')

// Tool name formatted for display (underscores → spaces, collapse multiple)
const toolNameDisplay = computed(() => toolName.value.replace(/_+/g, ' '))

// Tool input data: uses the edited copy when in edit mode, otherwise the original from the pending request.
const toolInput = computed(() =>
    isEditing.value && editedToolInput.value != null
        ? editedToolInput.value
        : (props.pendingRequest.tool_input || {})
)

// Overrides for JsonHumanView based on tool name, enriched with language from file path.
// For tools like Write/Edit/NotebookEdit, detects the language from the file_path/notebook_path
// in tool input and adds it to the overrides so code gets syntax highlighting.
const toolOverrides = computed(() => {
    const base = TOOL_OVERRIDES_BASE[toolName.value]
    if (!base) return {}

    const pathKey = TOOL_PATH_KEYS[toolName.value]
    if (!pathKey) return base

    const filePath = toolInput.value[pathKey]
    if (!filePath || typeof filePath !== 'string') return base

    const language = getLanguageFromPath(filePath)
    if (!language) return base

    // Enrich each override entry with the detected language
    const enriched = {}
    for (const [key, override] of Object.entries(base)) {
        enriched[key] = { ...override, language }
    }
    return enriched
})

// All permission suggestions from the backend.
const allPermissionSuggestions = computed(() => props.pendingRequest.permission_suggestions || [])

// Permission suggestions rendered in the collapsible details (everything except the setMode picker,
// which is rendered separately in its own visible row).
const permissionSuggestions = computed(() =>
    allPermissionSuggestions.value.filter(s => s.type !== 'setMode')
)

// The setMode suggestion (if any) — rendered as a standalone select above the action buttons.
const setModeSuggestion = computed(() =>
    allPermissionSuggestions.value.find(s => s.type === 'setMode') || null
)

// Options for the mode picker, normalized to ``{mode, disabled}`` objects.
// The SDK path injects a synthetic suggestion carrying the full
// `_modeOptions` list (backend-built, with disabled entries for the
// untrusted floor); hybrid sessions pass the hook's native setMode
// suggestion verbatim, which carries a single concrete `mode` (e.g.
// acceptEdits, the TUI's "allow all edits during this session") — render it
// as the lone, always-enabled switch option (the untrusted floor is
// enforced CLI-side in hybrid).
const modeOptions = computed(() => {
    const suggestion = setModeSuggestion.value
    if (!suggestion) return []
    return suggestion._modeOptions || (suggestion.mode ? [{ mode: suggestion.mode, disabled: false }] : [])
})

// Label for the "don't change mode" option in the picker.
const noChangeLabel = computed(() => {
    const current = setModeSuggestion.value?._currentMode
    return current ? `Don't change (current: ${formatModeLabel(current)})` : "Don't change"
})

// Whether a mode value is a disabled option (untrusted floor). The backend
// flags these in ``_modeOptions`` so the picker shows them as "(not available)"
// instead of hiding them; selecting/submitting one must never be possible.
function isModeDisabled(mode) {
    const opt = setModeSuggestion.value?._modeOptions?.find(o => o.mode === mode)
    return !!opt?.disabled
}

// Whether there are any permission suggestions to display
const hasPermissionSuggestions = computed(() => permissionSuggestions.value.length > 0)

// Summary label for the permission suggestions details element
const permissionSummaryLabel = computed(() => {
    const count = permissionSuggestions.value.length
    const accepted = checkedSuggestions.size
    let label = `${count} Permission suggestion${count > 1 ? 's' : ''}`
    if (accepted > 0) {
        label += accepted === count ? ' (All accepted)' : ` (${accepted} accepted)`
    }
    return label
})

// Whether the tool input has any fields that can be edited
const hasEditableContent = computed(() => {
    const input = props.pendingRequest.tool_input
    return input && typeof input === 'object' && Object.keys(input).length > 0
})

/**
 * Get the suggestion object for a given index, using the edited version if available.
 * @param {number} index - Index in the permissionSuggestions array
 * @returns {Object} The original or edited suggestion object
 */
function editedSuggestion(index) {
    return editedSuggestions.get(index) ?? permissionSuggestions.value[index]
}

/**
 * Handle update from a suggestion's JsonHumanView (e.g., destination select changed).
 * Stores the edited suggestion copy.
 * @param {number} index - Index in the permissionSuggestions array
 * @param {Object} newValue - The updated suggestion object
 */
function onSuggestionUpdate(index, newValue) {
    editedSuggestions.set(index, newValue)
    // Auto-accept: editing a suggestion implies the user wants to apply it
    checkedSuggestions.add(index)
}

/**
 * Toggle a permission suggestion's checked state.
 * @param {number} index - Index in the permissionSuggestions array
 */
function togglePermissionSuggestion(index) {
    if (checkedSuggestions.has(index)) {
        checkedSuggestions.delete(index)
    } else {
        checkedSuggestions.add(index)
    }
}

/**
 * Get the list of checked permission suggestion dicts (to send back as updated_permissions).
 * Returns edited versions when the user has modified a suggestion (e.g., changed destination).
 * Includes the setMode picker as a separate entry when the user has selected a target mode.
 * @returns {Array<Object>|null} The checked permission suggestion objects, or null if none
 */
function getCheckedPermissionSuggestions() {
    const result = []
    for (const index of checkedSuggestions) {
        const suggestion = { ...editedSuggestion(index) }
        // Strip private fields (e.g. _ruleContentOptions) — not part of the SDK protocol.
        for (const key of Object.keys(suggestion)) {
            if (key.startsWith('_')) delete suggestion[key]
        }
        // If ruleContent is empty (user chose "allow all"), remove it from the rule.
        for (const rule of suggestion.rules || []) {
            if ('ruleContent' in rule && !rule.ruleContent) {
                delete rule.ruleContent
            }
        }
        result.push(suggestion)
    }
    if (selectedMode.value && setModeSuggestion.value && !isModeDisabled(selectedMode.value)) {
        result.push({
            type: 'setMode',
            mode: selectedMode.value,
            destination: setModeSuggestion.value.destination || 'session',
        })
    }
    return result.length > 0 ? result : null
}

/**
 * Handle a change in the permission mode select.
 * Stores null when the user picks "don't change" (empty value), otherwise the selected mode.
 */
function onModeChange(event) {
    const value = event.target.value
    // Defensive: disabled options can't be picked through the UI, but never
    // accept one via a forged/edge event — the backend would re-clamp anyway.
    if (value && isModeDisabled(value)) {
        selectedMode.value = null
        return
    }
    selectedMode.value = value || null
}

// ============================================================================
// Ask user question computed
// ============================================================================

// The questions array from the pending request
const questions = computed(() => toolInput.value.questions || [])

// Number of questions that currently have an answer.
const answeredQuestionCount = computed(() => {
    let count = 0
    for (let i = 0; i < questions.value.length; i++) {
        if (getQuestionAnswer(i)) count++
    }
    return count
})

// Full submit requires every question to be answered.
const canSubmitQuestions = computed(
    () => questions.value.length > 0 && answeredQuestionCount.value === questions.value.length,
)

// Partial submit is offered only when some — but not all — questions are answered.
const canSubmitPartial = computed(
    () =>
        questions.value.length > 0 &&
        answeredQuestionCount.value > 0 &&
        answeredQuestionCount.value < questions.value.length,
)

// ============================================================================
// Tool approval handlers
// ============================================================================

/**
 * Build the base approval response payload, including checked permission suggestions.
 * @param {Object} toolInputValue - The tool input to include (original or edited)
 * @returns {Object} The response payload
 */
function buildApprovalResponse(toolInputValue) {
    const response = {
        request_type: 'tool_approval',
        decision: 'allow',
        updated_input: toolInputValue,
    }
    const permissions = getCheckedPermissionSuggestions()
    if (permissions) {
        response.updated_permissions = permissions
    }
    return response
}

/**
 * Handle approve action.
 * Emits 'submit' with the approval payload including original input and any checked permission suggestions.
 */
function handleApprove() {
    if (props.isResponding) return
    emit('submit', buildApprovalResponse(toolInput.value))
}

/**
 * Handle deny action.
 * Shows the deny reason input first (if not shown), then emits 'submit' with the denial payload.
 */
function handleDeny() {
    if (props.isResponding) return

    // If deny reason input is not shown yet, show it and focus
    if (!showDenyReason.value) {
        showDenyReason.value = true
        nextTick(() => {
            denyReasonInputRef.value?.focus()
        })
        return
    }

    const message = denyReason.value.trim() || 'User denied this action'
    emit('submit', {
        request_type: 'tool_approval',
        decision: 'deny',
        message,
    })
}

/**
 * Cancel showing the deny reason input and return to the main buttons.
 */
function cancelDeny() {
    showDenyReason.value = false
    denyReason.value = ''
    focusFormPrimary()
}

/**
 * Handle keyboard shortcut in deny reason textarea.
 * Escape cancels the deny. (Cmd/Ctrl+Enter is handled globally by
 * onSubmitShortcut, which submits regardless of which child has focus.)
 */
function onDenyReasonKeydown(event) {
    if (event.key === 'Escape') {
        event.preventDefault()
        cancelDeny()
    }
}

/**
 * Enter "approve with changes" edit mode.
 * Deep-clones the current tool input so the user can modify it.
 */
function handleStartEdit() {
    editedToolInput.value = JSON.parse(JSON.stringify(props.pendingRequest.tool_input || {}))
    isEditing.value = true
    // Also close the deny reason if it was open
    showDenyReason.value = false
    denyReason.value = ''
    // The user just opened the editor — land focus inside the first editable
    // field, not on the submit button.
    focusEditedContent()
}

/**
 * Cancel edit mode and discard changes.
 * Restores the original (read-only) tool input view.
 */
function cancelEdit() {
    isEditing.value = false
    editedToolInput.value = null
    focusFormPrimary()
}

/**
 * Handle "approve with changes" action.
 * Emits 'submit' with the modified tool input as updated_input and any checked permission suggestions.
 */
function handleApproveWithChanges() {
    if (props.isResponding) return
    emit('submit', buildApprovalResponse(editedToolInput.value))
}

/**
 * Handle update from JsonHumanView in edit mode.
 * Updates the editedToolInput ref with the new value.
 * @param {Object} newValue - The updated tool input object
 */
function onToolInputUpdate(newValue) {
    editedToolInput.value = newValue
}

// ============================================================================
// Ask user question handlers
// ============================================================================

/**
 * Select an option for a question.
 * For single-select: replaces the selection.
 * For multi-select: toggles the option in the set.
 * Clears "Other" when a predefined option is selected (single-select only).
 *
 * @param {number} questionIndex - Index in the questions array
 * @param {string} label - The option label to select/toggle
 * @param {boolean} multiSelect - Whether this question allows multiple selections
 */
function selectOption(questionIndex, label, multiSelect) {
    if (multiSelect) {
        // Multi-select: toggle the option
        if (!questionSelections[questionIndex]) {
            questionSelections[questionIndex] = new Set()
        }
        const selections = questionSelections[questionIndex]
        if (selections.has(label)) {
            selections.delete(label)
        } else {
            selections.add(label)
        }
        // Deactivate "Other" when selecting predefined options
        otherActive[questionIndex] = false
        otherTexts[questionIndex] = ''
    } else {
        // Single-select: replace selection
        questionSelections[questionIndex] = label
        // Deactivate "Other"
        otherActive[questionIndex] = false
        otherTexts[questionIndex] = ''
    }
}

/**
 * Handle keyboard navigation and selection on option cards.
 * Enter/Space select; ArrowLeft/Right wrap-navigate within the question;
 * Home/End jump to first/last card.
 */
function handleOptionKeydown(event, qIndex, option, multiSelect) {
    if (props.isResponding) return

    const key = event.key

    if (key === 'Enter' || key === ' ') {
        event.preventDefault()
        selectOption(qIndex, option.label, multiSelect)
        return
    }

    if (key === 'ArrowLeft' || key === 'ArrowRight' || key === 'Home' || key === 'End') {
        const currentCard = event.currentTarget
        const container = currentCard.parentElement
        if (!container) return
        const cards = Array.from(container.querySelectorAll(':scope > .option-card'))
        const currentIndex = cards.indexOf(currentCard)
        if (currentIndex === -1) return
        event.preventDefault()
        let targetIndex
        if (key === 'ArrowLeft') {
            targetIndex = (currentIndex - 1 + cards.length) % cards.length
        } else if (key === 'ArrowRight') {
            targetIndex = (currentIndex + 1) % cards.length
        } else if (key === 'Home') {
            targetIndex = 0
        } else {
            targetIndex = cards.length - 1
        }
        cards[targetIndex].focus()
    }
}

/**
 * Check if an option is currently selected for a question.
 *
 * @param {number} questionIndex - Index in the questions array
 * @param {string} label - The option label to check
 * @param {boolean} multiSelect - Whether this question allows multiple selections
 * @returns {boolean}
 */
function isOptionSelected(questionIndex, label, multiSelect) {
    if (multiSelect) {
        const selections = questionSelections[questionIndex]
        return selections instanceof Set && selections.has(label)
    }
    return questionSelections[questionIndex] === label
}

/**
 * Toggle "Other" free-text mode for a question.
 * If already active, deactivates it and clears the text.
 * If not active, activates it and clears predefined selections.
 *
 * @param {number} questionIndex - Index in the questions array
 * @param {boolean} multiSelect - Whether this question allows multiple selections
 */
function toggleOther(questionIndex, multiSelect) {
    if (otherActive[questionIndex]) {
        // Deactivate: clear "Other" state
        otherActive[questionIndex] = false
        otherTexts[questionIndex] = ''
        return
    }
    otherActive[questionIndex] = true
    if (!multiSelect) {
        questionSelections[questionIndex] = null
    } else {
        questionSelections[questionIndex] = new Set()
    }
    // Focus the input after Vue renders it
    nextTick(() => {
        const input = otherInputRefs.value[questionIndex]
        if (input) input.focus()
    })
}

/**
 * Handle "Other" text input change.
 *
 * @param {number} questionIndex - Index in the questions array
 * @param {Event} event - The input event
 */
function onOtherInput(questionIndex, event) {
    otherTexts[questionIndex] = event.target.value
}

/**
 * Get the answer value for a question.
 * Returns null if no answer is selected.
 *
 * @param {number} questionIndex - Index in the questions array
 * @returns {string|null} The answer value or null
 */
function getQuestionAnswer(questionIndex) {
    // "Other" takes priority when active and has text
    if (otherActive[questionIndex]) {
        const text = (otherTexts[questionIndex] || '').trim()
        return text || null
    }

    const question = questions.value[questionIndex]
    const multiSelect = question?.multiSelect

    if (multiSelect) {
        const selections = questionSelections[questionIndex]
        if (!(selections instanceof Set) || selections.size === 0) return null
        return Array.from(selections).join(', ')
    }

    return questionSelections[questionIndex] || null
}

/**
 * Build the answers map (question text -> answer value), omitting any question
 * left unanswered.
 *
 * @returns {Object} Map of question text to answer value
 */
function buildQuestionAnswers() {
    const answers = {}
    for (let i = 0; i < questions.value.length; i++) {
        const answer = getQuestionAnswer(i)
        if (answer) answers[questions.value[i].question] = answer
    }
    return answers
}

/**
 * Submit every question answer (full answer).
 */
function handleSubmitQuestions() {
    if (props.isResponding || !canSubmitQuestions.value) return
    emit('submit', {
        request_type: 'ask_user_question',
        action: 'submit',
        answers: buildQuestionAnswers(),
    })
}

/**
 * Submit the answered questions while leaving the rest unanswered. Claude is
 * told the user wants to clarify the remaining questions.
 */
function handleSubmitPartial() {
    if (props.isResponding || !canSubmitPartial.value) return
    emit('submit', {
        request_type: 'ask_user_question',
        action: 'partial',
        answers: buildQuestionAnswers(),
    })
}

/**
 * Decline the questions entirely. No answers are forwarded; Claude acknowledges
 * the decline and asks how you'd like to proceed.
 */
function handleCancelQuestions() {
    if (props.isResponding) return
    emit('submit', {
        request_type: 'ask_user_question',
        action: 'cancel',
        answers: {},
    })
}

// ============================================================================
// Draft persistence
// ============================================================================

// Persist everything the user has typed or picked — question answers AND the
// tool-approval side (deny reason, accepted permission suggestions, mode
// picker, and the "Approve with changes" edited input, which can hold a whole
// rewritten plan) — so a page reload doesn't throw it away.
//
// Gated off for the self-contained bodies: they run this same composable on
// the same (session, request) key, so writing our own (empty) state here would
// wipe theirs.
usePendingRequestDraft({
    sessionId: () => props.sessionId,
    pendingRequest: () => props.pendingRequest,
    isResponding: () => props.isResponding,
    enabled: () => !selfContainedBody.value,
    collect: () => {
        // Multi-select answers live in a Set — structured clone would take it,
        // but JSON (which is how the draft is sized and plain-ified) would not.
        const selections = {}
        for (const [key, value] of Object.entries(questionSelections)) {
            selections[key] = value instanceof Set ? [...value] : value
        }
        return {
            questionSelections: selections,
            otherTexts: { ...otherTexts },
            otherActive: { ...otherActive },
            denyReason: denyReason.value,
            showDenyReason: showDenyReason.value,
            isEditing: isEditing.value,
            editedToolInput: editedToolInput.value,
            checkedSuggestions: [...checkedSuggestions],
            // Map keys are suggestion indices; JSON turns them into strings.
            editedSuggestions: Object.fromEntries(editedSuggestions),
            selectedMode: selectedMode.value,
        }
    },
    apply: (state) => {
        for (const [key, value] of Object.entries(state.questionSelections || {})) {
            questionSelections[key] = Array.isArray(value) ? new Set(value) : value
        }
        Object.assign(otherTexts, state.otherTexts || {})
        Object.assign(otherActive, state.otherActive || {})
        denyReason.value = state.denyReason || ''
        showDenyReason.value = state.showDenyReason === true
        // Edit mode without its edited copy would silently show the original
        // input as editable — restore the pair or neither.
        editedToolInput.value = state.editedToolInput ?? null
        isEditing.value = state.isEditing === true && editedToolInput.value != null
        checkedSuggestions.clear()
        for (const index of state.checkedSuggestions || []) checkedSuggestions.add(index)
        editedSuggestions.clear()
        for (const [key, value] of Object.entries(state.editedSuggestions || {})) {
            editedSuggestions.set(Number(key), value)
        }
        selectedMode.value = state.selectedMode ?? null
    },
})
</script>

<template>
    <!-- MCP elicitations: self-contained shared bodies (form / URL), which own
         their entire rendering including the action row. -->
    <component
        :is="selfContainedBody"
        v-if="selfContainedBody"
        :pending-request="pendingRequest"
        :is-responding="isResponding"
        :session-id="sessionId"
        @submit="emit('submit', $event)"
    />

    <template v-else-if="requestType === 'tool_approval'">
        <!-- Tool details -->
        <div class="pending-request-details">
            <div class="tool-name-badge">
                <wa-badge variant="neutral">{{ toolNameDisplay }}</wa-badge>
            </div>

            <!-- Permission suggestions (wa-details, closed by default) -->
            <wa-details v-if="hasPermissionSuggestions" class="permission-suggestions-details">
            <span slot="summary">
                <wa-icon name="key" variant="classic"></wa-icon>
                {{ permissionSummaryLabel }}
            </span>
            <div class="permission-suggestions-list">
                <wa-card
                    v-for="(suggestion, sIndex) in permissionSuggestions"
                    :key="sIndex"
                    appearance="outlined"
                    class="permission-suggestion-card"
                    :class="{ selected: checkedSuggestions.has(sIndex) }"
                    @click="togglePermissionSuggestion(sIndex)"
                >
                    <div class="permission-suggestion-card-body" @click.stop>
                        <JsonHumanView
                            :value="editedSuggestion(sIndex)"
                            :overrides="suggestionOverrides(editedSuggestion(sIndex))"
                            @update:value="onSuggestionUpdate(sIndex, $event)"
                        />
                    </div>
                    <div class="permission-suggestion-card-footer">
                        <wa-switch
                            size="small"
                            :checked.prop="checkedSuggestions.has(sIndex)"
                            @click.stop
                            @change.stop="togglePermissionSuggestion(sIndex)"
                        >Apply this permission</wa-switch>
                    </div>
                </wa-card>
            </div>
        </wa-details>

            <JsonHumanView
                :value="toolInput"
                :overrides="toolOverrides"
                :editable="isEditing"
                @update:value="onToolInputUpdate"
            />
        </div>

        <!-- Permission mode picker — lets the user switch the session's mode while answering this approval.
             Hidden in deny mode because the wire doesn't carry updated_permissions on deny. -->
        <div v-if="setModeSuggestion && !showDenyReason" class="mode-selector-row">
            <label class="mode-selector-label">
                <wa-icon name="key" variant="classic"></wa-icon>
                Permission mode
            </label>
            <wa-select
                size="small"
                :value.prop="selectedMode || ''"
                @change="onModeChange"
                class="mode-selector-input"
                :disabled="isResponding"
            >
                <wa-option value="">{{ noChangeLabel }}</wa-option>
                <wa-option
                    v-for="opt in modeOptions"
                    :key="opt.mode"
                    :value="opt.mode"
                    :disabled="opt.disabled"
                >
                    <span>Switch to {{ formatModeLabel(opt.mode) }}{{ opt.disabled ? ' (not available)' : '' }}</span>
                    <span v-if="opt.disabled" class="option-description">{{ DISABLED_MODE_REASON }}</span>
                </wa-option>
            </wa-select>
        </div>

        <!-- Action buttons: three states — default / deny reason / editing -->
        <div class="pending-request-actions">
            <!-- Default state: Deny / Approve with changes / Approve -->
            <template v-if="!showDenyReason && !isEditing">
                <wa-button
                    :id="denyButtonId"
                    variant="danger"
                    appearance="outlined"
                    size="small"
                    :disabled="isResponding"
                    @click="handleDeny"
                >
                    <wa-spinner v-if="isResponding" slot="start"></wa-spinner>
                    <wa-icon v-else name="xmark" variant="classic" slot="start"></wa-icon>
                    Deny
                </wa-button>
                <AppTooltip :for="denyButtonId">Refuse this action. Claude will receive your message.</AppTooltip>
                <wa-button
                    v-if="hasEditableContent"
                    :id="approveWithChangesButtonId"
                    variant="neutral"
                    appearance="outlined"
                    size="small"
                    :disabled="isResponding"
                    @click="handleStartEdit"
                >
                    <wa-icon name="pen" variant="classic" slot="start"></wa-icon>
                    Approve with changes
                </wa-button>
                <AppTooltip v-if="hasEditableContent" :for="approveWithChangesButtonId">Approve using the edited tool input.</AppTooltip>
                <wa-button
                    :id="approveButtonId"
                    ref="approveButtonRef"
                    class="auto-focused"
                    variant="brand"
                    size="small"
                    :disabled="isResponding"
                    @click="handleApprove"
                >
                    <wa-spinner v-if="isResponding" slot="start"></wa-spinner>
                    <wa-icon v-else name="check" variant="classic" slot="start"></wa-icon>
                    Approve
                </wa-button>
                <AppTooltip :for="approveButtonId">Approve as-is.</AppTooltip>
            </template>
            <!-- Deny state: textarea + Cancel / Deny -->
            <template v-else-if="showDenyReason">
                <div class="deny-reason-row">
                    <wa-textarea
                        ref="denyReasonInputRef"
                        placeholder="Reason for denial (optional)"
                        size="small"
                        rows="2"
                        resize="auto"
                        :value.prop="denyReason"
                        @input="denyReason = $event.target.value"
                        @keydown="onDenyReasonKeydown"
                        class="deny-reason-input auto-focused"
                    ></wa-textarea>
                    <wa-button
                        variant="neutral"
                        appearance="outlined"
                        size="small"
                        @click="cancelDeny"
                    >
                        Cancel
                    </wa-button>
                    <wa-button
                        variant="danger"
                        size="small"
                        :disabled="isResponding"
                        @click="handleDeny"
                    >
                        <wa-spinner v-if="isResponding" slot="start"></wa-spinner>
                        <wa-icon v-else name="xmark" variant="classic" slot="start"></wa-icon>
                        Deny
                    </wa-button>
                </div>
            </template>
            <!-- Edit state: Cancel / Approve with changes -->
            <template v-else-if="isEditing">
                <wa-button
                    variant="neutral"
                    appearance="outlined"
                    size="small"
                    @click="cancelEdit"
                >
                    Cancel
                </wa-button>
                <wa-button
                    class="auto-focused"
                    variant="brand"
                    size="small"
                    :disabled="isResponding"
                    @click="handleApproveWithChanges"
                >
                    <wa-spinner v-if="isResponding" slot="start"></wa-spinner>
                    <wa-icon v-else name="check" variant="classic" slot="start"></wa-icon>
                    Approve with changes
                </wa-button>
            </template>
        </div>
    </template>

    <template v-else-if="requestType === 'ask_user_question'">
        <!-- Questions -->
        <div class="questions-container">
            <div
                v-for="(question, qIndex) in questions"
                :key="qIndex"
                class="question-block"
            >
                <!-- Question header and text -->
                <div v-if="question.header" class="question-header">{{ question.header }}</div>
                <div class="question-text">{{ question.question }}</div>
                <div class="question-select-hint">{{ question.multiSelect ? 'Select one or more' : 'Select one' }}</div>

                <!-- Options as selectable cards -->
                <div class="question-options">
                    <wa-card
                        v-for="(option, optionIndex) in question.options"
                        :key="option.label"
                        appearance="outlined"
                        class="option-card"
                        :class="{
                            selected: isOptionSelected(qIndex, option.label, question.multiSelect),
                            disabled: isResponding,
                            'auto-focused': qIndex === 0 && optionIndex === 0,
                        }"
                        role="button"
                        :tabindex="isResponding ? -1 : 0"
                        :aria-pressed="isOptionSelected(qIndex, option.label, question.multiSelect) ? 'true' : 'false'"
                        :aria-disabled="isResponding ? 'true' : null"
                        @click="!isResponding && selectOption(qIndex, option.label, question.multiSelect)"
                        @keydown="handleOptionKeydown($event, qIndex, option, question.multiSelect)"
                    >
                        <div class="option-card-content">
                            <span class="option-indicator" :class="question.multiSelect ? 'option-indicator--check' : 'option-indicator--radio'" aria-hidden="true"></span>
                            <div class="option-card-text">
                                <span class="option-label">{{ option.label }}</span>
                                <span v-if="option.description" class="option-description">{{ option.description }}</span>
                            </div>
                        </div>
                    </wa-card>
                </div>

                <!-- "Other" toggle link + text input -->
                <div class="other-section">
                    <a
                        href="#"
                        class="other-toggle-link"
                        :class="{ disabled: isResponding }"
                        @click.prevent="!isResponding && toggleOther(qIndex, question.multiSelect)"
                    >{{ otherActive[qIndex] ? 'Cancel other' : 'Other...' }}</a>
                </div>
                <div v-if="otherActive[qIndex]" class="other-input-row">
                    <wa-textarea
                        :ref="el => { if (el) otherInputRefs[qIndex] = el }"
                        placeholder="Type your answer..."
                        size="small"
                        rows="1"
                        resize="auto"
                        :value.prop="otherTexts[qIndex] || ''"
                        @input="onOtherInput(qIndex, $event)"
                        class="other-input"
                    ></wa-textarea>
                </div>
            </div>
        </div>

        <!-- Submit (full) + dropdown: Submit partially / Cancel -->
        <div class="pending-request-actions">
            <wa-button-group label="Submit answers">
                <wa-button
                    :id="submitQuestionsButtonId"
                    variant="brand"
                    size="small"
                    :disabled="isResponding || !canSubmitQuestions"
                    @click="handleSubmitQuestions"
                >
                    <wa-spinner v-if="isResponding" slot="start"></wa-spinner>
                    <wa-icon v-else name="paper-plane" variant="classic" slot="start"></wa-icon>
                    Submit
                </wa-button>
                <AppTooltip :for="submitQuestionsButtonId">Send your answers. Enabled once every question is answered.</AppTooltip>
                <wa-dropdown placement="top-end">
                    <wa-button
                        :id="submitQuestionsMenuId"
                        slot="trigger"
                        variant="brand"
                        size="small"
                        :disabled="isResponding"
                    >
                        <wa-icon name="chevron-up" label="More submit options" variant="classic"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="submitQuestionsMenuId">More options.</AppTooltip>
                    <wa-dropdown-item
                        :id="submitPartiallyItemId"
                        :disabled="isResponding || !canSubmitPartial"
                        @click="handleSubmitPartial"
                    >
                        <wa-icon slot="icon" name="paper-plane" variant="classic"></wa-icon>
                        Submit partially
                    </wa-dropdown-item>
                    <AppTooltip placement="left" :for="submitPartiallyItemId">Send the answers you've given and let Claude ask about the rest.</AppTooltip>
                    <wa-dropdown-item
                        :id="cancelQuestionsItemId"
                        :disabled="isResponding"
                        @click="handleCancelQuestions"
                    >
                        <wa-icon slot="icon" name="ban" variant="classic"></wa-icon>
                        Cancel
                    </wa-dropdown-item>
                    <AppTooltip placement="left" :for="cancelQuestionsItemId">Decline to answer. Claude acknowledges and asks how to proceed.</AppTooltip>
                </wa-dropdown>
            </wa-button-group>
        </div>
    </template>
</template>

<style scoped>

.pending-request-details {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-s);
    overflow-y: auto;
    flex: 1;
    /* Never collapse to nothing: below this the card itself scrolls. */
    min-height: 6rem;
}

.tool-name-badge {
    margin-bottom: var(--wa-space-2xs);
}

/* =========================================================================
   Permission suggestions styles
   ========================================================================= */

.permission-suggestions-details {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    --spacing: var(--wa-space-xs);

    &::part(base) {
        display: inline-block;
    }
    [slot="summary"] {
        display: flex !important;
        align-items: center;
        gap: var(--wa-space-s);
        font-weight: 600;
    }
    &::part(content) {
        padding-block-start: 0 !important;
        padding-block-end: calc(var(--spacing) + 4px);
    }
}

.permission-suggestions-list {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
}

.permission-suggestion-card {
    flex: 1 1 0;
    min-width: min-content;
    max-width: 20rem;
    cursor: pointer;
    transition: border-color 0.15s, background-color 0.15s;
    --spacing: var(--wa-space-s);

    --border-color-base: var(--wa-color-surface-border);
    --background-color-base: var(--wa-color-surface-raised);
    --border-color: var(--border-color-base);
    --background-color: var(--background-color-base);

    border-color: var(--border-color);
    background: var(--background-color);
    box-shadow: var(--depth-1);
    &:hover {
        --border-color: oklch(from var(--border-color-base)calc(l + 0.025) c h);
        --background-color: oklch(from var(--background-color-base)calc(l + 0.025) c h);
    }
}

.permission-suggestion-card.selected {
    --border-color-base: var(--wa-color-border-normal);
    --background-color-base: var(--wa-color-fill-normal);
}

.permission-suggestion-card-body {
    font-size: var(--wa-font-size-s);
}

.permission-suggestion-card-footer {
    margin-top: var(--wa-space-xs);
    padding-top: var(--wa-space-xs);
    border-top: 1px solid var(--wa-color-neutral-15);
}

.mode-selector-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-xs) var(--wa-space-s);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
}

.mode-selector-label {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-weight: 600;
    flex-shrink: 0;
}

.mode-selector-input {
    flex: 1;
    max-width: 24rem;
}

/* Always show the focus outline on the primary target of the form (initial Approve,
   "Approve with changes" in edit mode, deny reason textarea).
   The `auto-focused` class is placed statically on these elements; the rules below
   render the outline whenever focus lands on them, regardless of how it got there
   (programmatic from Alt+Shift+M or component auto-focus, keyboard tab, or mouse
   click). Default :focus-visible would skip mouse and programmatic focus, which
   hides the indicator we want for these primary actions.
   We use :focus-within (not :focus) because wa-button / wa-textarea delegate focus
   to an element in their shadow DOM; the host doesn't carry :focus, but the browser
   keeps :focus-within accurate via activeElement. The first option card's rule lives
   in styles/option-cards.css. */
wa-button.auto-focused:focus-within::part(base),
wa-textarea.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.pending-request-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

.deny-reason-row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    justify-content: flex-end;
    align-items: center;
    width: 100%;
}

.deny-reason-input {
    flex: 1;
    min-width: min(20rem, calc(100vw - 2 * var(--wa-space-s)));
}

/* =========================================================================
   Ask User Question styles
   ========================================================================= */

.questions-container {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    overflow-y: auto;
    flex: 1;
    /* Never collapse to nothing: below this the card itself scrolls. */
    min-height: 6rem;
}

.question-block {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-s);
}

.question-header {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
}

.question-text {
    line-height: 1.4;
}

.question-select-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

/* The option cards (.question-options, .option-card*, .option-indicator*, .option-label)
   live in styles/option-cards.css, shared with the Codex question body. */

.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    line-height: 1.3;
}

.other-section {
    margin-top: var(--wa-space-3xs);
}

.other-toggle-link {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-brand-60);
    cursor: pointer;
    text-decoration: none;
}

.other-toggle-link:hover:not(.disabled) {
    text-decoration: underline;
}

.other-toggle-link.disabled {
    opacity: 0.6;
    cursor: not-allowed;
    pointer-events: none;
}

.other-input-row {
    margin-top: var(--wa-space-2xs);
}

.other-input {
    width: 100%;
}

/* Auto-grow with content up to 4 lines of text, then scroll. The max-height
   mirrors the inner textarea's block padding formula (wa-textarea compensates
   the line-height overshoot: padding-block - (1lh - 1em) / 2 per side).
   resize="auto" sets overflow-y: hidden, which would trap content past the
   cap — restore scrolling. */
.other-input::part(textarea) {
    max-height: calc(4lh + 2 * (var(--wa-form-control-padding-block) - (1lh - 1em) / 2));
    overflow-y: auto;
}
</style>
