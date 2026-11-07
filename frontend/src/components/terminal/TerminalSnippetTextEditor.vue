<script setup>
// TerminalSnippetTextEditor.vue - Shared snippet text editing block (textarea, special chars, placeholders, options)
// Used by TerminalSnippetsDialog (snippet CRUD) and TerminalSnippetSendDialog (one-shot edit-before-send).
import { ref, nextTick } from 'vue'
import { PLACEHOLDERS } from '../../utils/snippetPlaceholders'

const props = defineProps({
    text: {
        type: String,
        required: true,
    },
    appendEnter: {
        type: Boolean,
        default: true,
    },
    openInNewTab: {
        type: Boolean,
        default: false,
    },
})

const emit = defineEmits(['update:text', 'update:appendEnter', 'update:openInNewTab'])

// ── Special characters (tedious to type on mobile) ──────────────────
const SPECIAL_CHARS = ['-', '/', '|', '~', '\\', '_', '*', '&', '.', '+', '↵']

// ── Refs ────────────────────────────────────────────────────────────
const textareaRef = ref(null)

// ── Textarea insertion helper ───────────────────────────────────────
function insertAtCursor(insertValue) {
    const textarea = textareaRef.value?.shadowRoot?.querySelector('textarea')
    const start = textarea?.selectionStart ?? props.text.length
    const end = textarea?.selectionEnd ?? props.text.length
    const current = props.text
    emit('update:text', current.slice(0, start) + insertValue + current.slice(end))
    const newPos = start + insertValue.length
    nextTick(() => {
        if (textarea) {
            textarea.focus()
            textarea.setSelectionRange(newPos, newPos)
        }
    })
}

// ── Special char insertion ──────────────────────────────────────────
function insertChar(char) {
    insertAtCursor(char === '↵' ? '\n' : char)
}

// ── Placeholder insertion ───────────────────────────────────────────
function insertPlaceholder(id) {
    insertAtCursor(`{${id}}`)
}

// ── Public API ──────────────────────────────────────────────────────
function focus() {
    const textarea = textareaRef.value?.shadowRoot?.querySelector('textarea')
    if (textarea) {
        textarea.focus()
        const len = props.text.length
        textarea.setSelectionRange(len, len)
    }
}

defineExpose({ focus })
</script>

<template>
    <div class="snippet-text-editor">
        <!-- Snippet text -->
        <div class="form-group">
            <label class="form-label">{{ $t('terminal.snippets.text.label') }}</label>
            <wa-textarea
                ref="textareaRef"
                :value="text"
                @input="emit('update:text', $event.target.value)"
                rows="3"
                :placeholder="$t('terminal.snippets.text.placeholder')"
                size="small"
                class="snippet-textarea"
            />
            <!-- Special character picker (for mobile convenience) -->
            <div class="char-picker-row">
                <button
                    v-for="char in SPECIAL_CHARS"
                    :key="char"
                    type="button"
                    class="picker-key"
                    @click="insertChar(char)"
                >{{ char }}</button>
            </div>

            <!-- Placeholder picker -->
            <p class="placeholder-hint">{{ $t('terminal.snippets.placeholders.hint') }}</p>
            <div class="placeholder-picker-row">
                <button
                    v-for="p in PLACEHOLDERS"
                    :key="p.id"
                    type="button"
                    class="picker-key placeholder-key"
                    @click="insertPlaceholder(p.id)"
                    :title="$t('terminal.snippets.placeholders.insert', { placeholder: `{${p.id}}` })"
                >{{ p.label }}</button>
            </div>
        </div>

        <!-- Options row (checkboxes + optional extra content via slot) -->
        <div class="editor-options-row">
            <wa-checkbox
                :checked="appendEnter"
                @change="emit('update:appendEnter', $event.target.checked)"
                size="small"
            >
                {{ $t('terminal.snippets.appendEnter') }}
            </wa-checkbox>

            <wa-checkbox
                :checked="openInNewTab"
                @change="emit('update:openInNewTab', $event.target.checked)"
                size="small"
            >
                {{ $t('terminal.snippets.openInNewTab') }}
            </wa-checkbox>

            <slot></slot>
        </div>
    </div>
</template>

<style scoped>
.snippet-text-editor {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    button {
        box-shadow: none;
        margin: 0;
    }
}

/* ── Form ─────────────────────────────────────────────────────────── */
.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.snippet-textarea::part(textarea) {
    font-family: var(--wa-font-family-code);
}

/* ── Special char picker ─────────────────────────────────────────── */
.char-picker-row {
    display: flex;
    gap: var(--wa-space-2xs);
    flex-wrap: wrap;
}

.picker-key {
    background: var(--wa-color-surface-raised);
    border: 1px solid var(--wa-color-surface-border);
    color: var(--wa-color-text-normal);
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
    height: 1.75rem;
    min-width: 2rem;
    padding: 0 var(--wa-space-2xs);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--wa-border-radius-s);
    cursor: pointer;
    transition: background-color 0.1s, border-color 0.1s;
    user-select: none;
    font-weight: normal;
    box-shadow: none;
    margin: 0;
}

.picker-key:hover {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-hover));
}

.picker-key:active {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-active));
    transform: scale(0.95);
}

/* ── Placeholder picker ──────────────────────────────────────────── */
.placeholder-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin: 0;
}

.placeholder-picker-row {
    display: flex;
    gap: var(--wa-space-2xs);
    flex-wrap: wrap;
}

.placeholder-key {
    font-family: var(--wa-font-family-sans) !important;
    font-size: var(--wa-font-size-s) !important;
    padding: 0 var(--wa-space-xs) !important;
    border-color: var(--wa-color-brand-border-quiet) !important;
    color: var(--wa-color-brand-on-quiet) !important;
}

.placeholder-key:hover {
    background: var(--wa-color-brand-fill-quiet) !important;
}

/* ── Options row ─────────────────────────────────────────────────── */
.editor-options-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    flex-wrap: wrap;
}
</style>
