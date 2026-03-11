<script setup>
/**
 * SlashCommandPickerPopup - A popup for selecting slash commands triggered by /.
 *
 * Opens a wa-popup anchored to a given element, showing a filterable list
 * of available slash commands fetched from the backend API.
 *
 * Keyboard navigation follows the same two-handler pattern as FileTreePanel:
 * - The search input handles ArrowDown/PageDown to move focus into the list
 * - The list container handles all navigation keys (arrows, Home/End, Page, Enter, Escape)
 * - DOM focus physically moves between the search input and the list container
 *
 * Props:
 *   projectId: current project id
 *   anchorId: id of the element to anchor the popup to
 *
 * Events:
 *   select(commandText): emitted when a command is selected (e.g. "/commit " or "/plugin:command ")
 *   close(): emitted when the popup is closed without selection
 */

import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { apiFetch } from '../utils/api'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
    anchorId: {
        type: String,
        required: true,
    },
})

const emit = defineEmits(['select', 'close', 'filter-change'])

// ─── Popup state ──────────────────────────────────────────────────────────

const popupRef = ref(null)
const isOpen = ref(false)
const searchInputRef = ref(null)
const listRef = ref(null)

// ─── Data ─────────────────────────────────────────────────────────────────

const allCommands = ref([])
const loading = ref(false)
const error = ref(null)
const searchQuery = ref('')
const activeIndex = ref(0)

// Number of items to jump with PageUp/PageDown
const PAGE_SIZE = 10

// ─── Built-in commands (hardcoded Claude Code CLI commands) ───────────────

const BUILTIN_COMMANDS = [
    { name: 'compact', plugin_name: null, source: 'builtin', is_global: true, description: 'Clear conversation history but keep a summary in context', argument_hint: '[instructions for summarization]' },
    { name: 'cost', plugin_name: null, source: 'builtin', is_global: true, description: 'Show the cost of the current session', argument_hint: null },
    { name: 'context', plugin_name: null, source: 'builtin', is_global: true, description: 'Show the current context window usage', argument_hint: null },
    { name: 'init', plugin_name: null, source: 'builtin', is_global: true, description: 'Initialize a new CLAUDE.md file with codebase documentation', argument_hint: null },
    { name: 'loop', plugin_name: null, source: 'builtin', is_global: true, description: "Run a prompt or slash command on a recurring interval until the session ends (e.g. /loop 5m /foo, defaults to 10m)", argument_hint: '[interval] [command or prompt]' },
]

// ─── Display tag ──────────────────────────────────────────────────────────
// Builds the parenthesized tag shown next to the command name.
// Examples: (built-in) — (superpowers, project) — (global command) — (project skill)

function commandTag(cmd) {
    const scope = cmd.is_global ? 'global' : 'project'

    if (cmd.source === 'builtin') return 'built-in'

    if (cmd.plugin_name) {
        return `${cmd.plugin_name} plugin, ${scope}`
    }

    const kind = cmd.source === 'commands_dir' ? 'command' : 'skill'
    return `${scope} ${kind}`
}

// ─── Filtered commands ────────────────────────────────────────────────────

const filteredCommands = computed(() => {
    const query = searchQuery.value.trim().toLowerCase()
    if (!query) return allCommands.value
    return allCommands.value.filter(cmd => {
        if (cmd.name.toLowerCase().includes(query)) return true
        if (cmd.plugin_name && cmd.plugin_name.toLowerCase().includes(query)) return true
        return false
    })
})

// ─── API fetch ────────────────────────────────────────────────────────────

async function fetchCommands() {
    loading.value = true
    error.value = null
    try {
        const res = await apiFetch(`/api/projects/${props.projectId}/slash-commands/`)
        if (!res.ok) {
            const data = await res.json()
            error.value = data.error || `HTTP ${res.status}`
            allCommands.value = []
            return
        }
        const data = await res.json()
        const all = [...BUILTIN_COMMANDS, ...(data.commands || [])]
        all.sort((a, b) => a.name.localeCompare(b.name))
        allCommands.value = all
    } catch (err) {
        error.value = err.message
        allCommands.value = []
    } finally {
        loading.value = false
    }
}

// ─── Open / close ─────────────────────────────────────────────────────────

async function open() {
    if (isOpen.value) return

    searchQuery.value = ''
    activeIndex.value = 0
    isOpen.value = true

    await fetchCommands()

    // Wait for popup and input to render
    await nextTick()
    await nextTick()

    // Focus the search input
    focusSearchInput()
}

function close() {
    isOpen.value = false
    allCommands.value = []
    error.value = null
    searchQuery.value = ''
    emit('close')
}

// ─── Focus management ─────────────────────────────────────────────────────

function focusSearchInput() {
    try {
        searchInputRef.value?.focus()
    } catch {
        // wa-input.focus() can throw if the shadow DOM isn't ready yet.
    }
}

// ─── Search input handler ─────────────────────────────────────────────────

function onSearchInput(event) {
    const raw = event.target.value
    // Strip leading "/" — the user already typed it to open the popup
    searchQuery.value = raw.startsWith('/') ? raw.slice(1) : raw
}

// ─── Selection ────────────────────────────────────────────────────────────

function selectCommand(cmd) {
    let text
    if (cmd.plugin_name) {
        text = `/${cmd.plugin_name}:${cmd.name} `
    } else {
        text = `/${cmd.name} `
    }
    emit('select', text)
    close()
}

function selectActive() {
    const cmds = filteredCommands.value
    if (cmds.length > 0 && activeIndex.value < cmds.length) {
        selectCommand(cmds[activeIndex.value])
    }
}

// ─── Scroll active item into view ─────────────────────────────────────────

function scrollActiveIntoView() {
    nextTick(() => {
        const container = listRef.value
        if (!container) return
        const el = container.querySelector(`[data-index="${activeIndex.value}"]`)
        el?.scrollIntoView({ block: 'nearest' })
    })
}

// ─── Search input keyboard handler ────────────────────────────────────────
// Only handles keys that move focus out of the search input into the list.
// All other keys pass through for normal typing.

function handleSearchKeydown(event) {
    if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close()
        return
    }
    if (event.key === 'ArrowDown') {
        event.preventDefault()
        const count = filteredCommands.value.length
        if (count > 1) {
            // First item is already selected, move to second
            activeIndex.value = 1
            listRef.value?.focus()
            scrollActiveIntoView()
        } else if (count === 1) {
            listRef.value?.focus()
        }
        return
    }
    if (event.key === 'PageDown') {
        event.preventDefault()
        const count = filteredCommands.value.length
        if (count > 0) {
            activeIndex.value = Math.min(PAGE_SIZE, count - 1)
            listRef.value?.focus()
            scrollActiveIntoView()
        }
        return
    }
    if (event.key === 'Enter') {
        event.preventDefault()
        selectActive()
        return
    }
}

// ─── List keyboard handler ────────────────────────────────────────────────
// Handles all navigation when focus is in the list container.

function handleListKeydown(event) {
    const cmds = filteredCommands.value
    const count = cmds.length
    if (!count) return

    switch (event.key) {
        case 'ArrowDown': {
            event.preventDefault()
            const next = activeIndex.value + 1
            if (next < count) {
                activeIndex.value = next
                scrollActiveIntoView()
            }
            break
        }

        case 'ArrowUp': {
            event.preventDefault()
            if (activeIndex.value <= 0) {
                // First item → go back to search input, first item stays highlighted
                activeIndex.value = 0
                focusSearchInput()
            } else {
                activeIndex.value = activeIndex.value - 1
                scrollActiveIntoView()
            }
            break
        }

        case 'Home': {
            event.preventDefault()
            activeIndex.value = 0
            scrollActiveIntoView()
            break
        }

        case 'End': {
            event.preventDefault()
            activeIndex.value = count - 1
            scrollActiveIntoView()
            break
        }

        case 'PageDown': {
            event.preventDefault()
            activeIndex.value = Math.min(activeIndex.value + PAGE_SIZE, count - 1)
            scrollActiveIntoView()
            break
        }

        case 'PageUp': {
            event.preventDefault()
            if (activeIndex.value <= 0) {
                activeIndex.value = 0
                focusSearchInput()
            } else {
                activeIndex.value = Math.max(activeIndex.value - PAGE_SIZE, 0)
                scrollActiveIntoView()
            }
            break
        }

        case 'Enter': {
            event.preventDefault()
            selectActive()
            break
        }

        case 'Escape': {
            event.preventDefault()
            event.stopPropagation()
            close()
            break
        }

        default:
            // Any other key (letter, etc.) → go back to search input for typing
            if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
                focusSearchInput()
            }
            return
    }
}

// ─── Reset active index when search changes ───────────────────────────────

watch(searchQuery, (newVal) => {
    activeIndex.value = 0
    emit('filter-change', newVal)
})

// ─── Click outside to close ───────────────────────────────────────────────

function onDocumentClick(event) {
    if (!isOpen.value) return
    const popup = popupRef.value
    if (!popup) return
    if (popup.contains(event.target)) return
    close()
}

watch(isOpen, (open) => {
    if (open) {
        // Delay to avoid the opening click from immediately closing
        setTimeout(() => {
            document.addEventListener('click', onDocumentClick, true)
        }, 0)
    } else {
        document.removeEventListener('click', onDocumentClick, true)
    }
})

// Clean up document listener if component is unmounted while popup is open
onBeforeUnmount(() => {
    document.removeEventListener('click', onDocumentClick, true)
})

defineExpose({ open, close, isOpen })
</script>

<template>
    <wa-popup
        ref="popupRef"
        :anchor="anchorId"
        placement="top-start"
        :active="isOpen"
        :distance="4"
        flip
        shift
        shift-padding="8"
    >
        <div class="picker-panel">
            <!-- Search input -->
            <div class="picker-search">
                <wa-input
                    ref="searchInputRef"
                    :value="searchQuery"
                    placeholder="Filter commands..."
                    size="small"
                    with-clear
                    class="picker-search-input"
                    @input="onSearchInput"
                    @keydown="handleSearchKeydown"
                >
                    <wa-icon slot="start" name="magnifying-glass"></wa-icon>
                </wa-input>
            </div>

            <!-- Command list (focusable container for keyboard navigation) -->
            <div
                ref="listRef"
                class="picker-list"
                tabindex="0"
                @keydown="handleListKeydown"
            >
                <template v-if="loading">
                    <div class="picker-status">Loading...</div>
                </template>
                <template v-else-if="error">
                    <div class="picker-status picker-error">{{ error }}</div>
                </template>
                <template v-else-if="filteredCommands.length === 0">
                    <div class="picker-status">
                        {{ searchQuery ? 'No matching commands' : 'No commands available' }}
                    </div>
                </template>
                <template v-else>
                    <div
                        v-for="(cmd, index) in filteredCommands"
                        :key="(cmd.plugin_name || '') + ':' + cmd.name"
                        :data-index="index"
                        class="picker-item"
                        :class="{ active: index === activeIndex }"
                        @click="selectCommand(cmd)"
                        @mouseenter="activeIndex = index"
                    >
                        <div class="item-header">
                            <span class="cmd-name">/{{ cmd.name }}</span>
                            <span v-if="cmd.argument_hint" class="cmd-hint">{{ cmd.argument_hint }}</span>
                            <span class="cmd-tag">({{ commandTag(cmd) }})</span>
                        </div>
                        <div v-if="cmd.description" class="item-desc">{{ cmd.description }}</div>
                    </div>
                </template>
            </div>
        </div>
    </wa-popup>
</template>

<style scoped>
.picker-panel {
    width: min(40rem, calc(100vw - 1rem));
    max-height: min(25rem, 80vh);
    display: flex;
    flex-direction: column;
    background: var(--wa-color-surface-default);
    border: 1px solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
    box-shadow: var(--wa-shadow-l);
    overflow: hidden;
}

/* ─── Search ──────────────────────────────────────────────────────────── */

.picker-search {
    padding: var(--wa-space-2xs);
    border-bottom: 1px solid var(--wa-color-surface-border);
    flex-shrink: 0;
}

.picker-search-input {
    width: 100%;
}

/* ─── List ────────────────────────────────────────────────────────────── */

.picker-list {
    overflow-y: auto;
    flex: 1;
    min-height: 0;
    outline: none;
}

.picker-status {
    padding: var(--wa-space-m) var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    text-align: center;
}

.picker-error {
    color: var(--wa-color-status-danger-text);
}

/* ─── Item ────────────────────────────────────────────────────────────── */

.picker-item {
    padding: var(--wa-space-xs);
    cursor: pointer;
    line-height: 1.5;
    display: flex;
    flex-direction: column;
    row-gap: var(--wa-space-2xs);
}

.picker-item:hover {
    background: var(--wa-color-surface-raised);
}

.picker-item.active {
    background: var(--wa-color-surface-lowered);
}

.item-header {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    column-gap: var(--wa-space-xs);
    row-gap: 0;
}

.cmd-name {
    font-family: var(--wa-font-family-code);
    font-weight: 600;
    color: var(--wa-color-text-default);
    white-space: nowrap;
}

.cmd-hint {
    white-space: nowrap;
}

.cmd-tag {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    white-space: nowrap;
    margin-left: var(--wa-space-3xs);
}

.item-desc {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
