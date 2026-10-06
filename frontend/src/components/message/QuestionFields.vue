<script setup>
// Shared single-select fields. Hosts own validation, persistence, and sending.
// Questions have stable index/title fields and {label, description} options.
import { nextTick, onMounted, ref, useId } from 'vue'
import { canStealFocus } from '../../utils/focusGuard'

const props = defineProps({
    questions: { type: Array, required: true },
    modelValue: { type: Object, default: () => ({}) },
    disabled: { type: Boolean, default: false },
    allowClear: { type: Boolean, default: false },
    autoFocus: { type: Boolean, default: true },
    allowOther: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const questionTextIdBase = useId()
const primaryRef = ref(null)
const textInputRefs = new Map()
const questionTextId = index => `${questionTextIdBase}-q${index}`
const hasOptions = question => Array.isArray(question.options) && question.options.length > 0
const isOptionSelected = (index, label) => props.modelValue[index]?.kind === 'option' && props.modelValue[index]?.value === label

function setPrimaryRef(el, isPrimary) {
    if (isPrimary) primaryRef.value = el
}
function setTextInputRef(index, el) {
    if (el) textInputRefs.set(index, el)
    else textInputRefs.delete(index)
}
function focusPrimary() {
    if (!props.autoFocus) return
    nextTick(() => {
        if (canStealFocus()) primaryRef.value?.focus()
    })
}
onMounted(focusPrimary)
defineExpose({ focusPrimary })

function updateAnswer(index, answer) {
    if (props.disabled) return
    const next = { ...props.modelValue }
    if (answer) next[index] = answer
    else delete next[index]
    emit('update:modelValue', next)
}
function selectOption(index, label) {
    updateAnswer(index, { kind: 'option', value: label })
}
function clearAnswer(index) {
    updateAnswer(index, null)
}
function toggleOther(index) {
    if (props.disabled) return
    if (props.modelValue[index]?.kind === 'other') {
        clearAnswer(index)
        return
    }
    updateAnswer(index, { kind: 'other', value: '' })
    nextTick(() => textInputRefs.get(index)?.focus())
}
function onOtherInput(index, event) {
    updateAnswer(index, { kind: 'other', value: event.target.value })
}
function handleOptionKeydown(event, index, option) {
    if (props.disabled) return
    const key = event.key
    if (key === 'Enter' || key === ' ') {
        // Keep both host submit shortcuts away from option-card selection.
        event.preventDefault()
        event.stopPropagation()
        selectOption(index, option.label)
        return
    }
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(key)) return
    const card = event.currentTarget
    const cards = Array.from(card.parentElement?.querySelectorAll(':scope > .option-card') || [])
    const position = cards.indexOf(card)
    if (position === -1) return
    event.preventDefault()
    const target = key === 'Home' ? 0 : key === 'End' ? cards.length - 1
        : (position + (key === 'ArrowLeft' ? -1 : 1) + cards.length) % cards.length
    cards[target].focus()
}
</script>

<template>
    <div class="questions-container">
        <div
            v-for="(question, position) in questions"
            :key="question.id ?? question.index"
            class="question-block"
            role="group"
            :aria-labelledby="questionTextId(question.index)"
        >
            <div v-if="question.header" class="question-header">{{ question.header }}</div>
            <div :id="questionTextId(question.index)" class="question-text">{{ question.title }}</div>
            <div v-if="hasOptions(question)" class="question-select-hint">Select one</div>

            <!-- Options as selectable cards. -->
            <div v-if="hasOptions(question)" class="question-options">
                <wa-card
                    v-for="(option, optionIndex) in question.options"
                    :key="option.label"
                    appearance="outlined"
                    class="option-card"
                    :class="{
                        selected: isOptionSelected(question.index, option.label),
                        disabled,
                        'auto-focused': autoFocus && position === 0 && optionIndex === 0,
                    }"
                    role="button"
                    :tabindex="disabled ? -1 : 0"
                    :aria-pressed="isOptionSelected(question.index, option.label) ? 'true' : 'false'"
                    :aria-disabled="disabled ? 'true' : null"
                    :ref="el => setPrimaryRef(el, position === 0 && optionIndex === 0)"
                    @click="!disabled && selectOption(question.index, option.label)"
                    @keydown="handleOptionKeydown($event, question.index, option)"
                >
                    <div class="option-card-content">
                        <span class="option-indicator option-indicator--radio" aria-hidden="true"></span>
                        <div class="option-card-text">
                            <span class="option-label">{{ option.label }}</span>
                            <span v-if="option.description" class="option-description">{{ option.description }}</span>
                        </div>
                    </div>
                </wa-card>
            </div>

            <!-- "Other" toggle link + text input (only when the question allows it). -->
            <div v-if="(allowOther || question.isOther) && hasOptions(question)" class="other-section">
                <a
                    href="#"
                    class="other-toggle-link"
                    :class="{ disabled }"
                    @click.prevent="!disabled && toggleOther(question.index)"
                >{{ modelValue[question.index]?.kind === 'other' ? 'Cancel other' : 'Other...' }}</a>
            </div>
            <div v-if="(allowOther || question.isOther) && hasOptions(question) && modelValue[question.index]?.kind === 'other'" class="other-input-row">
                <!-- Normal case (the native tool never sets isSecret): an
                     auto-growing wa-textarea, matching Claude verbatim. -->
                <wa-textarea
                    v-if="!question.isSecret"
                    :ref="el => setTextInputRef(question.index, el)"
                    :aria-label="question.title"
                    placeholder="Type your answer..."
                    size="small"
                    rows="1"
                    resize="auto"
                    class="other-input"
                    :value.prop="modelValue[question.index]?.value || ''"
                    :disabled="disabled"
                    @input="onOtherInput(question.index, $event)"
                ></wa-textarea>
                <!-- Defensive isSecret branch (never fires for the native
                     tool): a masked input — a textarea can't hide input. -->
                <wa-input
                    v-else
                    :ref="el => setTextInputRef(question.index, el)"
                    type="password"
                    :aria-label="question.title"
                    placeholder="Type your answer..."
                    size="small"
                    class="other-input"
                    :value.prop="modelValue[question.index]?.value || ''"
                    :disabled="disabled"
                    @input="onOtherInput(question.index, $event)"
                ></wa-input>
            </div>

            <!-- Pure free-text question (no options at all) — the input is the
                 only control, always visible (there's nothing to toggle). -->
            <div v-if="!hasOptions(question)" class="other-input-row">
                <wa-textarea
                    v-if="!question.isSecret"
                    :ref="el => setPrimaryRef(el, position === 0)"
                    class="other-input"
                    :class="{ 'auto-focused': autoFocus && position === 0 }"
                    :aria-label="question.title"
                    placeholder="Type your answer..."
                    size="small"
                    rows="1"
                    resize="auto"
                    :value.prop="modelValue[question.index]?.value || ''"
                    :disabled="disabled"
                    @input="onOtherInput(question.index, $event)"
                ></wa-textarea>
                <wa-input
                    v-else
                    :ref="el => setPrimaryRef(el, position === 0)"
                    class="other-input"
                    :class="{ 'auto-focused': autoFocus && position === 0 }"
                    type="password"
                    :aria-label="question.title"
                    placeholder="Type your answer..."
                    size="small"
                    :value.prop="modelValue[question.index]?.value || ''"
                    :disabled="disabled"
                    @input="onOtherInput(question.index, $event)"
                ></wa-input>
            </div>
            <a
                v-if="allowClear && modelValue[question.index]"
                href="#"
                class="other-toggle-link clear-answer"
                :class="{ disabled }"
                @click.prevent="clearAnswer(question.index)"
            >Clear answer</a>
        </div>
    </div>
</template>

<style scoped>
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

/* The option cards (.question-options, .option-card*, .option-indicator*, .option-label)
   live in styles/option-cards.css, shared with the Claude question body. */

/* Always show the focus outline on the primary target of the form (the lone
   text input for an options-less question; the option cards' focus rule lives in
   styles/option-cards.css), whether focus lands there via mouse click, Tab, or the
   programmatic auto-focus on mount / new request. Default :focus-visible would skip
   mouse and programmatic focus, which hides the indicator here. */
wa-textarea.auto-focused:focus-within::part(base),
wa-input.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
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
   cap — restore scrolling. Live for the normal-case wa-textarea fields; inert
   on the defensive isSecret wa-input fallback (it has no `textarea` part). */
.other-input::part(textarea) {
    max-height: calc(4lh + 2 * (var(--wa-form-control-padding-block) - (1lh - 1em) / 2));
    overflow-y: auto;
}
</style>
