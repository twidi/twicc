<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useDebounceFn } from '@vueuse/core'
import { apiFetch } from '../utils/api'
import AppTooltip from './AppTooltip.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
})

const emit = defineEmits(['close', 'navigate', 'update:terms'])

const inputRef = ref(null)
const query = ref('')
const isLoading = ref(false)
const error = ref(null)

// Sorted list of distinct line_nums from the last search
const matchLineNums = ref([])

// Current position in the matchLineNums list (-1 = no position yet)
const currentMatchIndex = ref(-1)

// Total match count (derived from matchLineNums)
const matchCount = computed(() => matchLineNums.value.length)

// Whether we have results to display (search was performed)
const hasSearched = ref(false)

// Whether navigation buttons should be enabled
const canNavigate = computed(() => matchCount.value > 0)

/**
 * Perform the search API call.
 * Calls the same /api/search/ endpoint with session_id filter.
 * The backend auto-excludes title documents when session_id is provided.
 */
async function performSearch() {
    const q = query.value.trim()
    if (q.length < 2) {
        matchLineNums.value = []
        currentMatchIndex.value = -1
        hasSearched.value = false
        error.value = null
        emit('update:terms', [])
        return
    }

    isLoading.value = true
    error.value = null

    try {
        const params = new URLSearchParams({
            q,
            session_id: props.sessionId,
            include_archived: 'true',
            limit: '1',  // Only 1 session group (we're filtering by session_id)
        })

        const response = await apiFetch(`/api/search/?${params}`)

        if (!response.ok) {
            if (response.status === 503) {
                error.value = 'Search index not available'
            } else {
                error.value = 'Search failed'
            }
            matchLineNums.value = []
            currentMatchIndex.value = -1
            hasSearched.value = false
            return
        }

        const data = await response.json()

        // Extract line_nums sorted ascending (one Tantivy doc per line_num, no duplicates)
        if (data.results && data.results.length > 0) {
            const matches = data.results[0].matches || []
            const lineNums = matches.map(m => m.line_num)
            lineNums.sort((a, b) => a - b)
            matchLineNums.value = lineNums
        } else {
            matchLineNums.value = []
        }

        hasSearched.value = true
        currentMatchIndex.value = -1

        // Emit search terms for highlighting
        emit('update:terms', q.split(/\s+/).filter(Boolean))

        // Auto-navigate to the first match
        if (matchLineNums.value.length > 0) {
            goToNext()
        }
    } catch (err) {
        error.value = 'Search failed'
        matchLineNums.value = []
        currentMatchIndex.value = -1
        hasSearched.value = false
        emit('update:terms', [])
    } finally {
        isLoading.value = false
    }
}

const debouncedSearch = useDebounceFn(performSearch, 300)

// Watch query changes to trigger debounced search
watch(query, () => {
    debouncedSearch()
})

/**
 * Navigate to the next match.
 */
function goToNext() {
    if (matchCount.value === 0) return
    if (currentMatchIndex.value < matchCount.value - 1) {
        currentMatchIndex.value++
    } else {
        currentMatchIndex.value = 0  // Wrap around
    }
    emit('navigate', matchLineNums.value[currentMatchIndex.value])
}

/**
 * Navigate to the previous match.
 */
function goToPrevious() {
    if (matchCount.value === 0) return
    if (currentMatchIndex.value > 0) {
        currentMatchIndex.value--
    } else {
        currentMatchIndex.value = matchCount.value - 1  // Wrap around
    }
    emit('navigate', matchLineNums.value[currentMatchIndex.value])
}

/**
 * Focus the search input.
 * Called when the search bar becomes visible.
 */
function focusInput() {
    nextTick(() => {
        const input = inputRef.value?.shadowRoot?.querySelector('input')
            ?? inputRef.value?.querySelector?.('input')
            ?? inputRef.value
        input?.focus()
    })
}

/**
 * Handle keyboard events on the search input.
 */
function handleKeydown(e) {
    if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        emit('close')
    } else if ((e.key === 'Enter' || e.key === 'F3') && e.shiftKey) {
        e.preventDefault()
        goToPrevious()
    } else if ((e.key === 'Enter' || e.key === 'F3') && !e.shiftKey) {
        e.preventDefault()
        goToNext()
    }
}

// Badge display: "X / Y" when navigating, just count otherwise
const badgeText = computed(() => {
    if (currentMatchIndex.value >= 0 && matchCount.value > 0) {
        return `${currentMatchIndex.value + 1} / ${matchCount.value}`
    }
    return String(matchCount.value)
})

/**
 * Open the search bar and focus input.
 */
function open() {
    focusInput()
}

/**
 * Open the search bar with a pre-filled query (e.g. from global search overlay).
 * Sets the query value and triggers an immediate search (bypassing debounce).
 */
async function openWithQuery(q) {
    query.value = q
    // Trigger search immediately (bypass debounce) since the query comes from
    // an already-validated global search
    await performSearch()
    // Focus the wa-input directly (its native focus() delegates to the inner input)
    inputRef.value?.focus()
}

/**
 * Reset search state.
 */
function reset() {
    query.value = ''
    matchLineNums.value = []
    currentMatchIndex.value = -1
    hasSearched.value = false
    error.value = null
    emit('update:terms', [])
}

defineExpose({ open, reset, openWithQuery, goToNext, goToPrevious })
</script>

<template>
    <div class="session-search-bar">
        <wa-input
            ref="inputRef"
            :value="query"
            @input="query = $event.target.value"
            @keydown="handleKeydown"
            placeholder="Search in session..."
            size="small"
            class="search-input"
            clearable
        >
            <wa-icon slot="start" name="magnifying-glass"></wa-icon>
            <wa-badge
                v-if="hasSearched || isLoading"
                slot="end"
                :variant="matchCount === 0 && !isLoading ? 'danger' : 'neutral'"
            >
                <wa-spinner v-if="isLoading" class="search-spinner"></wa-spinner>
                <span v-else>{{ badgeText }}</span>
            </wa-badge>
        </wa-input>
        <button
            id="session-search-prev"
            class="nav-button"
            :disabled="!canNavigate"
            aria-label="Previous match"
            @click="goToPrevious"
        >
            <wa-icon name="chevron-up"></wa-icon>
        </button>
        <AppTooltip for="session-search-prev">Previous match (Shift+Enter / Shift+F3)</AppTooltip>
        <button
            id="session-search-next"
            class="nav-button"
            :disabled="!canNavigate"
            aria-label="Next match"
            @click="goToNext"
        >
            <wa-icon name="chevron-down"></wa-icon>
        </button>
        <AppTooltip for="session-search-next">Next match (Enter / F3)</AppTooltip>
        <button
            id="session-search-close"
            class="nav-button close-button"
            aria-label="Close search"
            @click="emit('close')"
        >
            <wa-icon name="x"></wa-icon>
        </button>
        <AppTooltip for="session-search-close">Close (Escape)</AppTooltip>
    </div>
</template>

<style scoped>
.session-search-bar {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    padding: var(--wa-space-s);
    background: var(--wa-color-surface-default);
    border: 4px solid var(--wa-color-surface-border);
    border-top: 0;
    border-radius: 0 0 var(--wa-border-radius-l) var(--wa-border-radius-l);
    flex-shrink: 0;
    position: absolute;
    z-index: 1;
    left: 50%;
    translate: -50% 0;
}

.search-input {
    flex: 1;
    min-width: 0;
    max-width: 300px;
}

.nav-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border: none;
    border-radius: var(--wa-border-radius-m);
    background: transparent;
    box-shadow: none;
    color: var(--wa-color-text-muted);
    cursor: pointer;
    flex-shrink: 0;
    padding: 0;
}

.nav-button:hover:not(:disabled) {
    background: var(--wa-color-surface-raised);
    color: var(--wa-color-text);
}

.nav-button:disabled {
    opacity: 0.2;
    cursor: default;
}

.close-button:hover:not(:disabled) {
    color: var(--wa-color-text);
}


@container session-items-list (width <= 30rem) {
    .session-search-bar {
        width: 95%;
        gap: var(--wa-space-s);
        padding: var(--wa-space-xs);
    }
}


</style>
