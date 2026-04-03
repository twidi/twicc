<script setup>
// MessageSnippetsBar.vue - Displays message snippets as compact buttons below the textarea
// Visual style mirrors the snippets tab in TerminalExtraKeysBar.vue
import { computed } from 'vue'
import { useMessageSnippetsStore } from '../stores/messageSnippets'

const props = defineProps({
    projectId: {
        type: String,
        default: null,
    },
})

const emit = defineEmits(['snippet-press', 'manage-snippets'])

const messageSnippetsStore = useMessageSnippetsStore()

const snippets = computed(() => {
    if (!props.projectId) return messageSnippetsStore.snippets.global || []
    return messageSnippetsStore.getSnippetsForProject(props.projectId)
})

const hasSnippets = computed(() => snippets.value.length > 0)

/** Display text for a snippet: label if set, otherwise truncated text (10 chars + ellipsis). */
function snippetDisplayText(snippet) {
    if (snippet.label) return snippet.label
    const text = snippet.text || ''
    if (text.length <= 10) return text
    return text.slice(0, 10) + '…'
}

function handleSnippetClick(snippet) {
    emit('snippet-press', snippet)
}
</script>

<template>
    <div v-if="hasSnippets || messageSnippetsStore._initialized" class="message-snippets-bar">
        <template v-if="hasSnippets">
            <button
                v-for="(snippet, i) in snippets"
                :key="i"
                class="snippet-btn"
                :title="snippet.text"
                @click="handleSnippetClick(snippet)"
            >{{ snippetDisplayText(snippet) }}</button>
        </template>
        <span v-else class="empty-text">No snippets</span>
        <button
            class="snippet-btn manage-btn"
            @click="emit('manage-snippets')"
        >⚙ Manage</button>
    </div>
</template>

<style scoped>
/* ── Reset ── */
button {
    box-shadow: none !important;
    margin: 0;
}

/* ── Bar container ─────────────────────────────────────────────────── */
.message-snippets-bar {
    display: flex;
    gap: var(--wa-space-2xs);
    flex-wrap: wrap;
    align-items: center;
}

/* On mobile: single-line horizontal scroll */
@media (width < 640px) {
    .message-snippets-bar {
        flex-wrap: nowrap;
        overflow-x: auto;
        scrollbar-width: none; /* Firefox */
        -webkit-overflow-scrolling: touch;
    }
    .message-snippets-bar::-webkit-scrollbar {
        display: none; /* Chrome/Safari */
    }
}

/* Scroll shadow indicators — progressive enhancement (same pattern as SettingsPopover) */
@supports (container-type: scroll-state) {
    @media (width < 640px) {
        .message-snippets-bar {
            container-type: scroll-state;
        }

        .message-snippets-bar::before,
        .message-snippets-bar::after {
            --_shadow-color: color-mix(in srgb, var(--wa-color-text-normal) 12%, transparent);
            --_fade-size: var(--wa-space-s);
            content: '';
            display: block;
            flex-shrink: 0;
            position: sticky;
            width: var(--_fade-size);
            align-self: stretch;
            z-index: 2;
            pointer-events: none;
            opacity: 0;
            transition: opacity 0.2s ease;
        }

        .message-snippets-bar::before {
            order: -1;
            left: 0;
            margin-right: calc(-1 * var(--_fade-size));
            background: linear-gradient(to right, var(--_shadow-color), transparent);
        }

        .message-snippets-bar::after {
            order: 9999;
            right: 0;
            margin-left: calc(-1 * var(--_fade-size));
            background: linear-gradient(to left, var(--_shadow-color), transparent);
        }

        @container scroll-state(scrollable: left) {
            .message-snippets-bar::before {
                opacity: 1;
            }
        }

        @container scroll-state(scrollable: right) {
            .message-snippets-bar::after {
                opacity: 1;
            }
        }
    }
}

/* ── Snippet buttons (same visual style as TerminalExtraKeysBar) ──── */
.snippet-btn {
    background: var(--wa-color-surface-raised);
    border: 1px solid var(--wa-color-surface-border);
    color: var(--wa-color-text-normal);
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-xs);
    height: 1.75rem;
    border-radius: var(--wa-border-radius-s);
    cursor: pointer;
    transition: background-color 0.1s, border-color 0.1s, transform 0.1s;
    touch-action: manipulation;
    -webkit-user-select: none;
    user-select: none;
    line-height: 1;
    padding: 0 var(--wa-space-xs);
}

.snippet-btn:hover {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-hover));
}

.snippet-btn:active {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-active));
    transform: scale(0.95);
}

/* ── Empty text ──────────────────────────────────────────────────── */
.empty-text {
    font-size: var(--wa-font-size-xs);
    opacity: 0.4;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    align-self: center;
}

/* ── Manage button ────────────────────────────────────────────────── */
.snippet-btn.manage-btn {
    border-style: dashed;
    opacity: 0.6;
    font-size: var(--wa-font-size-xs);
}

.snippet-btn.manage-btn:hover {
    opacity: 0.9;
}
</style>
