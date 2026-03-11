<script setup>
/**
 * PromptHistoryPickerPopup - A popup for browsing and selecting past user messages.
 *
 * Opens a wa-popup anchored to a given element, showing a filterable list
 * of previous user messages from the current session in chronological order
 * (oldest at top, newest at bottom).
 *
 * Messages are fetched from the backend API in pages (FETCH_SIZE at a time)
 * and rendered via a virtual scroller with fixed item height.
 *
 * Each item has two action buttons:
 * - Insert: inserts at the current cursor position (default action)
 * - Replace: replaces the current textarea content
 *
 * Keyboard navigation:
 * - ArrowUp/ArrowDown: navigate items
 * - Enter: insert at cursor (default action, insert button is pre-highlighted)
 * - Tab: focus replace button, Tab again returns to list
 * - Escape: close popup
 */

import { ref, reactive, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { apiFetch } from '../utils/api'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
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

// ─── Constants ────────────────────────────────────────────────────────────

const FETCH_SIZE = 100       // Messages per API request
const ITEM_HEIGHT = 52       // Fixed pixel height per item
const RENDER_BUFFER = 5      // Extra items to render above/below viewport
const SEARCH_DEBOUNCE = 300  // ms

// ─── Popup state ──────────────────────────────────────────────────────────

const popupRef = ref(null)
const isOpen = ref(false)
const searchInputRef = ref(null)
const listRef = ref(null)
const loading = ref(false)

// ─── Data ─────────────────────────────────────────────────────────────────

const totalCount = ref(0)
const totalUnfiltered = ref(0)
const messages = reactive(new Map())  // index (in result set) → {index, originalIndex, text}
const loadedPages = reactive(new Set())
const fetchingPages = reactive(new Set())

const searchQuery = ref('')
const activeIndex = ref(0)
// Which element has focus within the active item: 'list' | 'replace' | 'insert'
const focusTarget = ref('list')

// Tracks the current search "version" — incremented on every search change
// to discard stale API responses.
let searchVersion = 0

// ─── Virtual scroll state ─────────────────────────────────────────────────

const scrollTopPx = ref(0)

const totalHeight = computed(() => totalCount.value * ITEM_HEIGHT)

const visibleStart = computed(() => Math.floor(scrollTopPx.value / ITEM_HEIGHT))
const visibleEnd = computed(() => {
    const containerH = listRef.value?.clientHeight || 300
    return Math.ceil((scrollTopPx.value + containerH) / ITEM_HEIGHT)
})

const renderStart = computed(() => Math.max(0, visibleStart.value - RENDER_BUFFER))
const renderEnd = computed(() => Math.min(totalCount.value, visibleEnd.value + RENDER_BUFFER))

const renderedItems = computed(() => {
    const items = []
    for (let i = renderStart.value; i < renderEnd.value; i++) {
        const msg = messages.get(i)
        items.push(msg || { index: i, original_index: i, text: null })
    }
    return items
})

// ─── Page fetching ────────────────────────────────────────────────────────

function pageFor(index) {
    return Math.floor(index / FETCH_SIZE)
}

function resetData() {
    messages.clear()
    loadedPages.clear()
    fetchingPages.clear()
    totalCount.value = 0
    totalUnfiltered.value = 0
    searchVersion++
}

async function fetchPage(pageNum) {
    if (loadedPages.has(pageNum) || fetchingPages.has(pageNum)) return
    fetchingPages.add(pageNum)

    const version = searchVersion
    const offset = pageNum * FETCH_SIZE
    const query = searchQuery.value.trim()
    const params = new URLSearchParams({
        offset: String(offset),
        limit: String(FETCH_SIZE),
    })
    if (query) params.set('q', query)

    try {
        const url = `/api/projects/${props.projectId}/sessions/${props.sessionId}/user-messages/?${params}`
        const response = await apiFetch(url)
        if (!response.ok || version !== searchVersion) return

        const data = await response.json()
        if (version !== searchVersion) return  // Stale

        totalCount.value = data.total
        totalUnfiltered.value = data.total_unfiltered
        for (const msg of data.messages) {
            messages.set(msg.index, msg)
        }
        loadedPages.add(pageNum)
    } finally {
        fetchingPages.delete(pageNum)
    }
}

function ensureVisible(startIdx, endIdx) {
    const startPage = pageFor(startIdx)
    const endPage = pageFor(Math.max(0, endIdx - 1))
    for (let p = startPage; p <= endPage; p++) {
        fetchPage(p)
    }
}

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
    loading.value = true
    resetData()

    // Fetch first page to get total count
    await fetchPage(0)

    if (totalCount.value === 0) {
        loading.value = false
        await nextTick()
        listRef.value?.focus()
        return
    }

    // Also fetch the last page so we can display the most recent messages
    const lastPage = pageFor(totalCount.value - 1)
    if (lastPage > 0) {
        await fetchPage(lastPage)
    }

    activeIndex.value = totalCount.value - 1
    loading.value = false

    await nextTick()
    await nextTick()

    listRef.value?.focus()
    scrollToIndex(activeIndex.value)
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
    resetData()
    searchQuery.value = ''
    emit('close')
}

// ─── Scroll management ───────────────────────────────────────────────────

function onScroll() {
    scrollTopPx.value = listRef.value?.scrollTop || 0
    ensureVisible(renderStart.value, renderEnd.value)
}

function scrollToIndex(index) {
    const container = listRef.value
    if (!container) return
    const top = index * ITEM_HEIGHT
    const bottom = top + ITEM_HEIGHT
    if (top < container.scrollTop) {
        container.scrollTop = top
    } else if (bottom > container.scrollTop + container.clientHeight) {
        container.scrollTop = bottom - container.clientHeight
    }
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
    const msg = messages.get(activeIndex.value)
    if (msg?.text) replaceMessage(msg.text)
}

function insertActive() {
    const msg = messages.get(activeIndex.value)
    if (msg?.text) insertMessage(msg.text)
}

// ─── Navigate to index (with fetch if needed) ────────────────────────────

function navigateTo(index) {
    activeIndex.value = index
    scrollToIndex(index)
    ensureVisible(index, index + 1)
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
        if (totalCount.value > 0) {
            if (activeIndex.value + 1 < totalCount.value) {
                activeIndex.value++
            }
            focusTarget.value = 'list'
            listRef.value?.focus()
            scrollToIndex(activeIndex.value)
        }
        return
    }
    if (event.key === 'PageDown') {
        event.preventDefault()
        if (totalCount.value > 0) {
            navigateTo(Math.min(activeIndex.value + 10, totalCount.value - 1))
            focusTarget.value = 'list'
            listRef.value?.focus()
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
    if (event.key === 'Enter') {
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
        if (action === 'insert') {
            focusActionButton('replace')
        } else {
            focusTarget.value = 'list'
            listRef.value?.focus()
        }
        return
    }
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
        event.preventDefault()
        event.stopPropagation()
        focusTarget.value = 'list'
        listRef.value?.focus()
        if (event.key === 'ArrowUp' && activeIndex.value > 0) {
            navigateTo(activeIndex.value - 1)
        } else if (event.key === 'ArrowDown' && activeIndex.value < totalCount.value - 1) {
            navigateTo(activeIndex.value + 1)
        }
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

    const count = totalCount.value
    if (!count) return

    switch (event.key) {
        case 'Tab': {
            event.preventDefault()
            focusActionButton('replace')
            break
        }

        case 'ArrowDown': {
            event.preventDefault()
            if (activeIndex.value + 1 < count) {
                navigateTo(activeIndex.value + 1)
            }
            break
        }

        case 'ArrowUp': {
            event.preventDefault()
            if (activeIndex.value <= 0) {
                activeIndex.value = 0
                focusSearchInput()
            } else {
                navigateTo(activeIndex.value - 1)
            }
            break
        }

        case 'Home': {
            event.preventDefault()
            navigateTo(0)
            break
        }

        case 'End': {
            event.preventDefault()
            navigateTo(count - 1)
            break
        }

        case 'PageDown': {
            event.preventDefault()
            navigateTo(Math.min(activeIndex.value + 10, count - 1))
            break
        }

        case 'PageUp': {
            event.preventDefault()
            if (activeIndex.value <= 0) {
                activeIndex.value = 0
                focusSearchInput()
            } else {
                navigateTo(Math.max(activeIndex.value - 10, 0))
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

// ─── Search debounce ──────────────────────────────────────────────────────

let searchTimer = null

watch(searchQuery, () => {
    clearTimeout(searchTimer)
    searchTimer = setTimeout(async () => {
        resetData()
        await fetchPage(0)
        if (totalCount.value > 0) {
            const lastPage = pageFor(totalCount.value - 1)
            if (lastPage > 0) await fetchPage(lastPage)
            activeIndex.value = totalCount.value - 1
            await nextTick()
            scrollToIndex(activeIndex.value)
        } else {
            activeIndex.value = 0
        }
    }, SEARCH_DEBOUNCE)
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
    clearTimeout(searchTimer)
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

            <!-- Virtual-scrolled message list -->
            <div
                ref="listRef"
                class="picker-list"
                tabindex="0"
                @scroll="onScroll"
                @keydown="handleListKeydown"
            >
                <template v-if="loading">
                    <div class="picker-status">
                        <wa-spinner></wa-spinner>
                    </div>
                </template>
                <template v-else-if="totalCount === 0">
                    <div class="picker-status">
                        {{ searchQuery ? 'No matching messages' : 'No messages in this session' }}
                    </div>
                </template>
                <template v-else>
                    <div class="virtual-viewport" :style="{ height: totalHeight + 'px' }">
                        <div
                            v-for="item in renderedItems"
                            :key="item.index"
                            :data-index="item.index"
                            class="picker-item"
                            :class="{ active: item.index === activeIndex }"
                            :style="{ transform: `translateY(${item.index * ITEM_HEIGHT}px)` }"
                            @mouseenter="activeIndex = item.index"
                        >
                            <span class="item-num">{{ (item.original_index ?? item.index) + 1 }}</span>
                            <div v-if="item.text" class="item-text">{{ item.text }}</div>
                            <div v-else class="item-text item-placeholder">…</div>
                            <div
                                v-if="item.text"
                                class="item-actions"
                                :class="{ visible: item.index === activeIndex }"
                            >
                                <button
                                    class="action-btn action-insert"
                                    :class="{ 'pre-selected': item.index === activeIndex && focusTarget === 'list' }"
                                    title="Insert (at cursor position) — Enter"
                                    tabindex="-1"
                                    @click.stop="insertMessage(item.text)"
                                    @keydown="handleActionKeydown($event, 'insert')"
                                >
                                    <wa-icon name="right-to-bracket"></wa-icon>
                                </button>
                                <button
                                    class="action-btn action-replace"
                                    title="Replace (overwrite current text) — Tab+Enter"
                                    tabindex="-1"
                                    @click.stop="replaceMessage(item.text)"
                                    @keydown="handleActionKeydown($event, 'replace')"
                                >
                                    <wa-icon name="right-left"></wa-icon>
                                </button>
                            </div>
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

/* ─── Virtual viewport ───────────────────────────────────────────────── */

.virtual-viewport {
    position: relative;
}

/* ─── Item (fixed height for virtual scroll) ─────────────────────────── */

.picker-item {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 52px; /* Must match ITEM_HEIGHT */
    padding: var(--wa-space-2xs) var(--wa-space-s);
    box-sizing: border-box;
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.picker-item:hover {
    background: var(--wa-color-surface-raised);
}

.picker-item.active {
    background: var(--wa-color-surface-lowered);
}

.item-num {
    flex-shrink: 0;
    min-width: 1.5rem;
    text-align: right;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    user-select: none;
}

.item-text {
    flex: 1;
    min-width: 0;
    color: var(--wa-color-text-default);
    font-size: var(--wa-font-size-s);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.item-placeholder {
    color: var(--wa-color-text-quiet);
    font-style: italic;
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
