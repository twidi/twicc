<script setup>
// PendingRequestForm.vue - Form for Claude's pending requests.
//
// Handles two request types:
// - tool_approval: Shows tool name, formatted parameters, and Approve/Deny buttons
// - ask_user_question: Shows questions with selectable options and an "Other" free-text input

import { ref, computed, reactive, watch, nextTick } from 'vue'
import { useDataStore } from '../stores/data'
import JsonHumanView from './JsonHumanView.vue'

// Per-tool overrides for JsonHumanView display types.
// Only keys that need an override (not auto-detected) are listed.
const TOOL_OVERRIDES = {
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
}

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    pendingRequest: {
        type: Object,
        required: true
    }
})

const store = useDataStore()

// Whether a response has been sent and we're waiting for the store to clear the pending request
const isResponding = ref(false)

// ============================================================================
// Tool approval state
// ============================================================================

// Deny reason text (optional)
const denyReason = ref('')

// Whether the deny reason input is shown
const showDenyReason = ref(false)

// Template ref for the deny reason input
const denyReasonInputRef = ref(null)

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

// Reset state when the pending request changes (e.g., a new one arrives after the previous was resolved)
watch(() => props.pendingRequest?.request_id, () => {
    isResponding.value = false
    // Tool approval state
    denyReason.value = ''
    showDenyReason.value = false
    // Ask user question state
    Object.keys(questionSelections).forEach(k => delete questionSelections[k])
    Object.keys(otherTexts).forEach(k => delete otherTexts[k])
    Object.keys(otherActive).forEach(k => delete otherActive[k])
})

// ============================================================================
// Tool approval computed
// ============================================================================

// Tool name (raw, used for override lookup)
const toolName = computed(() => props.pendingRequest.tool_name || 'Unknown tool')

// Tool name formatted for display (underscores → spaces, collapse multiple)
const toolNameDisplay = computed(() => toolName.value.replace(/_+/g, ' '))

// Tool input data
const toolInput = computed(() => props.pendingRequest.tool_input || {})

// Overrides for JsonHumanView based on tool name
const toolOverrides = computed(() => TOOL_OVERRIDES[toolName.value] || {})

// ============================================================================
// Ask user question computed
// ============================================================================

// The questions array from the pending request
const questions = computed(() => toolInput.value.questions || [])

// Whether the submit button should be enabled (at least one answer per question)
const canSubmitQuestions = computed(() => {
    for (let i = 0; i < questions.value.length; i++) {
        if (!getQuestionAnswer(i)) return false
    }
    return questions.value.length > 0
})

// ============================================================================
// Tool approval handlers
// ============================================================================

/**
 * Handle approve action.
 * Sends the tool approval with the original input data.
 */
function handleApprove() {
    if (isResponding.value) return
    isResponding.value = true

    store.respondToPendingRequest(props.sessionId, props.pendingRequest.request_id, {
        request_type: 'tool_approval',
        decision: 'allow',
        updated_input: toolInput.value,
    })
}

/**
 * Handle deny action.
 * Sends the tool denial with an optional reason message.
 */
function handleDeny() {
    if (isResponding.value) return

    // If deny reason input is not shown yet, show it and focus
    if (!showDenyReason.value) {
        showDenyReason.value = true
        nextTick(() => {
            denyReasonInputRef.value?.focus()
        })
        return
    }

    isResponding.value = true

    const message = denyReason.value.trim() || 'User denied this action'
    store.respondToPendingRequest(props.sessionId, props.pendingRequest.request_id, {
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
}

/**
 * Handle keyboard shortcut in deny reason textarea.
 * Cmd/Ctrl+Enter submits the deny. Escape cancels.
 */
function onDenyReasonKeydown(event) {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        handleDeny()
    }
    if (event.key === 'Escape') {
        event.preventDefault()
        cancelDeny()
    }
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
 * Submit all question answers.
 * Builds the answers map (question text -> answer value) and sends the response.
 */
function handleSubmitQuestions() {
    if (isResponding.value || !canSubmitQuestions.value) return
    isResponding.value = true

    const answers = {}
    for (let i = 0; i < questions.value.length; i++) {
        const question = questions.value[i]
        answers[question.question] = getQuestionAnswer(i)
    }

    store.respondToPendingRequest(props.sessionId, props.pendingRequest.request_id, {
        request_type: 'ask_user_question',
        answers,
    })
}
</script>

<template>
    <div class="pending-request-form">
        <!-- ================================================================ -->
        <!-- Tool Approval Variant -->
        <!-- ================================================================ -->
        <template v-if="requestType === 'tool_approval'">
            <!-- Tool info header -->
            <div class="pending-request-header">
                <wa-icon name="shield-halved" class="pending-request-icon"></wa-icon>
                <span class="pending-request-title">Tool approval requested</span>
            </div>

            <!-- Tool details -->
            <div class="pending-request-details">
                <div class="tool-name-badge">
                    <wa-badge variant="neutral">{{ toolNameDisplay }}</wa-badge>
                </div>

                <JsonHumanView :value="toolInput" :overrides="toolOverrides" />
            </div>

            <!-- Action buttons -->
            <div class="pending-request-actions">
                <template v-if="!showDenyReason">
                    <wa-button
                        variant="danger"
                        appearance="outlined"
                        size="small"
                        :disabled="isResponding"
                        @click="handleDeny"
                    >
                        <wa-spinner v-if="isResponding" slot="prefix"></wa-spinner>
                        <wa-icon v-else name="xmark" variant="classic" slot="prefix"></wa-icon>
                        Deny
                    </wa-button>
                    <wa-button
                        variant="brand"
                        size="small"
                        :disabled="isResponding"
                        @click="handleApprove"
                    >
                        <wa-spinner v-if="isResponding" slot="prefix"></wa-spinner>
                        <wa-icon v-else name="check" variant="classic" slot="prefix"></wa-icon>
                        Approve
                    </wa-button>
                </template>
                <template v-else>
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
                            class="deny-reason-input"
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
                            <wa-spinner v-if="isResponding" slot="prefix"></wa-spinner>
                            <wa-icon v-else name="xmark" variant="classic" slot="prefix"></wa-icon>
                            Deny
                        </wa-button>
                    </div>
                </template>
            </div>
        </template>

        <!-- ================================================================ -->
        <!-- Ask User Question Variant -->
        <!-- ================================================================ -->
        <template v-else-if="requestType === 'ask_user_question'">
            <!-- Header -->
            <div class="pending-request-header">
                <wa-icon name="circle-question" class="pending-request-icon question-icon"></wa-icon>
                <span class="pending-request-title">Claude needs your input</span>
            </div>

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
                            v-for="option in question.options"
                            :key="option.label"
                            appearance="outlined"
                            class="option-card"
                            :class="{ selected: isOptionSelected(qIndex, option.label, question.multiSelect), disabled: isResponding }"
                            @click="!isResponding && selectOption(qIndex, option.label, question.multiSelect)"
                        >
                            <div class="option-card-content">
                                <span class="option-label">{{ option.label }}</span>
                                <span v-if="option.description" class="option-description">{{ option.description }}</span>
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
                        <wa-input
                            :ref="el => { if (el) otherInputRefs[qIndex] = el }"
                            placeholder="Type your answer..."
                            size="small"
                            :value.prop="otherTexts[qIndex] || ''"
                            @input="onOtherInput(qIndex, $event)"
                            class="other-input"
                        ></wa-input>
                    </div>
                </div>
            </div>

            <!-- Submit button -->
            <div class="pending-request-actions">
                <wa-button
                    variant="brand"
                    size="small"
                    :disabled="isResponding || !canSubmitQuestions"
                    @click="handleSubmitQuestions"
                >
                    <wa-spinner v-if="isResponding" slot="prefix"></wa-spinner>
                    <wa-icon v-else name="paper-plane" variant="classic" slot="prefix"></wa-icon>
                    Submit
                </wa-button>
            </div>
        </template>
    </div>
</template>

<style scoped>
.pending-request-form {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    padding: var(--wa-space-s);
    background: var(--main-header-footer-bg-color);
    max-height: 70vh;
}

.pending-request-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-warning-60);
    font-weight: 600;
}

.question-icon {
    color: var(--wa-color-primary-60);
}

.pending-request-details {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-s);
    overflow-y: auto;
    flex: 1;
    min-height: 0;
}

.tool-name-badge {
    margin-bottom: var(--wa-space-2xs);
}

.pending-request-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

.deny-reason-row {
    display: flex;
    gap: var(--wa-space-s);
    align-items: center;
    width: 100%;
}

.deny-reason-input {
    flex: 1;
    min-width: 0;
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
    min-height: 0;
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

.question-options {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    margin-top: var(--wa-space-2xs);
}

.option-card {
    flex: 1 1 0;
    min-width: min-content;
    max-width: 20rem;
    cursor: pointer;
    transition: border-color 0.15s, background-color 0.15s;
    --spacing: var(--wa-space-m);

    --border-color-base: var(--wa-color-surface-border);
    --background-color-base: var(--wa-color-surface-raised);
    --border-color: var(--border-color-base);
    --background-color: var(--background-color-base);

    border-color: var(--border-color);
    background: var(--background-color);
    box-shadow: var(--wa-shadow-offset-x-s) var(--wa-shadow-offset-y-s) 0 0 var(--border-color);
    &:hover {
        /* use new css color syntax to make border-color and background color 10% lighter */
        --border-color: oklch(from var(--border-color-base)calc(l + 0.025) c h);
        --background-color: oklch(from var(--background-color-base)calc(l + 0.025) c h);
    }
}

.option-card.selected {
    --border-color-base: var(--wa-color-border-normal);
    --background-color-base: var(--wa-color-fill-normal);
}

.option-card.disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

.option-card-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}

.option-label {
    font-weight: 600;
    color: var(--wa-color-text);
    line-height: 1.4;
}

.option-description {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    line-height: 1.3;
}

.other-section {
    margin-top: var(--wa-space-3xs);
}

.other-toggle-link {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-primary-60);
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
</style>
