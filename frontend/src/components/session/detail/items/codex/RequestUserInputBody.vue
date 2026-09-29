<script setup>
// RequestUserInputBody.vue (codex) — body sub-component for a
// ``toolRequestUserInput`` pending request (request_type ``ask_user_question``).
//
// Wire params (tool_input): { threadId, turnId, itemId, questions: [
//   { id, header, question, isOther, isSecret, options: [{label, description}] | null }
// ] }
//
// This is the same shape the MCP-approval fallback uses (options labelled
// "Allow" / "Allow for this session" / "Allow and don't ask me again" /
// "Cancel") — we render whatever labels/descriptions we're given, no
// special-casing.
//
// Self-contained: unlike the sibling approval bodies, this component owns
// its entire body including the action row (Dismiss / Submit).
//
// Rendering and keyboard interaction are a deliberate hard-align with the
// Claude equivalent (PendingRequestBody.vue's ask_user_question section) —
// an interim before a shared component, hence the duplicated markup/CSS.
// Codex is single-select only (the native request_user_input tool never
// emits multi-select), so the multiSelect concept from that reference is
// dropped entirely here.

import { computed, nextTick, onMounted, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import { canStealFocus } from '../../../../../utils/focusGuard'
import { usePendingRequestSubmitShortcut } from '../../../../../composables/usePendingRequestSubmitShortcut'
import { usePendingRequestDraft } from '../../../../../composables/usePendingRequestDraft'

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
    sessionId: { type: String, required: true },
})
const emit = defineEmits(['submit'])

const dismissButtonId = useId()
const submitButtonId = useId()

// Base id for the per-question text elements, referenced by each question
// group's aria-labelledby.
const questionTextIdBase = useId()
function questionTextId(qIndex) {
    return `${questionTextIdBase}-q${qIndex}`
}

// Wire params.
const questions = computed(() => {
    const qs = props.pendingRequest.tool_input?.questions
    return Array.isArray(qs) ? qs : []
})

// Per-question-index state, keyed by array index (questions have no stable
// position guarantee across requests, but request_id changes reset all of this).
const selections = ref({}) // idx -> selected option label
const otherTexts = ref({}) // idx -> free text
const otherActive = ref({}) // idx -> bool ("Other" is the active choice)

function resetState() {
    selections.value = {}
    otherTexts.value = {}
    otherActive.value = {}
}

// Template ref for the primary control (first option card of the first
// question, or its text input when that question has no options) —
// auto-focused on mount and whenever a new request takes over the slot.
const primaryRef = ref(null)
function setPrimaryRef(el, isPrimary) {
    if (isPrimary) primaryRef.value = el
}

// Template refs for the "Other" free-text inputs, keyed by question index —
// used to move focus into the field when its toggle link is clicked. Bare
// free-text questions (no options) don't register here: they have no
// toggle, the input is the primary control itself (see setPrimaryRef).
const textInputRefs = ref({})
function setTextInputRef(idx, el) {
    if (el) textInputRefs.value[idx] = el
}

function focusPrimary() {
    nextTick(() => {
        if (!canStealFocus()) return
        primaryRef.value?.focus()
    })
}

onMounted(focusPrimary)

// Reset per-question state and re-focus when a new request takes over the slot.
watch(() => props.pendingRequest?.request_id, () => {
    resetState()
    focusPrimary()
})

function hasOptions(question) {
    return Array.isArray(question.options) && question.options.length > 0
}

/**
 * Select an option for a question. Single-select only: replaces the
 * selection and clears any active "Other" text.
 */
function selectOption(questionIndex, label) {
    if (props.isResponding) return
    selections.value[questionIndex] = label
    otherActive.value[questionIndex] = false
    otherTexts.value[questionIndex] = ''
}

/**
 * Handle keyboard navigation and selection on option cards.
 * Enter/Space select; ArrowLeft/Right wrap-navigate within the question;
 * Home/End jump to first/last card.
 */
function handleOptionKeydown(event, qIndex, option) {
    if (props.isResponding) return

    const key = event.key

    if (key === 'Enter' || key === ' ') {
        event.preventDefault()
        selectOption(qIndex, option.label)
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

function isOptionSelected(questionIndex, label) {
    return selections.value[questionIndex] === label
}

/**
 * Toggle "Other" free-text mode for a question.
 * If already active, deactivates it and clears the text.
 * If not active, activates it and clears the selected option card.
 */
function toggleOther(questionIndex) {
    if (otherActive.value[questionIndex]) {
        otherActive.value[questionIndex] = false
        otherTexts.value[questionIndex] = ''
        return
    }
    otherActive.value[questionIndex] = true
    selections.value[questionIndex] = null
    // Focus the input after Vue renders it
    nextTick(() => {
        const input = textInputRefs.value[questionIndex]
        if (input) input.focus()
    })
}

function onOtherInput(questionIndex, event) {
    otherTexts.value[questionIndex] = event.target.value
}

// Resolved answer for a question: the free text when "Other" is active (or
// the question has no options at all — a bare free-text question),
// otherwise the selected option label. null means unanswered.
function getQuestionAnswer(questionIndex) {
    const question = questions.value[questionIndex]
    if (!question) return null
    if (otherActive.value[questionIndex] || !hasOptions(question)) {
        const text = (otherTexts.value[questionIndex] || '').trim()
        return text ? text : null
    }
    return selections.value[questionIndex] ?? null
}

const allAnswered = computed(() =>
    questions.value.length > 0 && questions.value.every((q, idx) => getQuestionAnswer(idx) !== null))

function submit() {
    if (props.isResponding || !allAnswered.value) return
    const answers = {}
    for (const [idx, q] of questions.value.entries()) {
        // The wire guarantees string question ids, but a pathological
        // payload could send a missing/non-string id (→ key "undefined",
        // silently overwriting any prior entry) — skip those defensively.
        if (typeof q.id !== 'string' || !q.id) continue
        answers[q.id] = { answers: [getQuestionAnswer(idx)] }
    }
    emit('submit', { tool_name: 'toolRequestUserInput', answers })
}

function dismiss() {
    if (props.isResponding) return
    // Empty answers map: Codex treats a missing answer as a cancel.
    emit('submit', { tool_name: 'toolRequestUserInput', answers: {} })
}

usePendingRequestSubmitShortcut((e) => {
    if (!allAnswered.value) return
    e.preventDefault()
    e.stopPropagation()
    submit()
}, () => props.isResponding)

// Persist the in-progress answers so a page reload doesn't lose them. Secret
// answers are never written to disk (the native tool never sets ``isSecret``,
// but the defensive branch exists in the template — so it exists here too).
usePendingRequestDraft({
    sessionId: () => props.sessionId,
    pendingRequest: () => props.pendingRequest,
    isResponding: () => props.isResponding,
    collect: () => {
        const texts = {}
        for (const [idx, text] of Object.entries(otherTexts.value)) {
            if (questions.value[idx]?.isSecret) continue
            texts[idx] = text
        }
        return {
            selections: { ...selections.value },
            otherTexts: texts,
            otherActive: { ...otherActive.value },
        }
    },
    apply: (state) => {
        selections.value = { ...state.selections }
        otherTexts.value = { ...state.otherTexts }
        otherActive.value = { ...state.otherActive }
    },
})
</script>

<template>
    <div class="request-user-input-body">
        <div class="questions-container">
            <div
                v-for="(question, qIndex) in questions"
                :key="question.id ?? qIndex"
                class="question-block"
                role="group"
                :aria-labelledby="questionTextId(qIndex)"
            >
                <div v-if="question.header" class="question-header">{{ question.header }}</div>
                <div :id="questionTextId(qIndex)" class="question-text">{{ question.question }}</div>
                <div v-if="hasOptions(question)" class="question-select-hint">Select one</div>

                <!-- Options as selectable cards. -->
                <div v-if="hasOptions(question)" class="question-options">
                    <wa-card
                        v-for="(option, optionIndex) in question.options"
                        :key="option.label"
                        appearance="outlined"
                        class="option-card"
                        :class="{
                            selected: isOptionSelected(qIndex, option.label),
                            disabled: isResponding,
                            'auto-focused': qIndex === 0 && optionIndex === 0,
                        }"
                        role="button"
                        :tabindex="isResponding ? -1 : 0"
                        :aria-pressed="isOptionSelected(qIndex, option.label) ? 'true' : 'false'"
                        :aria-disabled="isResponding ? 'true' : null"
                        :ref="el => setPrimaryRef(el, qIndex === 0 && optionIndex === 0)"
                        @click="!isResponding && selectOption(qIndex, option.label)"
                        @keydown="handleOptionKeydown($event, qIndex, option)"
                    >
                        <div class="option-card-content">
                            <span class="option-label">{{ option.label }}</span>
                            <span v-if="option.description" class="option-description">{{ option.description }}</span>
                        </div>
                    </wa-card>
                </div>

                <!-- "Other" toggle link + text input (only when the question allows it). -->
                <div v-if="question.isOther && hasOptions(question)" class="other-section">
                    <a
                        href="#"
                        class="other-toggle-link"
                        :class="{ disabled: isResponding }"
                        @click.prevent="!isResponding && toggleOther(qIndex)"
                    >{{ otherActive[qIndex] ? 'Cancel other' : 'Other...' }}</a>
                </div>
                <div v-if="question.isOther && hasOptions(question) && otherActive[qIndex]" class="other-input-row">
                    <!-- Normal case (the native tool never sets isSecret): an
                         auto-growing wa-textarea, matching Claude verbatim. -->
                    <wa-textarea
                        v-if="!question.isSecret"
                        :ref="el => setTextInputRef(qIndex, el)"
                        :aria-label="question.question"
                        placeholder="Type your answer..."
                        size="small"
                        rows="1"
                        resize="auto"
                        class="other-input"
                        :value.prop="otherTexts[qIndex] || ''"
                        :disabled="isResponding"
                        @input="onOtherInput(qIndex, $event)"
                    ></wa-textarea>
                    <!-- Defensive isSecret branch (never fires for the native
                         tool): a masked input — a textarea can't hide input. -->
                    <wa-input
                        v-else
                        :ref="el => setTextInputRef(qIndex, el)"
                        type="password"
                        :aria-label="question.question"
                        placeholder="Type your answer..."
                        size="small"
                        class="other-input"
                        :value.prop="otherTexts[qIndex] || ''"
                        :disabled="isResponding"
                        @input="onOtherInput(qIndex, $event)"
                    ></wa-input>
                </div>

                <!-- Pure free-text question (no options at all) — the input is the
                     only control, always visible (there's nothing to toggle). -->
                <div v-if="!hasOptions(question)" class="other-input-row">
                    <wa-textarea
                        v-if="!question.isSecret"
                        :ref="el => setPrimaryRef(el, qIndex === 0)"
                        class="other-input"
                        :class="{ 'auto-focused': qIndex === 0 }"
                        :aria-label="question.question"
                        placeholder="Type your answer..."
                        size="small"
                        rows="1"
                        resize="auto"
                        :value.prop="otherTexts[qIndex] || ''"
                        :disabled="isResponding"
                        @input="onOtherInput(qIndex, $event)"
                    ></wa-textarea>
                    <wa-input
                        v-else
                        :ref="el => setPrimaryRef(el, qIndex === 0)"
                        class="other-input"
                        :class="{ 'auto-focused': qIndex === 0 }"
                        type="password"
                        :aria-label="question.question"
                        placeholder="Type your answer..."
                        size="small"
                        :value.prop="otherTexts[qIndex] || ''"
                        :disabled="isResponding"
                        @input="onOtherInput(qIndex, $event)"
                    ></wa-input>
                </div>
            </div>
        </div>

        <div class="codex-pending-actions">
            <wa-button
                :id="dismissButtonId"
                variant="neutral"
                appearance="outlined"
                size="small"
                :disabled="isResponding"
                @click="dismiss"
            >
                <wa-icon slot="start" name="ban" variant="classic"></wa-icon>
                Dismiss
            </wa-button>
            <AppTooltip :for="dismissButtonId">Dismiss without answering — Codex treats it as cancelled.</AppTooltip>

            <wa-button
                :id="submitButtonId"
                variant="brand"
                size="small"
                :disabled="isResponding || !allAnswered"
                @click="submit"
            >
                <wa-icon slot="start" name="check" variant="classic"></wa-icon>
                Submit
            </wa-button>
            <AppTooltip :for="submitButtonId">Send your answers.</AppTooltip>
        </div>
    </div>
</template>

<style scoped>
.request-user-input-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    flex: 1;
    min-height: 0;
}

.codex-pending-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

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

/* Two-layer variable indirection: the `-base` layer carries the state
   colors (default vs .selected), and the derived layer is what's painted —
   hover lightens the derived layer FROM the current base, so hovering a
   selected card keeps its selected colors. */
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
    box-shadow: var(--depth-1);
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

.option-card:focus-visible {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

/* Always show the focus outline on the primary target of the form (first
   option card, or the lone text input for an options-less question),
   whether focus lands there via mouse click, Tab, or the programmatic
   auto-focus on mount / new request. Default :focus-visible would skip
   mouse and programmatic focus, which hides the indicator here. */
.option-card.auto-focused:focus-within {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

wa-textarea.auto-focused:focus-within::part(base),
wa-input.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
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

/* Auto-grow with content up to 4 lines of text, then scroll. The max-height
   mirrors the inner textarea's block padding formula (wa-textarea compensates
   the line-height overshoot: padding-block - (1lh - 1em) / 2 per side).
   resize="auto" sets overflow-y: hidden, which would trap content past the
   cap — restore scrolling. Live for the normal-case wa-textarea fields; inert
   on the defensive isSecret wa-input fallback (it has no `textarea` part). */
.other-input::part(textarea) {
    max-height: calc(4lh + 2 * (var(--wa-form-control-padding-block) - (1lh - 1em) / 2));
    overflow-y: auto;
}
</style>
