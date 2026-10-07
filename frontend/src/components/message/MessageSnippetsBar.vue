<script setup>
// MessageSnippetsBar.vue - Displays message snippets as compact buttons below the textarea
// Visual style mirrors the snippets tab in TerminalExtraKeysBar.vue
import { onBeforeUnmount, ref } from 'vue'
import GroupedSnippetEntries from '../ui/GroupedSnippetEntries.vue'
import { useMessageSnippetsStore } from '../../stores/messageSnippets'
import { useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { getActivationCharMetadata } from '../../utils/commandActivation'
import AppTooltip from '../ui/AppTooltip.vue'
import ProjectMark from '../project/ProjectMark.vue'

defineProps({
    context: { default: null },
    /** Pre-enriched snippets (with _disabled / _disabledReason from parent). */
    snippets: {
        type: Array,
        default: () => [],
    },
    /** Whether to show the message history button (hidden in draft mode). */
    showHistoryButton: {
        type: Boolean,
        default: false,
    },
    /**
     * Activation chars exposed by the current provider's command vocabulary.
     * The bar renders one button per char (icon + tooltip resolved from
     * ``commandActivation.js``); click emits ``open-command`` with the char.
     */
    activationChars: {
        type: Array,
        default: () => [],
    },
    /** Whether the command-picker buttons are enabled (disabled when textarea is not empty). */
    canOpenCommand: {
        type: Boolean,
        default: true,
    },
})

const emit = defineEmits([
    'snippet-press',
    'snippet-long-press',
    'snippet-disabled-press',
    'manage-snippets',
    'open-history',
    'open-command',
    'open-at',
])

// Lookup helper exposed to the template — keeps the v-for tidy.
function activationMeta(char) {
    return getActivationCharMetadata(char)
}

// Stable, CSS-class-safe id for the per-char button (used for the AppTooltip
// ``for`` binding when the button is disabled). The codepoint avoids any
// trouble with special chars like ``/`` or ``$`` in DOM ids.
function commandBtnDisabledId(char) {
    return `command-btn-disabled-${char.charCodeAt(0)}`
}

const groupedEntries = ref(null)
const messageSnippetsStore = useMessageSnippetsStore()
const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()

function snippetScopeInfo(snippet) {
    const scope = snippet._scope
    if (!scope) return null
    if (scope.startsWith('project:')) {
        const projectId = scope.slice(8)
        const project = dataStore.getProject(projectId)
        return { type: 'project', color: project?.color || null, projectId }
    }
    if (scope.startsWith('workspace:')) {
        const ws = workspacesStore.getWorkspaceById(scope.slice(10))
        return { type: 'workspace', color: ws?.color || null }
    }
    return null
}

/** Display text for a snippet: label if set, otherwise truncated text (10 chars + ellipsis). */
function snippetDisplayText(snippet) {
    if (snippet.label) return snippet.label
    const text = snippet.text || ''
    if (text.length <= 10) return text
    return text.slice(0, 10) + '…'
}

function handleSnippetClick(snippet) {
    groupedEntries.value?.close()
    if (snippet._disabled) {
        emit('snippet-disabled-press', snippet)
    } else {
        emit('snippet-press', snippet)
    }
}

// ── Touch long-press: insert + send in one gesture ──────────────────
// A tap fills the textarea and focuses it, which opens the on-screen keyboard.
// When the snippet IS the whole message, that resize is pure friction. A long
// press therefore sends straight away (see `snippet-long-press` in the parent).
// Only `touch*` events drive this, so pointer-only devices never see it.
const LONG_PRESS_DELAY = 500

let longPressTimer = null
let longPressTriggered = false

function onSnippetTouchStart(snippet, event) {
    if (event.touches.length !== 1 || snippet._disabled) return
    longPressTriggered = false
    longPressTimer = setTimeout(() => {
        longPressTriggered = true
        groupedEntries.value?.close()
        emit('snippet-long-press', snippet)
    }, LONG_PRESS_DELAY)
}

// The bar scrolls horizontally on a narrow composer — a swipe must not send.
function onSnippetTouchMove() {
    clearTimeout(longPressTimer)
    longPressTimer = null
}

function onSnippetTouchEnd(event) {
    clearTimeout(longPressTimer)
    longPressTimer = null
    // Cancel the synthetic click, otherwise the snippet is inserted a second time.
    if (longPressTriggered) {
        event.preventDefault()
    }
}
onBeforeUnmount(() => clearTimeout(longPressTimer))
</script>

<template>
    <div v-if="snippets.length > 0 || messageSnippetsStore._initialized" class="message-snippets-bar">
        <button
            v-if="showHistoryButton"
            class="snippet-btn history-btn"
            title="Browse previous messages"
            @click="emit('open-history')"
        >
            <wa-icon name="arrow-up"></wa-icon>
        </button>
        <template v-for="char in activationChars" :key="char">
            <template v-if="activationMeta(char)">
                <button
                    :id="canOpenCommand ? undefined : commandBtnDisabledId(char)"
                    class="snippet-btn command-btn"
                    :class="{ 'snippet-disabled': !canOpenCommand }"
                    :title="canOpenCommand ? activationMeta(char).tooltip : undefined"
                    @click="canOpenCommand && emit('open-command', char)"
                >
                    <wa-icon :name="activationMeta(char).icon" :style="activationMeta(char).iconStyle"></wa-icon>
                </button>
                <AppTooltip v-if="!canOpenCommand" :for="commandBtnDisabledId(char)">
                    Clear the message first to use a command
                </AppTooltip>
            </template>
        </template>
        <button
            class="snippet-btn at-btn"
            title="Insert a file path"
            @click="emit('open-at')"
        >
            <wa-icon name="at"></wa-icon>
        </button>
        <wa-divider orientation="vertical" class="history-divider"></wa-divider>
        <template v-if="snippets.length > 0">
            <GroupedSnippetEntries ref="groupedEntries" :entries="snippets" :context="context">
                <template #group="{ entry, triggerId, expanded }">
                    <button :id="triggerId" class="snippet-btn" :aria-expanded="expanded" aria-haspopup="dialog">
                        <ProjectMark
                            v-if="snippetScopeInfo(entry)?.type === 'project'"
                            class="snippet-scope-mark"
                            :icon-url="dataStore.resolvedProjectIcons[snippetScopeInfo(entry).projectId] || null"
                            :color="snippetScopeInfo(entry).color"
                        />
                        <wa-icon
                            v-else-if="snippetScopeInfo(entry)?.type === 'workspace'"
                            name="layer-group"
                            class="snippet-scope-icon"
                            :style="snippetScopeInfo(entry).color ? { color: snippetScopeInfo(entry).color } : null"
                        ></wa-icon>
                        {{ entry.label }}
                        <wa-icon name="chevron-up"></wa-icon>
                    </button>
                </template>
                <template #default="{ entry: snippet, itemId }">
                    <button
                        :id="snippet._disabled ? `disabled-msg-snippet-${itemId}` : undefined"
                        class="snippet-btn"
                        :class="{ 'snippet-disabled': snippet._disabled }"
                        :title="snippet.text"
                        @click="handleSnippetClick(snippet)"
                        @touchstart.passive="onSnippetTouchStart(snippet, $event)"
                        @touchmove.passive="onSnippetTouchMove"
                        @touchend="onSnippetTouchEnd"
                    >
                        <template v-if="snippetScopeInfo(snippet)?.type === 'project'">
                            <ProjectMark
                                class="snippet-scope-mark"
                                :icon-url="dataStore.resolvedProjectIcons[snippetScopeInfo(snippet).projectId] || null"
                                :color="snippetScopeInfo(snippet).color"
                            />
                        </template>
                        <template v-else-if="snippetScopeInfo(snippet)?.type === 'workspace'">
                            <wa-icon
                                name="layer-group"
                                class="snippet-scope-icon"
                                :style="snippetScopeInfo(snippet).color ? { color: snippetScopeInfo(snippet).color } : null"
                            ></wa-icon>
                        </template>
                        {{ snippetDisplayText(snippet) }}
                    </button>
                    <AppTooltip v-if="snippet._disabled" :for="`disabled-msg-snippet-${itemId}`">
                        {{ snippet._disabledReason }}
                    </AppTooltip>
                </template>
            </GroupedSnippetEntries>
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
    margin-bottom: var(--wa-space-xs);
}

/* On a narrow composer: single-line horizontal scroll. Reacts to the composer's own
   width (the message-input container), not the viewport. */
@container message-input (width < 40rem) {
    .message-snippets-bar {
        flex-wrap: nowrap;
        overflow-x: auto;
        scrollbar-width: none; /* Firefox */
        -webkit-overflow-scrolling: touch;
        /* overflow-x: auto also clips vertically: room for the hover lift of a chip,
           net zero on the layout. */
        padding-block-start: 1px;
        margin-block-start: -1px;
    }
    .message-snippets-bar::-webkit-scrollbar {
        display: none; /* Chrome/Safari */
    }
}

/* Scroll shadow indicators — progressive enhancement (same pattern as SettingsPopover) */
@supports (container-type: scroll-state) {
    @container message-input (width < 40rem) {
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
    transition: background-color 0.1s, border-color 0.1s,
        scale var(--motion-dur-1) var(--motion-ease),
        translate var(--motion-dur-2) var(--motion-ease-spring);
    touch-action: manipulation;
    -webkit-user-select: none;
    user-select: none;
    line-height: 1;
    padding: 0 var(--wa-space-xs);
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

/* Hover only where a real pointer exists. On touch the state sticks after the
   finger lifts, leaving the button visibly pressed until you tap elsewhere —
   very noticeable with the long-press-to-send gesture. */
@media (hover: hover) {
    .snippet-btn:hover {
        background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-hover));
        translate: 0 calc(-1px * var(--motion-amount));
    }
}

.snippet-btn:active {
    background: color-mix(in srgb, var(--wa-color-surface-raised), var(--wa-color-mix-active));
    scale: calc(1 - 0.05 * var(--motion-amount));
}

/* ── Disabled snippets ────────────────────────────────────────────── */
.snippet-btn.snippet-disabled {
    opacity: 0.35;
    cursor: not-allowed;
}

@media (hover: hover) {
    .snippet-btn.snippet-disabled:hover {
        background: var(--wa-color-surface-raised);
        scale: none;
        translate: none;
    }
}

.snippet-btn.snippet-disabled:active {
    scale: none;
}

/* ── Scope indicators ────────────────────────────────────────────── */
.snippet-scope-mark {
    --project-mark-size: 0.85em;
    --project-mark-icon-size: var(--wa-space-m);
    flex-shrink: 0;
}

.snippet-scope-icon {
    font-size: 0.7em;
    flex-shrink: 0;
}

/* ── Empty text ──────────────────────────────────────────────────── */
.empty-text {
    font-size: var(--wa-font-size-xs);
    opacity: 0.4;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    align-self: center;
}

/* ── Icon-only buttons (history, command, at) ────────────────────── */
.snippet-btn.history-btn,
.snippet-btn.command-btn,
.snippet-btn.at-btn {
    padding: 0 var(--wa-space-2xs);
}

.history-divider {
    align-self: stretch;
    --spacing: var(--wa-space-2xs);
    height: auto;
}

/* ── Manage button ────────────────────────────────────────────────── */
.snippet-btn.manage-btn {
    border-style: dashed;
    opacity: 0.6;
    font-size: var(--wa-font-size-xs);
}

@media (hover: hover) {
    .snippet-btn.manage-btn:hover {
        opacity: 0.9;
    }
}
</style>
