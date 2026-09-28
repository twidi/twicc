<script setup>
/**
 * MessageHistoryPickerPopup - A popup for selecting previous user messages triggered by !.
 *
 * Opens a wa-popup anchored to a given element, showing a filterable list
 * of previous user messages fetched from the backend API.
 *
 * Design and keyboard navigation are identical to CommandPickerPopup.
 *
 * Props:
 *   projectId: current project id
 *   sessionId: current session id
 *   anchorId: id of the element to anchor the popup to
 *
 * Events:
 *   select(messageText): emitted when a message is selected (full text)
 *   close(): emitted when the popup is closed without selection
 *   filter-change(filterText): emitted when the filter text changes
 */

import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { apiFetch } from '../../utils/api'
import { useGlideInk } from '../../composables/useGlideInk'
import { usePopupMotion } from '../../composables/usePopupMotion'

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
    syntheticMessageText: {
        type: String,
        default: null,
    },
})

const emit = defineEmits(['select', 'close', 'filter-change'])

// ─── Popup state ──────────────────────────────────────────────────────────

const popupRef = ref(null)
const isOpen = ref(false)
// The panel grows from its anchor on each opening; the close stays instant (overlay motion §6).
const panelRef = ref(null)
usePopupMotion(isOpen, panelRef, popupRef)
const searchInputRef = ref(null)
const listRef = ref(null)

// ─── Data ─────────────────────────────────────────────────────────────────

const allMessages = ref([])
const loading = ref(false)
const error = ref(null)
const searchQuery = ref('')
const activeIndex = ref(0)

// Number of items to jump with PageUp/PageDown
const PAGE_SIZE = 10


// ─── All messages including synthetic ─────────────────────────────────────

const allMessagesWithSynthetic = computed(() => {
    const synth = props.syntheticMessageText
    if (!synth) return allMessages.value
    // Prepend synthetic message with a special line_num (-1) so it's always first
    const syntheticEntry = { line_num: -1, timestamp: null, text: synth, synthetic: true }
    return [syntheticEntry, ...allMessages.value]
})

// ─── Filtered messages ───────────────────────────────────────────────────

const filteredMessages = computed(() => {
    const query = searchQuery.value.trim().toLowerCase()
    if (!query) return allMessagesWithSynthetic.value
    return allMessagesWithSynthetic.value.filter(msg =>
        msg.text.toLowerCase().includes(query)
    )
})

// ─── Gliding highlight (visual refresh step 4c) ───────────────────────────
// The highlight glides from row to row; a new filter snaps it.

const listInkRef = ref(null)
useGlideInk({
    container: listRef,
    flushTarget: listInkRef,
    getActive: () => listRef.value?.querySelector('.picker-item.active') ?? null,
    getItems: () => [...(listRef.value?.querySelectorAll('.picker-item') ?? [])],
    sources: [activeIndex, filteredMessages],
    resetKey: () => searchQuery.value,
})

// ─── API fetch ────────────────────────────────────────────────────────────

async function fetchMessages() {
    loading.value = true
    error.value = null
    try {
        const res = await apiFetch(`/api/projects/${props.projectId}/sessions/${props.sessionId}/user-messages/`)
        if (!res.ok) {
            const data = await res.json()
            error.value = data.error || `HTTP ${res.status}`
            allMessages.value = []
            return
        }
        const data = await res.json()
        // Backend returns messages in chronological order (oldest first).
        // Reverse to put the most recent first, then deduplicate by exact text,
        // keeping the first occurrence (i.e. the most recent occurrence of each message).
        const reversed = (data.messages || []).slice().reverse()
        const seen = new Set()
        const deduped = []
        for (const msg of reversed) {
            if (seen.has(msg.text)) continue
            seen.add(msg.text)
            deduped.push(msg)
        }
        allMessages.value = deduped
    } catch (err) {
        error.value = err.message
        allMessages.value = []
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

    await fetchMessages()

    // Wait for popup and input to render
    await nextTick()
    await nextTick()

    // Focus the search input
    focusSearchInput()
}

function close(payload = {}) {
    isOpen.value = false
    allMessages.value = []
    error.value = null
    searchQuery.value = ''
    emit('close', payload)
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
    // Strip leading "!" — the user already typed it to open the popup
    searchQuery.value = raw.startsWith('!') ? raw.slice(1) : raw
}

// ─── Selection ────────────────────────────────────────────────────────────

function selectMessage(msg) {
    emit('select', msg.text)
    close()
}

function selectActive() {
    const msgs = filteredMessages.value
    if (msgs.length > 0 && activeIndex.value < msgs.length) {
        selectMessage(msgs[activeIndex.value])
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
    if (event.key === 'ArrowDown') {
        event.preventDefault()
        const count = filteredMessages.value.length
        if (count > 1) {
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
        const count = filteredMessages.value.length
        if (count > 0) {
            activeIndex.value = Math.min(PAGE_SIZE, count - 1)
            listRef.value?.focus()
            scrollActiveIntoView()
        }
        return
    }
    if (event.key === 'Enter') {
        // Cmd/Ctrl+Enter is a no-op inside the popover (does not close, does not send).
        if (event.metaKey || event.ctrlKey) return
        event.preventDefault()
        if (filteredMessages.value.length > 0) {
            selectActive()
        } else {
            // No match: close and ask the parent to preserve the typed text.
            // In bang mode the text is already mirrored in the textarea;
            // in pageup mode the parent will insert it at the right position.
            close({ preserveText: searchQuery.value })
        }
        return
    }
}

// ─── List keyboard handler ────────────────────────────────────────────────

function handleListKeydown(event) {
    const msgs = filteredMessages.value
    const count = msgs.length
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
            if (event.metaKey || event.ctrlKey) break
            event.preventDefault()
            if (filteredMessages.value.length > 0) {
                selectActive()
            } else {
                close({ preserveText: searchQuery.value })
            }
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
        class="picker-popup"
    >
        <div ref="panelRef" class="picker-panel glass-surface">
            <!-- Search input -->
            <div class="picker-search">
                <wa-input
                    ref="searchInputRef"
                    :value="searchQuery"
                    placeholder="Filter messages..."
                    size="small"
                    with-clear
                    class="picker-search-input"
                    @input="onSearchInput"
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
                <span ref="listInkRef" class="glide-ink" aria-hidden="true"></span>
                <template v-if="loading">
                    <div class="picker-status">Loading...</div>
                </template>
                <template v-else-if="error">
                    <div class="picker-status picker-error">{{ error }}</div>
                </template>
                <template v-else-if="filteredMessages.length === 0">
                    <div class="picker-status">
                        {{ searchQuery ? 'No matching messages' : 'No messages in this session' }}
                    </div>
                </template>
                <template v-else>
                    <div
                        v-for="(msg, index) in filteredMessages"
                        :key="msg.line_num"
                        :data-index="index"
                        class="picker-item"
                        :class="{ active: index === activeIndex }"
                        @click="selectMessage(msg)"
                        @mouseenter="activeIndex = index"
                    >
                        <div class="item-text">{{ msg.text }}</div>
                    </div>
                </template>
            </div>
        </div>
    </wa-popup>
</template>

<style scoped>
.picker-panel {
    width: min(40rem, calc(100vw - 1rem));
    max-height: min(25rem, 60dvh);
    display: flex;
    flex-direction: column;
    border-radius: var(--wa-border-radius-m);
    overflow: hidden;
}
@media (max-height: 640px) {
    .picker-popup::part(popup) {
        top: 0 !important;
    }
}

/* ─── Search ──────────────────────────────────────────────────────────── */

.picker-search {
    /* --glass-edge-gap (set inside glass surfaces): the focus ring clears the glass border. */
    padding: calc(var(--wa-space-2xs) + var(--glass-edge-gap, 0px));
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
    /* The gliding highlight (motion.css .glide-ink). */
    position: relative;
    --glide-ink-bg: var(--glass-item-highlight);
    --glide-ink-radius: 0;
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
    position: relative; /* paints above the glide ink */
}

.picker-item:hover {
    background: var(--glass-item-hover);
}

.picker-item.active {
    background: var(--glass-item-highlight);
}

/* The ink marks the active row once placed. A hover makes its row active, so the ink
   already marks it: a separate hover fill would sit on top of the arriving ink. */
.picker-list[data-glide-ready] > .picker-item.active,
.picker-list[data-glide-ready] > .picker-item:hover {
    background: transparent;
}

.item-text {
    white-space: pre-line;
    color: var(--wa-color-text-default);
    font-size: var(--wa-font-size-s);
    max-height: 5rem;
    overflow-y: auto;
}
</style>
