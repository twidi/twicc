<script setup>
import { ref, computed, onMounted, onUnmounted, watch, nextTick, inject } from 'vue'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate, formatDuration } from '../utils/date'
import { PROCESS_STATE, PROCESS_STATE_COLORS, PROCESS_STATE_NAMES, SESSION_TIME_FORMAT } from '../constants'
import { killProcess } from '../composables/useWebSocket'
import ProjectBadge from './ProjectBadge.vue'
import ProcessIndicator from './ProcessIndicator.vue'
import VirtualScroller from './VirtualScroller.vue'
import CostDisplay from './CostDisplay.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        default: null
    },
    showProjectName: {
        type: Boolean,
        default: false
    },
    searchQuery: {
        type: String,
        default: ''
    },
    showArchived: {
        type: Boolean,
        default: false
    }
})

const store = useDataStore()
const settingsStore = useSettingsStore()

// Session time format setting
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
// Tooltips setting
const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)
// Costs setting
const showCosts = computed(() => settingsStore.areCostsShown)
// Compact view setting
const compactView = computed(() => settingsStore.isCompactSessionList)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)

// Sessions are already sorted by mtime desc in the getter
// Filter out archived sessions unless showArchived is enabled
// Always keep the currently selected session visible (even if archived)
const allSessions = computed(() => {
    const baseSessions = props.projectId === ALL_PROJECTS_ID
        ? store.getAllSessions
        : store.getProjectSessions(props.projectId)

    return baseSessions.filter(s =>
        props.showArchived || !s.archived || s.id === props.sessionId
    )
})

/**
 * Check if a query matches a text using subsequence matching.
 * All characters from query must appear in text, in order, but not necessarily consecutive.
 * Case-insensitive.
 *
 * Examples:
 *   matchSubsequence("vs", "virtual scroller") -> true (v...irtual s...croller)
 *   matchSubsequence("vscr", "virtual scroller") -> true (v...irtual scr...oller)
 *   matchSubsequence("xyz", "virtual scroller") -> false
 *
 * @param {string} query - The search query
 * @param {string} text - The text to search in
 * @returns {boolean} True if query is a subsequence of text
 */
function matchSubsequence(query, text) {
    const lowerQuery = query.toLowerCase()
    const lowerText = text.toLowerCase()

    let queryIndex = 0
    for (let i = 0; i < lowerText.length && queryIndex < lowerQuery.length; i++) {
        if (lowerText[i] === lowerQuery[queryIndex]) {
            queryIndex++
        }
    }
    return queryIndex === lowerQuery.length
}

// Filtered sessions based on search query (subsequence matching on title)
const sessions = computed(() => {
    const query = props.searchQuery.trim()
    if (!query) return allSessions.value

    return allSessions.value.filter(session => {
        const displayName = (session.draft && !session.title)
            ? 'New session'
            : (session.title || session.id)
        return matchSubsequence(query, displayName)
    })
})

// Pagination state
const hasMore = computed(() => store.hasMoreSessions(props.projectId))
const isLoading = computed(() => store.areSessionsLoading(props.projectId))

// Local error state for "load more" failures (not initial load)
const loadMoreError = ref(false)

// Virtual scroller configuration
// Session items have relatively uniform height (~80-100px normal, ~35-40px compact)
const minSessionHeight = computed(() => compactView.value ? 35 : 70)
const SCROLLER_BUFFER = 300

// Reference to the VirtualScroller component
const scrollerRef = ref(null)

// Keyboard navigation: highlighted item index (-1 = none)
const highlightedIndex = ref(-1)

// Number of items to jump for PageUp/PageDown
const PAGE_SIZE = 10

// Load more sessions when approaching the end of the list
async function loadMore() {
    if (isLoading.value || !hasMore.value || loadMoreError.value) return

    try {
        loadMoreError.value = false
        await store.loadSessions(props.projectId)
    } catch {
        // Only show error if we already have some sessions (not initial load)
        if (sessions.value.length > 0) {
            loadMoreError.value = true
        }
    }
}

// Retry after error
async function handleRetry() {
    loadMoreError.value = false
    await loadMore()
}

/**
 * Handle virtual scroller update event.
 * Triggers loading more sessions when user scrolls near the end.
 */
function onScrollerUpdate({ visibleEndIndex }) {
    // Load more when within 10 items of the end
    if (hasMore.value && !isLoading.value && sessions.value.length - visibleEndIndex < 10) {
        loadMore()
    }
}

// Reset scroll to top and highlight when project changes
watch(() => props.projectId, () => {
    loadMoreError.value = false
    highlightedIndex.value = -1
    if (scrollerRef.value) {
        scrollerRef.value.scrollToTop()
    }
})

// Reset highlight when search query changes
watch(() => props.searchQuery, () => {
    highlightedIndex.value = -1
})

// Reset highlight when selected session changes (e.g., mouse selection)
// This ensures keyboard navigation restarts from the new selection
watch(() => props.sessionId, () => {
    highlightedIndex.value = -1
})

// Get display name for session (title if available, "New session" for drafts, otherwise ID)
function getSessionDisplayName(session) {
    // For draft sessions without a title, show "New session"
    if (session.draft && !session.title) {
        return 'New session'
    }
    return session.title || session.id
}


const emit = defineEmits(['select', 'focus-search'])

function handleSelect(session) {
    emit('select', session)
}

/**
 * Get process state for a session.
 * @param {string} sessionId
 * @returns {{ state: string, started_at?: number, state_changed_at?: number, memory?: number, error?: string } | null}
 */
function getProcessState(sessionId) {
    return store.getProcessState(sessionId)
}

/**
 * Get the color for a process state.
 * @param {string} state
 * @returns {string} CSS color variable
 */
function getProcessColor(state) {
    return PROCESS_STATE_COLORS[state] || PROCESS_STATE_COLORS[PROCESS_STATE.DEAD]
}

/**
 * Format memory in bytes to a human-readable string.
 * - KB: no decimal (e.g., "512 KB")
 * - MB < 10: 1 decimal (e.g., "4.6 MB")
 * - MB >= 10: no decimal (e.g., "420 MB")
 * - GB: 1 decimal (e.g., "1.5 GB")
 * @param {number|null} bytes
 * @returns {string}
 */
function formatMemory(bytes) {
    if (bytes == null) return ''

    const kb = bytes / 1024
    const mb = kb / 1024
    const gb = mb / 1024

    if (gb >= 1) {
        return `${gb.toFixed(1)} GB`
    }
    if (mb >= 10) {
        return `${Math.round(mb)} MB`
    }
    if (mb >= 1) {
        return `${mb.toFixed(1)} MB`
    }
    return `${Math.round(kb)} KB`
}

// Only assistant_turn should animate in session list
const animateStates = ['assistant_turn']

// Session menu handling
const openRenameDialog = inject('openRenameDialog')

/**
 * Check if a session's process can be stopped (any state except dead).
 * @param {string} sessionId
 * @returns {boolean}
 */
function canStopProcess(sessionId) {
    const state = getProcessState(sessionId)?.state
    return state && state !== PROCESS_STATE.DEAD
}

/**
 * Handle dropdown menu selection for a session.
 * @param {CustomEvent} event - The wa-select event
 * @param {Object} session - The session object
 */
function handleMenuSelect(event, session) {
    const action = event.detail.item.value
    if (action === 'rename') {
        openRenameDialog(session)
    } else if (action === 'stop') {
        killProcess(session.id)
    } else if (action === 'delete-draft') {
        store.deleteDraftSession(session.id)
    } else if (action === 'archive') {
        // Stop the process if running — archived and running are mutually exclusive
        if (canStopProcess(session.id)) {
            killProcess(session.id)
        }
        store.setSessionArchived(session.project_id, session.id, true)
    } else if (action === 'unarchive') {
        store.setSessionArchived(session.project_id, session.id, false)
    } else if (action === 'pin') {
        store.setSessionPinned(session.project_id, session.id, true)
    } else if (action === 'unpin') {
        store.setSessionPinned(session.project_id, session.id, false)
    }
}

/**
 * Prevent click on dropdown from selecting the session.
 * @param {Event} event
 */
function handleDropdownClick(event) {
    event.stopPropagation()
}

// Timer for updating state durations
const now = ref(Date.now() / 1000)  // Current time in seconds
let durationTimer = null

onMounted(() => {
    // Update every second for duration display
    durationTimer = setInterval(() => {
        now.value = Date.now() / 1000
    }, 1000)
})

onUnmounted(() => {
    if (durationTimer) {
        clearInterval(durationTimer)
    }
})

/**
 * Calculate state duration for a process.
 * @param {object} processState
 * @returns {number} Duration in seconds
 */
function getStateDuration(processState) {
    if (!processState?.state_changed_at) return 0
    return Math.max(0, Math.floor(now.value - processState.state_changed_at))
}

/**
 * Convert Unix timestamp (seconds) to Date object for wa-relative-time.
 * @param {number} timestamp - Unix timestamp in seconds
 * @returns {Date}
 */
function timestampToDate(timestamp) {
    if (!timestamp) return new Date()
    return new Date(timestamp * 1000)
}

/**
 * Get the starting index for keyboard navigation.
 * If a session is highlighted, use that. Otherwise, use the selected session's index.
 * Returns -1 if neither is available.
 */
function getNavigationStartIndex() {
    if (highlightedIndex.value >= 0) {
        return highlightedIndex.value
    }
    // No highlight - try to start from selected session
    if (props.sessionId) {
        const selectedIndex = sessions.value.findIndex(s => s.id === props.sessionId)
        if (selectedIndex >= 0) {
            return selectedIndex
        }
    }
    return -1
}

/**
 * Handle keyboard navigation from the search input or the list itself.
 * Navigates through sessions with arrow keys and selects with Enter.
 *
 * @param {KeyboardEvent} event - The keyboard event
 * @param {Object} [options] - Navigation options
 * @param {boolean} [options.fromSearch=false] - True when called from the search input.
 *   When true, navigation ignores the selected session and always starts from scratch
 *   (e.g., ArrowDown goes to the first item, not relative to the active session).
 * @returns {boolean} True if the event was handled (should preventDefault)
 */
function handleKeyNavigation(event, { fromSearch = false } = {}) {
    const count = sessions.value.length
    if (count === 0) return false

    const key = event.key
    // When coming from the search input with no highlight, always start from
    // scratch (-1) so that ArrowDown goes to the first item, not relative to
    // the currently selected session.
    const startIndex = (fromSearch && highlightedIndex.value < 0) ? -1 : getNavigationStartIndex()
    let newIndex = highlightedIndex.value

    switch (key) {
        case 'ArrowDown':
            // Move down from current position, or start at first item
            newIndex = startIndex < 0 ? 0 : Math.min(startIndex + 1, count - 1)
            break

        case 'ArrowUp':
            // If already at the first item, move focus back to the search input
            if (startIndex === 0) {
                highlightedIndex.value = -1
                emit('focus-search')
                return true
            }
            // Move up from current position, or start at last item
            newIndex = startIndex < 0 ? count - 1 : Math.max(startIndex - 1, 0)
            break

        case 'Home':
            newIndex = 0
            break

        case 'End':
            newIndex = count - 1
            break

        case 'PageDown':
            newIndex = startIndex < 0 ? PAGE_SIZE - 1 : Math.min(startIndex + PAGE_SIZE, count - 1)
            break

        case 'PageUp':
            // If already at the first item, move focus back to the search input
            if (startIndex === 0) {
                highlightedIndex.value = -1
                emit('focus-search')
                return true
            }
            newIndex = startIndex < 0 ? 0 : Math.max(startIndex - PAGE_SIZE, 0)
            break

        case 'Enter':
            // Select the highlighted session
            if (highlightedIndex.value >= 0 && highlightedIndex.value < count) {
                handleSelect(sessions.value[highlightedIndex.value])
                return true
            }
            return false

        case 'Escape':
            // Clear highlight if any, otherwise let parent handle it (e.g., clear search)
            if (highlightedIndex.value >= 0) {
                highlightedIndex.value = -1
                return true
            }
            return false

        default:
            return false
    }

    // Update highlight and scroll to it
    if (newIndex !== highlightedIndex.value) {
        highlightedIndex.value = newIndex
        if (scrollerRef.value) {
            // For Home/End, use the scroller's native methods which work better
            // For other navigation, scroll to make the item visible
            if (key === 'Home') {
                scrollerRef.value.scrollToTop()
            } else if (key === 'End') {
                // scrollToBottom() uses estimated heights for unmeasured items,
                // which may not scroll far enough. After the initial scroll,
                // wait for items to be rendered AND measured by ResizeObserver.
                // ResizeObserver is async and not tied to Vue's nextTick, so we use
                // a small timeout to allow measurements to complete.
                scrollerRef.value.scrollToBottom()
                setTimeout(() => {
                    scrollerRef.value?.scrollToIndex(newIndex, { align: 'end' })
                }, 50)
            } else if (key === 'PageDown' || key === 'PageUp') {
                // Page navigation may jump to unmeasured items, use delayed correction
                scrollToIndexIfNeeded(newIndex, { delayedCorrection: true })
            } else {
                // For arrow keys, items are usually already measured (adjacent to visible)
                scrollToIndexIfNeeded(newIndex)
            }

            // Ensure focus stays on the list after scroll (items may be re-rendered)
            // Use nextTick to wait for Vue to update the DOM
            nextTick(() => {
                scrollerRef.value?.$el?.focus()
            })
        }
    }
    return true
}

/**
 * Scroll to an index only if it's not already fully visible in the viewport.
 * Uses align 'start' or 'end' depending on scroll direction.
 *
 * @param {number} index - The item index to scroll to
 * @param {Object} [options] - Options
 * @param {boolean} [options.delayedCorrection=false] - If true, re-scroll after a delay
 *        to account for items that weren't measured yet (heights were estimated)
 */
function scrollToIndexIfNeeded(index, { delayedCorrection = false } = {}) {
    if (!scrollerRef.value) return

    // Get the actual visible range from the scroller (based on measured heights)
    // visibleEnd is exclusive and may include a partially visible item at the bottom
    const { start: visibleStart, end: visibleEnd } = scrollerRef.value.getVisibleRange()

    let scrolled = false
    let align = null

    if (index < visibleStart) {
        // Item is above the viewport
        align = 'start'
        scrollerRef.value.scrollToIndex(index, { align })
        scrolled = true
    } else if (index >= visibleEnd) {
        // Item is below the viewport
        align = 'end'
        scrollerRef.value.scrollToIndex(index, { align })
        scrolled = true
    }

    // If we scrolled and correction is requested, re-scroll after items are measured
    // This handles the case where we scroll to unmeasured items with estimated heights
    if (scrolled && delayedCorrection && align) {
        setTimeout(() => {
            scrollerRef.value?.scrollToIndex(index, { align })
        }, 50)
    }
}

/**
 * Handle keydown events directly on the session list container.
 * This allows keyboard navigation when focus is in the list (not just the search input).
 *
 * @param {KeyboardEvent} event
 */
function handleListKeydown(event) {
    // Only handle navigation keys
    const navigationKeys = ['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageUp', 'PageDown', 'Enter', 'Escape']
    if (!navigationKeys.includes(event.key)) return

    const handled = handleKeyNavigation(event)
    if (handled) {
        event.preventDefault()
    }
}

// Expose methods for parent component access via ref
defineExpose({
    handleKeyNavigation,
})
</script>

<template>
    <div class="session-list-container" :class="{ 'session-list-container--compact': compactView }">
        <!-- Empty state: no sessions at all -->
        <div v-if="allSessions.length === 0 && !isLoading" class="empty-state">
            No sessions
        </div>

        <!-- Empty state: no matching sessions (search returned nothing) -->
        <div v-else-if="sessions.length === 0 && !isLoading" class="empty-state">
            No matching sessions
        </div>

        <!-- Session list with virtual scroller -->
        <VirtualScroller
            v-else
            ref="scrollerRef"
            :key="projectId"
            :items="sessions"
            :item-key="session => session.id"
            :min-item-height="minSessionHeight"
            :buffer="SCROLLER_BUFFER"
            :unload-buffer="SCROLLER_BUFFER * 1.5"
            class="session-list"
            tabindex="0"
            @update="onScrollerUpdate"
            @keydown="handleListKeydown"
        >
            <template #default="{ item: session, index }">
                <div
                    class="session-item-wrapper"
                    :class="{
                        'session-item-wrapper--active': session.id === sessionId,
                        'session-item-wrapper--highlighted': index === highlightedIndex
                    }"
                >
                    <wa-button
                        :appearance="session.id === sessionId ? 'outlined' : 'plain'"
                        :variant="session.id === sessionId ? 'brand' : 'neutral'"
                        class="session-item"
                        :class="{
                            'session-item--active': session.id === sessionId,
                            'session-item--highlighted': index === highlightedIndex
                        }"
                        @click="handleSelect(session)"
                    >
                        <div class="session-name-row">
                            <!-- Compact mode: inline project color dot (instead of full ProjectBadge line) -->
                            <span
                                v-if="compactView && showProjectName"
                                :id="`compact-project-dot-${session.id}`"
                                class="compact-project-dot"
                                :style="store.getProject(session.project_id)?.color ? { '--dot-color': store.getProject(session.project_id).color } : null"
                            ></span>
                            <wa-tooltip v-if="compactView && showProjectName && tooltipsEnabled" :for="`compact-project-dot-${session.id}`">{{ store.getProjectDisplayName(session.project_id) }}</wa-tooltip>
                            <wa-icon v-if="session.pinned" name="thumbtack" class="pinned-icon"></wa-icon>
                            <wa-tag v-if="session.archived" size="small" variant="neutral" class="archived-tag">Arch.</wa-tag>
                            <wa-tag v-else-if="session.draft" size="small" variant="warning" class="draft-tag">Draft</wa-tag>
                            <wa-tag v-if="session.stale" size="small" variant="warning" class="stale-tag">Stale</wa-tag>
                            <span :id="`session-name-${session.id}`" class="session-name">{{ getSessionDisplayName(session) }}</span>
                            <wa-tooltip v-if="tooltipsEnabled" :for="`session-name-${session.id}`">{{ session.title || session.id }}</wa-tooltip>
                            <!-- Compact mode: inline process indicator -->
                            <ProcessIndicator
                                v-if="compactView && !session.draft && getProcessState(session.id)"
                                :id="`compact-process-indicator-${session.id}`"
                                :state="getProcessState(session.id).state"
                                size="small"
                                :animate-states="animateStates"
                                class="compact-process-indicator"
                            />
                            <wa-tooltip v-if="compactView && tooltipsEnabled && !session.draft && getProcessState(session.id)" :for="`compact-process-indicator-${session.id}`">Claude Code state: {{ PROCESS_STATE_NAMES[getProcessState(session.id).state] }}</wa-tooltip>
                        </div>
                        <!-- Project badge line (hidden in compact mode, dot is shown inline instead) -->
                        <ProjectBadge v-if="!compactView && showProjectName" :project-id="session.project_id" class="session-project" />
                    <!-- Process info row (only shown when process is active and not draft, hidden in compact mode) -->
                    <div
                        v-if="!compactView && !session.draft && getProcessState(session.id)"
                        class="process-info"
                        :style="{ color: getProcessColor(getProcessState(session.id).state) }"
                    >
                        <span :id="`process-memory-${session.id}`" class="process-memory">
                            <template v-if="getProcessState(session.id).memory">
                                {{ formatMemory(getProcessState(session.id).memory) }}
                            </template>
                        </span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`process-memory-${session.id}`">Claude Code memory usage</wa-tooltip>

                        <span :id="`process-duration-${session.id}`" class="process-duration">
                            <template v-if="getProcessState(session.id).state === PROCESS_STATE.ASSISTANT_TURN && getProcessState(session.id).state_changed_at">
                                {{ formatDuration(getStateDuration(getProcessState(session.id))) }}
                            </template>
                        </span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`process-duration-${session.id}`">Assistant turn duration</wa-tooltip>

                        <span class="process-indicator-cell">
                            <wa-icon
                                v-if="store.getPendingRequest(session.id)"
                                :id="`pending-request-${session.id}`"
                                name="hand"
                                class="pending-request-indicator"
                            ></wa-icon>
                            <wa-tooltip v-if="tooltipsEnabled && store.getPendingRequest(session.id)" :for="`pending-request-${session.id}`">Waiting for your response</wa-tooltip>
                            <ProcessIndicator
                                :id="`process-indicator-${session.id}`"
                                :state="getProcessState(session.id).state"
                                size="small"
                                :animate-states="animateStates"
                            />
                            <wa-tooltip v-if="tooltipsEnabled" :for="`process-indicator-${session.id}`">Claude Code state: {{ PROCESS_STATE_NAMES[getProcessState(session.id).state] }}</wa-tooltip>
                        </span>
                    </div>
                    <!-- Meta row (not shown for draft sessions, hidden in compact mode) -->
                    <div v-if="!compactView && !session.draft" class="session-meta" :class="{ 'session-meta--no-cost': !showCosts }">
                        <span :id="`session-messages-${session.id}`" class="session-messages"><wa-icon auto-width name="comment" variant="regular"></wa-icon>{{ session.message_count ?? '??' }}</span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`session-messages-${session.id}`">Number of user and assistant messages</wa-tooltip>

                        <template v-if="showCosts">
                            <CostDisplay :id="`session-cost-${session.id}`" :cost="session.total_cost" class="session-cost" />
                            <wa-tooltip v-if="tooltipsEnabled" :for="`session-cost-${session.id}`">Total session cost</wa-tooltip>
                        </template>

                        <span :id="`session-mtime-${session.id}`" class="session-mtime">
                            <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                            <wa-relative-time v-if="useRelativeTime" :date.prop="timestampToDate(session.mtime)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                            <template v-else>{{ formatDate(session.mtime, { smart: true }) }}</template>
                        </span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`session-mtime-${session.id}`">{{ useRelativeTime ? `Last activity: ${formatDate(session.mtime, { smart: true })}` : 'Last activity' }}</wa-tooltip>
                        </div>
                    </wa-button>
                    <!-- Session dropdown menu (outside button to avoid nesting issues) -->
                    <wa-dropdown
                        class="session-menu"
                        placement="bottom-end"
                        @wa-select="(e) => handleMenuSelect(e, session)"
                    >
                        <wa-button
                            slot="trigger"
                            variant="neutral"
                            appearance="plain"
                            size="small"
                            class="session-menu-trigger"
                        >
                            <wa-icon name="ellipsis" label="Session menu"></wa-icon>
                        </wa-button>
                        <!-- Standard actions -->
                        <wa-dropdown-item value="rename">
                            <wa-icon slot="icon" name="pencil"></wa-icon>
                            Rename
                        </wa-dropdown-item>
                        <wa-dropdown-item v-if="!session.draft && !session.pinned" value="pin">
                            <wa-icon slot="icon" name="thumbtack"></wa-icon>
                            Pin
                        </wa-dropdown-item>
                        <wa-dropdown-item v-if="session.pinned" value="unpin">
                            <wa-icon slot="icon" name="thumbtack" class="unpinned-menu-icon"></wa-icon>
                            Unpin
                        </wa-dropdown-item>
                        <wa-dropdown-item v-if="!session.draft && !session.archived" value="archive">
                            <wa-icon slot="icon" name="box-archive"></wa-icon>
                            {{ canStopProcess(session.id) ? 'Archive (it will stop the Claude Code process)' : 'Archive' }}
                        </wa-dropdown-item>
                        <wa-dropdown-item v-if="session.archived" value="unarchive">
                            <wa-icon slot="icon" name="box-open"></wa-icon>
                            Unarchive
                        </wa-dropdown-item>
                        <!-- Danger actions -->
                        <template v-if="canStopProcess(session.id) || session.draft">
                            <wa-divider></wa-divider>
                            <wa-dropdown-item v-if="canStopProcess(session.id)" value="stop">
                                <wa-icon slot="icon" name="ban"></wa-icon>
                                Stop the Claude Code process
                            </wa-dropdown-item>
                            <wa-dropdown-item v-if="session.draft" value="delete-draft" variant="danger">
                                <wa-icon slot="icon" name="trash"></wa-icon>
                                Delete draft
                            </wa-dropdown-item>
                        </template>
                    </wa-dropdown>
                </div>
            </template>
        </VirtualScroller>

        <!-- Error state for load more (shown after the scroller) -->
        <div v-if="loadMoreError" class="load-more-error">
            <wa-callout variant="danger">
                <span>Failed to load more sessions</span>
                <wa-button
                    slot="footer"
                    variant="danger"
                    appearance="outlined"
                    size="small"
                    :loading="isLoading"
                    @click="handleRetry"
                >
                    <wa-icon name="arrow-rotate-right" slot="prefix"></wa-icon>
                    Retry
                </wa-button>
            </wa-callout>
        </div>

        <!-- Loading indicator (shown at bottom when loading more) -->
        <div v-if="isLoading && sessions.length > 0" class="load-more-indicator">
            <wa-spinner></wa-spinner>
        </div>

    </div>
</template>

<style scoped>
.session-list-container {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
    container-type: inline-size;
    container-name: session-list;
}

.session-list {
    flex: 1;
    min-height: 0;
    padding: var(--wa-space-s);
}

/* Remove default focus outline on the list - we show highlight on items instead */
.session-list:focus {
    outline: none;
}

.session-item-wrapper {
    position: relative;
    width: 100%;
}

.session-list-container:not(.session-list-container--compact) .session-item-wrapper {
    /* Small gap between items */
    margin-block: var(--wa-space-3xs);
}

.session-item {
    width: 100%;
}

.session-item::part(base) {
    padding: var(--wa-space-xs);
    height: auto;
    margin-bottom: var(--wa-shadow-offset-y-s);  /* default if border, enforce for non active items to avoid movement */
}

.session-list-container.session-list-container--compact .session-item::part(base) {
    padding-block: var(--wa-space-2xs);
}

/* Keyboard navigation highlight */
.session-item--highlighted::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.session-item::part(label) {
    width: 100%;
    text-align: left;
}

.session-name-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.pinned-icon {
    flex-shrink: 0;
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-yellow-80) !important;
    transform: rotate(30deg);
}

.unpinned-menu-icon {
    opacity: 0.5;
}

.draft-tag,
.archived-tag,
.stale-tag {
    flex-shrink: 0;
    line-height: unset;
    height: unset;
    align-self: stretch;
}

.session-name {
    font-size: var(--wa-font-size-s);
    font-weight: 700;
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    /* Visual space for dropdown button */
    margin-right: 1.5rem;
}

/* Compact mode: inline project color dot */
.compact-project-dot {
    width: var(--wa-space-s);
    height: var(--wa-space-s);
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

/* Compact mode: inline process indicator pushed to the right */
.compact-process-indicator {
    margin-left: auto;
    flex-shrink: 0;
    position: relative;
    left: -1.5rem;
}

/* Session dropdown menu */
.session-menu {
    display: block;
    position: absolute;
    top: var(--wa-space-xs);
    right: 0;
    z-index: 1;
}

.session-menu-trigger {
    opacity: 0.4;
    transition: opacity 0.15s;
    font-size: var(--wa-font-size-2xs);
}

/* Show menu trigger on hover or when dropdown is open */
.session-item-wrapper:hover .session-menu-trigger,
.session-item-wrapper--active .session-menu-trigger,
.session-menu[open] .session-menu-trigger {
    opacity: 0.6;
}

.session-menu-trigger:hover {
    opacity: 1 !important;
}

.session-project {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-body);;
    margin-top: var(--wa-space-3xs);
    max-width: 100%;
}

/* Process info row - shows memory, duration, and indicator */
.process-info {
    display: grid;
    grid-template-columns: 1fr auto 0.6fr;
    align-items: center;
    justify-items: end;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-body);
    margin-top: var(--wa-space-3xs);
    overflow: hidden;
}

.process-memory {
    justify-self: start;
}

.process-duration {
    justify-self: center;
}

.process-indicator-cell {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: var(--wa-space-2xs);
}

.pending-request-indicator {
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-xs);
    animation: pending-pulse 1.5s ease-in-out infinite;
}

@keyframes pending-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

.session-meta {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    align-items: center;
    justify-items: start;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-weight: var(--wa-font-weight-body);;
    margin-top: var(--wa-space-2xs);
    overflow: hidden;
}

.session-meta > span {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.session-mtime {
    justify-self: end;
}
.session-cost {
    justify-self: center;
}

.session-meta--no-cost {
    grid-template-columns: 1fr 1fr;
}

@container session-list (width <= 12rem) {
    /* Hide memory when container is too narrow */
    .process-info {
        grid-template-columns: auto 0.6fr;
    }
    .process-duration {
        justify-self: start;
    }
    .process-memory {
        display: none !important;
    }
    /* Hide cost when container is too narrow */
    .session-meta {
        grid-template-columns: 1fr 1fr;
    }
    .session-cost {
        display: none !important;
    }
}

@container session-list (width <= 9rem) {
    /* Hide duration when container is very narrow (keep only indicator) */
    .process-info {
        grid-template-columns: 1fr;
    }
    .process-duration {
        display: none !important;
    }
    /* Hide messages when container is very narrow (keep only mtime) */
    .session-meta {
        grid-template-columns: 1fr;
    }
    .session-messages {
        display: none !important;
    }
}

.load-more-indicator {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s);
    flex-shrink: 0;
}

.load-more-error {
    padding: var(--wa-space-s);
    flex-shrink: 0;
}

.load-more-error wa-callout {
    --wa-callout-padding: var(--wa-space-s);
}

.load-more-error wa-callout span {
    font-size: var(--wa-font-size-s);
}

.empty-state {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: var(--wa-space-l);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}
</style>
