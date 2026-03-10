<script setup>
/**
 * PromptHistoryPickerPopup - A popup for browsing and selecting past user messages.
 *
 * Opens a wa-popup anchored to a given element, showing a filterable list
 * of previous user messages from the current session in chronological order
 * (oldest at top, newest at bottom).
 *
 * Each item has two action buttons:
 * - Replace: replaces the current textarea content
 * - Insert: inserts at the current cursor position
 *
 * Keyboard navigation:
 * - ArrowUp/ArrowDown: navigate items
 * - Enter: insert at cursor (default action, insert button is pre-highlighted)
 * - Tab: focus replace button, Tab again returns to list
 * - Escape: close popup
 *
 * Props:
 *   sessionId: current session id
 *   anchorId: id of the element to anchor the popup to
 *
 * Events:
 *   replace(text): emitted when "replace" action is chosen
 *   insert(text): emitted when "insert" action is chosen
 *   close(): emitted when the popup is closed without selection
 */

import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { useDataStore } from '../stores/data'
import { getParsedContent } from '../utils/parsedContent'

const props = defineProps({
    sessionId: {
        type: String,
        required: true,
    },
    anchorId: {
        type: String,
        required: true,
    },
})

const emit = defineEmits(['replace', 'insert', 'close'])

const store = useDataStore()

// ─── Popup state ──────────────────────────────────────────────────────────

const popupRef = ref(null)
const isOpen = ref(false)
const searchInputRef = ref(null)
const listRef = ref(null)

// ─── Data ─────────────────────────────────────────────────────────────────

const allMessages = ref([])
const searchQuery = ref('')
const activeIndex = ref(0)
// Which element has focus within the active item: 'list' | 'replace' | 'insert'
const focusTarget = ref('list')

// Number of items to jump with PageUp/PageDown
const PAGE_SIZE = 10

// ─── Build message list from session items (chronological order) ──────────

function loadMessages() {
    const items = store.getSessionItems(props.sessionId)
    const messages = []
    for (let i = 0; i < items.length; i++) {
        if (items[i].kind !== 'user_message') continue
        const parsed = getParsedContent(items[i])
        const content = parsed?.message?.content
        if (!content) continue
        let text = ''
        if (typeof content === 'string') {
            text = content
        } else if (Array.isArray(content)) {
            const textBlock = content.find(b => b.type === 'text')
            if (textBlock?.text) text = textBlock.text
        }
        if (text) messages.push(text)
    }
    allMessages.value = messages
}

// ─── Filtered messages ────────────────────────────────────────────────────

const filteredMessages = computed(() => {
    const query = searchQuery.value.trim().toLowerCase()
    if (!query) return allMessages.value
    return allMessages.value.filter(msg => msg.toLowerCase().includes(query))
})

// ─── Open / close ─────────────────────────────────────────────────────────

async function open() {
    if (isOpen.value) {
        // Already open: toggle focus to search input
        focusSearchInput()
        return
    }

    searchQuery.value = ''
    isOpen.value = true
    focusTarget.value = 'list'

    loadMessages()

    // Default active index to the last (most recent) item
    activeIndex.value = Math.max(0, allMessages.value.length - 1)

    // Wait for popup and input to render
    await nextTick()
    await nextTick()

    // Focus the list for immediate keyboard navigation
    listRef.value?.focus()

    // Scroll to the bottom so the most recent message is visible
    scrollActiveIntoView()
}

/**
 * Focus the list container (called from outside via Alt+PageDown).
 */
function focusList() {
    if (!isOpen.value) return
    focusTarget.value = 'list'
    listRef.value?.focus()
}

function close() {
    isOpen.value = false
    allMessages.value = []
    searchQuery.value = ''
    emit('close')
}

// ─── Focus management ─────────────────────────────────────────────────────

function focusSearchInput() {
    focusTarget.value = 'list'
    try {
        searchInputRef.value?.focus()
    } catch {
        // wa-input.focus() can throw if the shadow DOM isn't ready yet.
    }
}

function focusActionButton(action) {
    focusTarget.value = action
    // Focus synchronously — no nextTick needed since the buttons are already rendered
    const container = listRef.value
    if (!container) return
    const itemEl = container.querySelector(`[data-index="${activeIndex.value}"]`)
    const btn = itemEl?.querySelector(`.action-${action}`)
    if (btn) {
        btn.tabIndex = 0
        btn.focus()
    }
}

// ─── Actions ──────────────────────────────────────────────────────────────

function replaceMessage(text) {
    emit('replace', text)
    close()
}

function insertMessage(text) {
    emit('insert', text)
    close()
}

function replaceActive() {
    const msgs = filteredMessages.value
    if (msgs.length > 0 && activeIndex.value < msgs.length) {
        replaceMessage(msgs[activeIndex.value])
    }
}

function insertActive() {
    const msgs = filteredMessages.value
    if (msgs.length > 0 && activeIndex.value < msgs.length) {
        insertMessage(msgs[activeIndex.value])
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

function handleSearchKeydown(event) {
    if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close()
        return
    }
    // Alt+PageDown: switch focus to list
    if (event.altKey && event.key === 'PageDown') {
        event.preventDefault()
        focusTarget.value = 'list'
        listRef.value?.focus()
        return
    }
    if (event.key === 'ArrowDown') {
        event.preventDefault()
        const count = filteredMessages.value.length
        if (count > 0) {
            // Move to next item if possible, otherwise stay on current
            if (activeIndex.value + 1 < count) {
                activeIndex.value++
            }
            focusTarget.value = 'list'
            listRef.value?.focus()
            scrollActiveIntoView()
        }
        return
    }
    if (event.key === 'PageDown') {
        event.preventDefault()
        const count = filteredMessages.value.length
        if (count > 0) {
            activeIndex.value = Math.min(activeIndex.value + PAGE_SIZE, count - 1)
            focusTarget.value = 'list'
            listRef.value?.focus()
            scrollActiveIntoView()
        }
        return
    }
    if (event.key === 'Enter') {
        event.preventDefault()
        insertActive()
        return
    }
}

// ─── Action button keyboard handler ───────────────────────────────────────

function handleActionKeydown(event, action) {
    // Stop propagation for all handled keys to prevent the list handler
    // from re-processing the event (buttons are inside the list container).
    if (event.key === 'Enter') {
        // Let the native click fire on the button, but stop propagation
        // to prevent the list handler from calling insertActive()
        event.stopPropagation()
        return
    }
    if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        close()
        return
    }
    if (event.key === 'Tab') {
        event.preventDefault()
        event.stopPropagation()
        // Cycle: list (insert pre-selected) → replace → list
        // If somehow on insert button, go to replace; otherwise back to list
        if (action === 'insert') {
            focusActionButton('replace')
        } else {
            // Back to list navigation (insert becomes pre-selected again)
            focusTarget.value = 'list'
            listRef.value?.focus()
        }
        return
    }
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
        event.preventDefault()
        event.stopPropagation()
        // Go back to list navigation mode
        focusTarget.value = 'list'
        listRef.value?.focus()
        if (event.key === 'ArrowUp' && activeIndex.value > 0) {
            activeIndex.value--
        } else if (event.key === 'ArrowDown' && activeIndex.value < filteredMessages.value.length - 1) {
            activeIndex.value++
        }
        scrollActiveIntoView()
        return
    }
}

// ─── List keyboard handler ────────────────────────────────────────────────

function handleListKeydown(event) {
    // Alt+PageUp: switch focus to search input
    if (event.altKey && event.key === 'PageUp') {
        event.preventDefault()
        focusSearchInput()
        return
    }

    const msgs = filteredMessages.value
    const count = msgs.length
    if (!count) return

    switch (event.key) {
        case 'Tab': {
            // Move focus to the replace button — insert is already pre-selected
            // (Enter on the list triggers insert, Tab gives access to replace)
            event.preventDefault()
            focusActionButton('replace')
            break
        }

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
            insertActive()
            break
        }

        case 'Escape': {
            event.preventDefault()
            event.stopPropagation()
            close()
            break
        }

        default:
            if (event.key.length === 1 && !event.ctrlKey && !event.metaKey) {
                focusSearchInput()
            }
            return
    }
}

// ─── Reset active index when search changes ───────────────────────────────

watch(searchQuery, () => {
    // On search change, select the last (most recent) matching result
    nextTick(() => {
        activeIndex.value = Math.max(0, filteredMessages.value.length - 1)
    })
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
        setTimeout(() => {
            document.addEventListener('click', onDocumentClick, true)
        }, 0)
    } else {
        document.removeEventListener('click', onDocumentClick, true)
    }
})

onBeforeUnmount(() => {
    document.removeEventListener('click', onDocumentClick, true)
})

defineExpose({ open, close, isOpen, focusList })
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
                    placeholder="Filter messages..."
                    size="small"
                    with-clear
                    class="picker-search-input"
                    @input="searchQuery = $event.target.value"
                    @keydown="handleSearchKeydown"
                >
                    <wa-icon slot="start" name="magnifying-glass"></wa-icon>
                </wa-input>
            </div>

            <!-- Message list (focusable container for keyboard navigation) -->
            <div
                ref="listRef"
                class="picker-list"
                tabindex="0"
                @keydown="handleListKeydown"
            >
                <template v-if="filteredMessages.length === 0">
                    <div class="picker-status">
                        {{ searchQuery ? 'No matching messages' : 'No messages in this session' }}
                    </div>
                </template>
                <template v-else>
                    <div
                        v-for="(msg, index) in filteredMessages"
                        :key="index"
                        :data-index="index"
                        class="picker-item"
                        :class="{ active: index === activeIndex }"
                        @mouseenter="activeIndex = index"
                    >
                        <div class="item-text">{{ msg }}</div>
                        <div class="item-actions" :class="{ visible: index === activeIndex }">
                            <button
                                class="action-btn action-insert"
                                :class="{ 'pre-selected': index === activeIndex && focusTarget === 'list' }"
                                title="Insert (at cursor position) — Enter"
                                tabindex="-1"
                                @click.stop="insertMessage(msg)"
                                @keydown="handleActionKeydown($event, 'insert')"
                            >
                                <wa-icon name="right-to-bracket"></wa-icon>
                            </button>
                            <button
                                class="action-btn action-replace"
                                title="Replace (overwrite current text) — Tab+Enter"
                                tabindex="-1"
                                @click.stop="replaceMessage(msg)"
                                @keydown="handleActionKeydown($event, 'replace')"
                            >
                                <wa-icon name="right-left"></wa-icon>
                            </button>
                        </div>
                    </div>
                </template>
            </div>
        </div>
    </wa-popup>
</template>

<style scoped>
.picker-panel {
    width: min(40rem, calc(100vw - 1rem));
    max-height: min(30rem, 80vh);
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

/* ─── Item ────────────────────────────────────────────────────────────── */

.picker-item {
    padding: var(--wa-space-xs) var(--wa-space-s);
    line-height: 1.5;
    display: flex;
    align-items: flex-start;
    gap: var(--wa-space-xs);
}

.picker-item:hover {
    background: var(--wa-color-surface-raised);
}

.picker-item.active {
    background: var(--wa-color-surface-lowered);
}

.item-text {
    flex: 1;
    min-width: 0;
    color: var(--wa-color-text-default);
    font-size: var(--wa-font-size-s);
    white-space: pre-wrap;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
}

/* ─── Action buttons ─────────────────────────────────────────────────── */

.item-actions {
    display: flex;
    gap: var(--wa-space-3xs);
    flex-shrink: 0;
    opacity: 0;
    transition: opacity 0.1s;
}

.item-actions.visible,
.picker-item:hover .item-actions {
    opacity: 1;
}

.action-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 1.75rem;
    height: 1.75rem;
    border: 1px solid var(--wa-color-border-default);
    border-radius: var(--wa-border-radius-s);
    background: var(--wa-color-surface-default);
    color: var(--wa-color-text-quiet);
    cursor: pointer;
    font-size: 0.8rem;
    transition: background 0.1s, color 0.1s, border-color 0.1s;
}

.action-btn:hover {
    background: var(--wa-color-surface-alt);
    color: var(--wa-color-text-default);
    border-color: var(--wa-color-border-hover);
}

.action-btn:focus-visible {
    outline: 2px solid var(--wa-color-brand-default);
    outline-offset: 1px;
    color: var(--wa-color-text-default);
}

/* Pre-selected state: insert button is visually highlighted when list is focused */
.action-btn.pre-selected {
    background: var(--wa-color-surface-alt);
    border-color: var(--wa-color-brand-default);
    color: var(--wa-color-text-default);
}
</style>
