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
// Shared fields preserve the existing option-card interaction and style.
// Codex is single-select only (the native request_user_input tool never
// emits multi-select), so the multiSelect concept from that reference is
// dropped entirely here.

import { computed, ref, useId, watch } from 'vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import QuestionFields from '../../../../message/QuestionFields.vue'
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

const fieldsRef = ref(null)
const fieldQuestions = computed(() => questions.value.map((question, index) => ({
    ...question, index, title: question.question,
})))
const fieldAnswers = computed({
    get: () => {
        const answers = {}
        for (const [index, question] of questions.value.entries()) {
            if (otherActive.value[index] || !hasOptions(question)) {
                answers[index] = { kind: 'other', value: otherTexts.value[index] || '' }
            } else if (selections.value[index] != null) {
                answers[index] = { kind: 'option', value: selections.value[index] }
            }
        }
        return answers
    },
    set: answers => {
        resetState()
        for (const [index, answer] of Object.entries(answers)) {
            if (answer.kind === 'option') selections.value[index] = answer.value
            else {
                otherActive.value[index] = true
                otherTexts.value[index] = answer.value
            }
        }
    },
})
function hasOptions(question) {
    return Array.isArray(question.options) && question.options.length > 0
}
watch(() => props.pendingRequest?.request_id, () => {
    resetState()
    fieldsRef.value?.focusPrimary()
})

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
        <QuestionFields
            ref="fieldsRef"
            v-model="fieldAnswers"
            :questions="fieldQuestions"
            :disabled="isResponding"
        />

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

</style>
