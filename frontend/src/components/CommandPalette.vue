<script setup>
/**
 * CommandPalette — Modal command palette with search, keyboard navigation,
 * and nested sub-selection mode.
 *
 * Opens via `isOpen` from the command registry. Displays commands grouped
 * by category (root mode), filtered by fuzzy search (search mode), or
 * showing sub-items for a parent command (nested mode).
 *
 * Keyboard navigation:
 *   ArrowUp/Down — move selection
 *   Enter — execute or enter sub-level
 *   Escape — go back (nested → root) or close
 *   Home/End — first/last item
 *   PageUp/PageDown — jump ~8 items
 */

import { ref, computed, watch, nextTick, shallowRef } from 'vue'
import { useCommandRegistry } from '../composables/useCommandRegistry'
import { fuzzyMatch } from '../utils/fuzzyMatch'

const { isOpen, availableCommands, commandsByCategory, openPalette, closePalette } = useCommandRegistry()

const dialogRef = ref(null)
const searchInputRef = ref(null)
const listRef = ref(null)

const query = ref('')
const activeId = ref(null)
const parentCommand = shallowRef(null)

const PAGE_SIZE = 8

// ─── Dialog open/close driven by registry state ─────────────────────────

watch(isOpen, (open) => {
    if (!dialogRef.value) return
    if (open) {
        dialogRef.value.open = true
    } else {
        dialogRef.value.open = false
    }
})

// ─── Search results: fuzzy filtered + scored ─────────────────────────────

const searchResults = computed(() => {
    if (!query.value || parentCommand.value) return []
    const results = []
    for (const cmd of availableCommands.value) {
        const result = fuzzyMatch(query.value, cmd.label)
        if (result.match) {
            results.push({
                cmd,
                score: result.score,
                highlighted: highlightMatches(cmd.label, result.ranges),
            })
        }
    }
    results.sort((a, b) => b.score - a.score)
    return results
})

// ─── Nested items (when parentCommand is set) ────────────────────────────

const nestedResults = computed(() => {
    if (!parentCommand.value?.items) return []
    const items = parentCommand.value.items()
    if (!query.value) {
        // Always produce escaped HTML for safe v-html rendering
        return items.map((item) => ({ ...item, highlighted: escapeHtml(item.label) }))
    }
    // Filter by fuzzy match
    const results = []
    for (const item of items) {
        const result = fuzzyMatch(query.value, item.label)
        if (result.match) {
            results.push({
                ...item,
                score: result.score,
                highlighted: highlightMatches(item.label, result.ranges),
            })
        }
    }
    results.sort((a, b) => b.score - a.score)
    return results
})

// ─── Flat list of all currently visible items (for keyboard nav) ─────────

const visibleItems = computed(() => {
    if (parentCommand.value) return nestedResults.value
    if (query.value) return searchResults.value.map((r) => r.cmd)
    // Category mode: flat list of all commands
    const flat = []
    for (const group of commandsByCategory.value) {
        for (const cmd of group.commands) {
            flat.push(cmd)
        }
    }
    return flat
})

// ─── Auto-select first item when visible items change ────────────────────

function selectFirstItem() {
    const items = visibleItems.value
    activeId.value = items.length > 0 ? items[0].id : null
}

watch(visibleItems, selectFirstItem)

// ─── Dialog event handlers ───────────────────────────────────────────────

function onAfterShow() {
    selectFirstItem()
    searchInputRef.value?.focus()
}

function onHide() {
    // Reset state when dialog actually closes
    query.value = ''
    activeId.value = null
    parentCommand.value = null
    closePalette()
}

// ─── Command execution ──────────────────────────────────────────────────

/**
 * Execute an action after the dialog has fully closed.
 * This ensures the dialog doesn't steal focus from the action
 * (e.g., focus commands need the dialog gone first).
 *
 * wa-dialog internally does `setTimeout(() => trigger.focus())` BEFORE
 * dispatching wa-after-hide. So even with rAF in onAfterHide, the queued
 * setTimeout would restore focus to the trigger element and steal it.
 * We clear `originalTrigger` before closing so that restoration is a no-op,
 * then run our action after a setTimeout to stay after any dialog internals.
 */
let pendingAction = null

function executeAfterClose(action) {
    pendingAction = action
    // Neutralize wa-dialog's focus restoration (it checks trigger?.focus)
    if (dialogRef.value) {
        dialogRef.value.originalTrigger = null
    }
    close()
}

function onAfterHide() {
    if (pendingAction) {
        const action = pendingAction
        pendingAction = null
        // Run after any remaining dialog internals (setTimeout-based)
        setTimeout(() => action())
    }
}

function selectCommand(cmd) {
    if (cmd.items) {
        // Enter nested mode
        parentCommand.value = cmd
        query.value = ''
        // activeId will be set by the visibleItems watcher
        nextTick(() => searchInputRef.value?.focus())
    } else {
        executeAfterClose(() => cmd.action?.())
    }
}

function selectNestedItem(item) {
    executeAfterClose(() => item.action?.())
}

// ─── Open / close ────────────────────────────────────────────────────────

function open() {
    // Always go through openPalette() to bump contextVersion
    // (ensures when() guards are freshly evaluated)
    openPalette()
    // The watcher on isOpen will set dialogRef.value.open = true
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
    // onHide will handle state reset and closePalette()
}

function goBack() {
    if (parentCommand.value) {
        const parentId = parentCommand.value.id
        parentCommand.value = null
        query.value = ''
        nextTick(() => {
            activeId.value = parentId
            scrollIntoView(parentId)
            searchInputRef.value?.focus()
        })
    } else {
        close()
    }
}

// ─── Keyboard navigation ────────────────────────────────────────────────

function handleKeydown(e) {
    const items = visibleItems.value
    if (!items.length && !['Escape', 'ArrowLeft', 'Backspace'].includes(e.key)) return

    switch (e.key) {
        case 'ArrowDown': {
            e.preventDefault()
            const idx = items.findIndex((i) => i.id === activeId.value)
            const next = Math.min(idx + 1, items.length - 1)
            activeId.value = items[next].id
            scrollIntoView(items[next].id)
            break
        }
        case 'ArrowUp': {
            e.preventDefault()
            const idx = items.findIndex((i) => i.id === activeId.value)
            const prev = Math.max(idx - 1, 0)
            activeId.value = items[prev].id
            scrollIntoView(items[prev].id)
            break
        }
        case 'Home': {
            e.preventDefault()
            activeId.value = items[0].id
            scrollIntoView(items[0].id)
            break
        }
        case 'End': {
            e.preventDefault()
            activeId.value = items[items.length - 1].id
            scrollIntoView(items[items.length - 1].id)
            break
        }
        case 'PageDown': {
            e.preventDefault()
            const idx = items.findIndex((i) => i.id === activeId.value)
            const next = Math.min(idx + PAGE_SIZE, items.length - 1)
            activeId.value = items[next].id
            scrollIntoView(items[next].id)
            break
        }
        case 'PageUp': {
            e.preventDefault()
            const idx = items.findIndex((i) => i.id === activeId.value)
            const prev = Math.max(idx - PAGE_SIZE, 0)
            activeId.value = items[prev].id
            scrollIntoView(items[prev].id)
            break
        }
        case 'ArrowRight': {
            // Enter sub-menu if command has items (like Enter)
            if (parentCommand.value) break // already in nested mode
            const activeCmd = items.find((i) => i.id === activeId.value)
            if (activeCmd?.items) {
                e.preventDefault()
                selectCommand(activeCmd)
            }
            break
        }
        case 'ArrowLeft':
        case 'Backspace': {
            // Go back one level when query is empty (nested → root → close)
            if (!query.value) {
                e.preventDefault()
                goBack()
            }
            break
        }
        case 'Enter': {
            e.preventDefault()
            const active = items.find((i) => i.id === activeId.value)
            if (!active) break
            if (parentCommand.value) {
                selectNestedItem(active)
            } else {
                selectCommand(active)
            }
            break
        }
        case 'Escape': {
            e.preventDefault()
            e.stopPropagation()
            goBack()
            break
        }
    }
}

// ─── Scroll active item into view ────────────────────────────────────────

function scrollIntoView(id) {
    nextTick(() => {
        listRef.value?.querySelector(`[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' })
    })
}

// ─── Highlight matched characters ────────────────────────────────────────

function highlightMatches(text, ranges) {
    if (!ranges.length) return escapeHtml(text)
    let result = ''
    let lastIndex = 0
    for (const [start, end] of ranges) {
        result += escapeHtml(text.slice(lastIndex, start))
        result += '<mark>' + escapeHtml(text.slice(start, end + 1)) + '</mark>'
        lastIndex = end + 1
    }
    result += escapeHtml(text.slice(lastIndex))
    return result
}

function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog ref="dialogRef" without-header light-dismiss @wa-after-show="onAfterShow" @wa-hide="onHide" @wa-after-hide="onAfterHide">
        <div class="command-palette">
            <!-- Header with search -->
            <div class="palette-header">
                <span v-if="parentCommand" class="breadcrumb">
                    <wa-icon :name="parentCommand.icon" />
                    <span>{{ parentCommand.label }}</span>
                    <wa-icon name="chevron-right" />
                </span>
                <input
                    ref="searchInputRef"
                    type="text"
                    v-model="query"
                    :placeholder="parentCommand ? 'Filter...' : 'Type a command...'"
                    @keydown="handleKeydown"
                    autocomplete="off"
                    spellcheck="false"
                />
            </div>
            <wa-divider />
            <!-- Command list -->
            <div ref="listRef" class="palette-list">
                <!-- Root category mode -->
                <template v-if="!query && !parentCommand">
                    <template v-for="group in commandsByCategory" :key="group.key">
                        <div class="category-label">{{ group.label }}</div>
                        <div
                            v-for="cmd in group.commands"
                            :key="cmd.id"
                            class="command-item"
                            :class="{ active: cmd.id === activeId }"
                            :data-id="cmd.id"
                            @click="selectCommand(cmd)"
                            @pointerenter="activeId = cmd.id"
                        >
                            <wa-icon :name="cmd.icon" class="command-icon" />
                            <span class="command-label">{{ cmd.label }}</span>
                            <span v-if="cmd.toggled" class="command-toggle">
                                <wa-icon v-if="cmd.toggled()" name="check" />
                            </span>
                            <span v-if="cmd.items" class="command-chevron"><wa-icon name="chevron-right" /></span>
                        </div>
                    </template>
                </template>
                <!-- Search results mode -->
                <template v-else-if="!parentCommand">
                    <div
                        v-for="result in searchResults"
                        :key="result.cmd.id"
                        class="command-item"
                        :class="{ active: result.cmd.id === activeId }"
                        :data-id="result.cmd.id"
                        @click="selectCommand(result.cmd)"
                        @pointerenter="activeId = result.cmd.id"
                    >
                        <wa-icon :name="result.cmd.icon" class="command-icon" />
                        <span class="command-label" v-html="result.highlighted" />
                        <span v-if="result.cmd.toggled" class="command-toggle">
                            <wa-icon v-if="result.cmd.toggled()" name="check" />
                        </span>
                        <span v-if="result.cmd.items" class="command-chevron"><wa-icon name="chevron-right" /></span>
                    </div>
                </template>
                <!-- Nested mode -->
                <template v-else>
                    <div
                        v-for="item in nestedResults"
                        :key="item.id"
                        class="command-item"
                        :class="{ active: item.id === activeId }"
                        :data-id="item.id"
                        @click="selectNestedItem(item)"
                        @pointerenter="activeId = item.id"
                    >
                        <wa-icon v-if="item.active" name="check" class="command-icon active-check" />
                        <span v-else class="command-icon-spacer" />
                        <span class="command-label" v-html="item.highlighted" />
                    </div>
                </template>
                <!-- Empty state -->
                <div v-if="visibleItems.length === 0" class="palette-empty">No matching commands</div>
            </div>
        </div>
    </wa-dialog>
</template>

<style scoped>
wa-dialog {
    background: var(--wa-color-surface-default);
    --width: min(720px, calc(100vw - 1rem));
}

wa-dialog::part(body) {
    background: var(--wa-color-surface-default);
    padding: 0;
}
wa-dialog::part(overlay) {
    background: rgba(0, 0, 0, 0.4);
}

.palette-header {
    display: flex;
    align-items: center;
    padding: var(--wa-space-s) var(--wa-space-m);
    gap: var(--wa-space-s);
}
.palette-header input {
    flex: 1;
    border: none;
    outline: none;
    background: transparent;
    box-shadow: none;
    color: var(--wa-color-text-normal);
    font-size: var(--wa-font-size-m);
    font-family: inherit;
    min-width: 0;
}
.palette-header input::placeholder {
    color: var(--wa-color-text-muted);
}
.breadcrumb {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    color: var(--wa-color-text-muted);
    font-size: var(--wa-font-size-s);
    white-space: nowrap;
}

wa-divider {
    --spacing: 0;
}

.palette-list {
    max-height: min(400px, 60vh);
    overflow-y: auto;
    padding: var(--wa-space-xs) 0;
}

.category-label {
    padding: var(--wa-space-2xs) var(--wa-space-m);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
    user-select: none;
}
.category-label:not(:first-child) {
    margin-top: var(--wa-space-xs);
    border-top: 1px solid var(--wa-color-surface-border);
    padding-top: var(--wa-space-s);
}

.command-item {
    display: flex;
    align-items: center;
    padding: var(--wa-space-xs) var(--wa-space-m);
    cursor: pointer;
    gap: var(--wa-space-s);
    border-radius: var(--wa-border-radius-s);
    margin: 1px var(--wa-space-xs);
    user-select: none;
}
.command-item.active {
    background: var(--wa-color-surface-lowered);
}
.command-icon {
    width: 1.25em;
    text-align: center;
    color: var(--wa-color-text-muted);
    flex-shrink: 0;
    font-size: 0.9em;
}
.command-icon-spacer {
    width: 1.25em;
    flex-shrink: 0;
}
.command-label {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.command-label :deep(mark) {
    background: transparent;
    color: var(--wa-color-success-60);
    font-weight: 600;
    padding: 0;
}
.command-chevron,
.command-toggle {
    color: var(--wa-color-text-muted);
    flex-shrink: 0;
    font-size: 0.85em;
}
.active-check {
    color: var(--wa-color-success-60);
}

.palette-empty {
    padding: var(--wa-space-l) var(--wa-space-m);
    text-align: center;
    color: var(--wa-color-text-muted);
    font-style: italic;
}
</style>
