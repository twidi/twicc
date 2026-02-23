<script setup>
import { computed, ref, watch, onMounted, onUnmounted, provide } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import SessionList from '../components/SessionList.vue'
import FetchErrorPanel from '../components/FetchErrorPanel.vue'
import SettingsPopover from '../components/SettingsPopover.vue'
import ProjectBadge from '../components/ProjectBadge.vue'
import ProjectProcessIndicator from '../components/ProjectProcessIndicator.vue'
import ProjectDetailPanel from '../components/ProjectDetailPanel.vue'
import SessionRenameDialog from '../components/SessionRenameDialog.vue'
import { getUsageRingColor } from '../utils/usage'
import CostDisplay from '../components/CostDisplay.vue'
import AppTooltip from '../components/AppTooltip.vue'

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const settingsStore = useSettingsStore()

// Costs setting
const showCosts = computed(() => settingsStore.areCostsShown)

// ═══════════════════════════════════════════════════════════════════════════
// Usage quotas
// ═══════════════════════════════════════════════════════════════════════════

const quotaData = computed(() => store.usage)
const quotaHasOauth = computed(() => quotaData.value?.hasOauth ?? false)
const quotaComputed = computed(() => quotaData.value?.computed ?? null)

const quotaFiveHour = computed(() => quotaComputed.value?.fiveHour ?? null)
const quotaSevenDay = computed(() => quotaComputed.value?.sevenDay ?? null)

const quotaExtraUsage = computed(() => {
    // "Only when needed" mode: show only if 5h or 7d quota is at 100%
    if (settingsStore.isExtraUsageOnlyWhenNeeded) {
        const fh = quotaFiveHour.value
        const sd = quotaSevenDay.value
        const needed = (fh && fh.utilization >= 100) || (sd && sd.utilization >= 100)
        if (!needed) return null
    }
    const extra = quotaComputed.value?.extraUsage
    if (!extra || !extra.isEnabled) return null
    return extra
})

const quotaFiveHourCost = computed(() => quotaComputed.value?.fiveHourCost ?? null)
const quotaSevenDayCost = computed(() => quotaComputed.value?.sevenDayCost ?? null)

const quotaFiveHourRingColor = computed(() => getUsageRingColor(quotaFiveHour.value))
const quotaSevenDayRingColor = computed(() => getUsageRingColor(quotaSevenDay.value))
const quotaExtraUsageRingColor = computed(() => {
    const extra = quotaExtraUsage.value
    if (!extra || extra.utilization == null) return 'var(--wa-color-neutral)'
    if (extra.utilization >= 75) return 'var(--wa-color-danger)'
    if (extra.utilization >= 50) return 'var(--wa-color-warning)'
    return 'var(--wa-color-success)'
})

function resetsAtToDate(resetsAt) {
    if (!resetsAt) return new Date()
    return new Date(resetsAt)
}

function extraUsageResetDate() {
    const now = new Date()
    return new Date(now.getFullYear(), now.getMonth() + 1, 1)
}

function formatResetTime(resetsAt) {
    if (!resetsAt) return '?'
    const reset = resetsAt instanceof Date ? resetsAt : new Date(resetsAt)
    const now = new Date()
    const locale = navigator.language
    const diffMs = reset - now
    const diffHours = diffMs / (1000 * 60 * 60)
    // < 24h: time only
    if (diffHours < 24) {
        return reset.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
    }
    // < 7 days: weekday only
    if (diffHours < 7 * 24) {
        return reset.toLocaleDateString(locale, { weekday: 'long' })
    }
    // >= 7 days: weekday + day/month
    return reset.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'numeric' })
}

// Stale data detection: data older than 15 minutes
const STALE_THRESHOLD_MS = 15 * 60 * 1000
const STALE_CHECK_INTERVAL_MS = 60 * 1000 // Re-check every minute

// Reactive "now" that ticks every minute to trigger stale re-evaluation
const now = ref(Date.now())
const staleCheckInterval = setInterval(() => { now.value = Date.now() }, STALE_CHECK_INTERVAL_MS)
onUnmounted(() => clearInterval(staleCheckInterval))

const quotaIsStale = computed(() => {
    const fetchedAt = quotaComputed.value?.fetchedAt
    if (!fetchedAt) return false
    const fetchedDate = new Date(fetchedAt)
    return (now.value - fetchedDate.getTime()) > STALE_THRESHOLD_MS
})

const quotaLastUpdateFormatted = computed(() => {
    const fetchedAt = quotaComputed.value?.fetchedAt
    if (!fetchedAt) return '?'
    const date = new Date(fetchedAt)
    const locale = navigator.language
    return date.toLocaleString(locale, {
        day: '2-digit',
        month: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
})


// ═══════════════════════════════════════════════════════════════════════════
// Shared Rename Dialog (single instance for both sidebar and session header)
// ═══════════════════════════════════════════════════════════════════════════

const renameDialogRef = ref(null)
const sessionToRename = ref(null)

/**
 * Open the rename dialog for a session.
 * Provided to child components via provide/inject.
 * @param {Object} session - The session object to rename
 * @param {Object} [options] - Options passed to the dialog's open method
 * @param {boolean} [options.showHint] - Show contextual hint (for needs-title flow)
 */
function openRenameDialog(session, options = {}) {
    sessionToRename.value = session
    renameDialogRef.value?.open({ ...options, session })
}

provide('openRenameDialog', openRenameDialog)

// Current project from route params
const projectId = computed(() => route.params.projectId)
const sessionId = computed(() => route.params.sessionId || null)

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))

// Effective project ID for store operations
const effectiveProjectId = computed(() =>
    isAllProjectsMode.value ? ALL_PROJECTS_ID : projectId.value
)

// All projects for the selector (already sorted by mtime desc in store)
const allProjects = computed(() => store.getProjects)

// Selected project color for display in the closed select
const selectedProjectColor = computed(() => {
    if (isAllProjectsMode.value) return null
    const project = store.getProject(projectId.value)
    return project?.color || null
})

// Loading and error states for sessions
// Only show initial loading spinner when we haven't fetched any sessions yet
const areSessionsFetched = computed(() => store.areProjectSessionsFetched(effectiveProjectId.value))
const isInitialLoading = computed(() =>
    store.areSessionsLoading(effectiveProjectId.value) && !areSessionsFetched.value
)
const didSessionsFailToLoad = computed(() => store.didSessionsFailToLoad(effectiveProjectId.value))

// Search/filter state for sessions
const searchQuery = ref('')

// Show archived sessions filter (persists across project changes)
const showArchivedSessions = ref(false)

// Compact view: local override initialized from the setting.
// Toggling from the session list menu changes this local state without affecting the setting.
// When the setting changes (e.g. from the Settings panel), we re-sync to follow the new default.
const compactView = ref(settingsStore.isCompactSessionList)
watch(() => settingsStore.isCompactSessionList, (newValue) => {
    compactView.value = newValue
})

// Reference to SessionList for keyboard navigation
const sessionListRef = ref(null)

// Reference to the search input for focus management
const searchInputRef = ref(null)

/**
 * Focus the search input field.
 * Called when SessionList emits 'focus-search' (e.g., ArrowUp from first item).
 */
function focusSearchInput() {
    searchInputRef.value?.focus()
}

// Clear search when project changes
watch(effectiveProjectId, () => {
    searchQuery.value = ''
})

/**
 * Handle keyboard events from the search input.
 * Only delegates ArrowDown, Enter, and Escape to the SessionList component.
 * All other keys (Home, End, PageUp, PageDown, ArrowUp) keep their default
 * text input behavior so the user can navigate within the search field.
 *
 * @param {KeyboardEvent} event
 */
function handleSearchKeydown(event) {
    // From the search input, only these keys should be delegated to SessionList.
    // Other navigation keys (Home, End, PageUp, PageDown, ArrowUp) must keep
    // their default text input behavior (cursor movement, text selection, etc.).
    const navigationKeys = ['ArrowDown', 'Enter', 'Escape']

    if (navigationKeys.includes(event.key) && sessionListRef.value) {
        const handled = sessionListRef.value.handleKeyNavigation(event, { fromSearch: true })
        if (handled) {
            event.preventDefault()
            return
        }
    }

    // Escape not handled by SessionList (no highlight) → clear search field
    if (event.key === 'Escape' && searchQuery.value) {
        searchQuery.value = ''
        event.preventDefault()
    }
}

// Load sessions when project changes or mode changes
watch(effectiveProjectId, async (newProjectId) => {
    if (newProjectId) {
        await store.loadSessions(newProjectId, { isInitialLoading: true })
    }
}, { immediate: true })

// Retry loading sessions
async function handleRetry() {
    if (effectiveProjectId.value) {
        await store.loadSessions(effectiveProjectId.value, { force: true, isInitialLoading: true })
    }
}

// Handle session options dropdown selection
function handleSessionOptionsSelect(event) {
    const item = event.detail.item
    if (item.value === 'show-archived') {
        showArchivedSessions.value = item.checked
    } else if (item.value === 'compact-view') {
        compactView.value = item.checked
    }
}

// Handle project change from selector
function handleProjectChange(event) {
    const newProjectId = event.target.value
    if (newProjectId === ALL_PROJECTS_ID) {
        // If we have a session selected, keep it in "All Projects" mode
        if (sessionId.value && projectId.value) {
            const subagentId = route.params.subagentId
            if (subagentId) {
                router.push({ name: 'projects-session-subagent', params: { projectId: projectId.value, sessionId: sessionId.value, subagentId } })
            } else {
                router.push({ name: 'projects-session', params: { projectId: projectId.value, sessionId: sessionId.value } })
            }
        } else {
            router.push({ name: 'projects-all' })
        }
    } else if (newProjectId && newProjectId !== effectiveProjectId.value) {
        // If we have a session selected that belongs to this project, keep it
        if (sessionId.value && projectId.value === newProjectId) {
            const subagentId = route.params.subagentId
            if (subagentId) {
                router.push({ name: 'session-subagent', params: { projectId: newProjectId, sessionId: sessionId.value, subagentId } })
            } else {
                router.push({ name: 'session', params: { projectId: newProjectId, sessionId: sessionId.value } })
            }
        } else {
            router.push({ name: 'project', params: { projectId: newProjectId } })
        }
    }
}

// Handle session selection (toggle: clicking already-selected session deselects it)
function handleSessionSelect(session) {
    if (session.id === sessionId.value) {
        // Deselect: navigate back to project root
        if (isAllProjectsMode.value) {
            router.push({ name: 'projects-all' })
        } else {
            router.push({ name: 'project', params: { projectId: projectId.value } })
        }
    } else if (isAllProjectsMode.value) {
        router.push({
            name: 'projects-session',
            params: { projectId: session.project_id, sessionId: session.id }
        })
    } else {
        router.push({
            name: 'session',
            params: { projectId: projectId.value, sessionId: session.id }
        })
    }
}

// Navigate back to home
function handleBackHome() {
    router.push({ name: 'home' })
}

// Create a new draft session and navigate to it
// In single project mode: uses current projectId
// In all projects mode: requires explicit targetProjectId parameter
function handleNewSession(targetProjectId = null) {
    const projectIdToUse = targetProjectId || projectId.value
    if (!projectIdToUse) return

    const newSessionId = store.createDraftSession(projectIdToUse)

    // Navigate to appropriate route based on current mode
    if (isAllProjectsMode.value) {
        router.push({
            name: 'projects-session',
            params: { projectId: projectIdToUse, sessionId: newSessionId }
        })
    } else {
        router.push({
            name: 'session',
            params: { projectId: projectIdToUse, sessionId: newSessionId }
        })
    }
}

// Sidebar state persistence
const SIDEBAR_STORAGE_KEY = 'twicc-sidebar-state'
const DEFAULT_SIDEBAR_WIDTH = 300
// Sidebar collapse threshold in pixels
const SIDEBAR_COLLAPSE_THRESHOLD = 120
// Mobile breakpoint (must match CSS media query)
const MOBILE_BREAKPOINT = 640

// Load sidebar state from localStorage
function loadSidebarState() {
    try {
        const stored = localStorage.getItem(SIDEBAR_STORAGE_KEY)
        if (stored) {
            const parsed = JSON.parse(stored)
            return {
                open: typeof parsed.open === 'boolean' ? parsed.open : true,
                width: typeof parsed.width === 'number' && parsed.width > 0 ? parsed.width : DEFAULT_SIDEBAR_WIDTH,
            }
        }
    } catch (e) {
        console.warn('Failed to load sidebar state from localStorage:', e)
    }
    return { open: true, width: DEFAULT_SIDEBAR_WIDTH }
}

// Save sidebar state to localStorage
function saveSidebarState(state) {
    try {
        localStorage.setItem(SIDEBAR_STORAGE_KEY, JSON.stringify(state))
    } catch (e) {
        console.warn('Failed to save sidebar state to localStorage:', e)
    }
}

// Current sidebar state (non-reactive, applied once at mount)
const sidebarState = loadSidebarState()

// Check if we're on mobile (for initial sidebar state)
const isMobile = () => window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`).matches

// Initial checkbox state:
// - On mobile: checked = open, so check when no session
// - On desktop: checked = closed, so check if sidebar was closed (inverted logic)
const initialSidebarChecked = computed(() => {
    if (typeof window === 'undefined') return false
    if (isMobile()) {
        return !sessionId.value
    }
    // Desktop: checkbox checked means closed, so invert the stored "open" state
    return !sidebarState.open
})

// Track all route changes for MRU (Most Recently Used) navigation.
// Stores the full path (including sub-routes like /files, /git, /terminal)
// so we can restore the exact view when navigating back after archiving.
watch(() => route.fullPath, (newPath) => {
    store.touchMruPath(newPath, sessionId.value)
}, { immediate: true })

// Navigate to previous MRU path when the current session gets archived.
// Uses a computed to reactively track the archived state of the current session,
// so this works regardless of where the archive action was triggered
// (session list menu, session header button, etc.).
const currentSessionArchived = computed(() => {
    if (!sessionId.value) return false
    return store.sessions[sessionId.value]?.archived ?? false
})

watch(currentSessionArchived, (archived) => {
    if (!archived) return
    if (showArchivedSessions.value) return  // session stays visible, no need to navigate

    const nextPath = store.getNextMruPath(sessionId.value)
    if (nextPath) {
        router.push(nextPath)
    }
})

// On mobile, close sidebar when session changes
watch(sessionId, (newSessionId) => {
    if (!newSessionId) return
    // Only on mobile
    if (!isMobile()) return

    const checkbox = document.getElementById('sidebar-toggle-state')
    if (checkbox) {
        checkbox.checked = false
    }
    updateSidebarClosedClass(true)
})

// Reset sidebar to default width on divider double-click.
// wa-split-panel's drag handler calls preventDefault() on mousedown, which prevents
// native dblclick events from firing. We detect double-clicks manually by tracking
// rapid consecutive pointerdown events on the split panel host element, filtering
// only those whose composedPath includes the shadow DOM divider.
function resetSidebarToDefault() {
    if (isMobile()) return
    const splitPanel = document.querySelector('.project-view')
    if (splitPanel) {
        ignoringReposition = true
        splitPanel.positionInPixels = DEFAULT_SIDEBAR_WIDTH
        requestAnimationFrame(() => {
            ignoringReposition = false
        })
        sidebarState.width = DEFAULT_SIDEBAR_WIDTH
        lastKnownPosition = DEFAULT_SIDEBAR_WIDTH
        saveSidebarState({ open: true, width: DEFAULT_SIDEBAR_WIDTH })
        updateSidebarClosedClass(false)
    }
}

const DOUBLE_CLICK_DELAY = 400 // ms
let lastDividerPointerDown = 0

function handleSplitPanelPointerDown(event) {
    // Only react to pointerdown events that originate from the divider
    // (check composedPath to see through shadow DOM boundary)
    const splitPanel = event.currentTarget
    const path = event.composedPath()
    if (!path.includes(splitPanel.divider)) return

    const now = Date.now()
    if (now - lastDividerPointerDown < DOUBLE_CLICK_DELAY) {
        lastDividerPointerDown = 0
        resetSidebarToDefault()
    } else {
        lastDividerPointerDown = now
    }
}

// Apply stored sidebar width and body class once on mount
onMounted(() => {
    const splitPanel = document.querySelector('.project-view')
    if (splitPanel) {
        if (sidebarState.width !== DEFAULT_SIDEBAR_WIDTH) {
            splitPanel.positionInPixels = sidebarState.width
        }
        lastKnownPosition = sidebarState.open ? sidebarState.width : 0
        // Listen for pointerdown on the host element (capture phase) to detect
        // double-clicks on the divider. Native dblclick is blocked by the drag handler.
        splitPanel.addEventListener('pointerdown', handleSplitPanelPointerDown, true)
    }
    // Set initial sidebar-closed class on body
    if (isMobile()) {
        updateSidebarClosedClass(!!sessionId.value)
    } else {
        updateSidebarClosedClass(!sidebarState.open)
    }
})

// Guard flag to ignore reposition events triggered by width restore after auto-collapse
let ignoringReposition = false
// Track the last known position to detect spurious reposition events (e.g. from
// KeepAlive reactivation or layout recalculations) that jump from a normal width
// to near-zero in a single step — no human drag can do that.
let lastKnownPosition = DEFAULT_SIDEBAR_WIDTH

// Handle split panel reposition: auto-collapse when dragged to threshold, and persist width.
// Ignored on mobile where the split panel is not used (sidebar is an overlay).
function handleSplitReposition(event) {
    // Ignore events bubbling from nested wa-split-panels (e.g. FilesPanel's tree/content splitter).
    // Only handle events from our own split panel.
    if (event.target !== event.currentTarget) return

    if (isMobile()) return
    if (ignoringReposition) return

    const checkbox = document.getElementById('sidebar-toggle-state')
    if (!checkbox) return

    const newWidth = event.target.positionInPixels

    // Ignore null/NaN positions emitted during KeepAlive transitions.
    // wa-split-panel fires wa-reposition with positionInPixels = null when
    // the component is deactivated/reactivated by KeepAlive.
    if (newWidth == null || Number.isNaN(newWidth)) return

    if (newWidth <= SIDEBAR_COLLAPSE_THRESHOLD) {
        // Ignore spurious collapse: if the sidebar was at a normal width and jumped
        // to near-zero in one step, this is a layout recalculation (e.g. KeepAlive
        // reactivation), not a user drag. Restore the stored width instead.
        if (lastKnownPosition > SIDEBAR_COLLAPSE_THRESHOLD * 2) {
            ignoringReposition = true
            requestAnimationFrame(() => {
                event.target.positionInPixels = sidebarState.width
                requestAnimationFrame(() => {
                    ignoringReposition = false
                    lastKnownPosition = sidebarState.width
                })
            })
            return
        }
        // Auto-collapse: mark as closed, reset width to stored value
        lastKnownPosition = 0
        checkbox.checked = true
        saveSidebarState({ open: false, width: sidebarState.width })
        updateSidebarClosedClass(true)
        // Restore width, ignoring the resulting reposition event
        ignoringReposition = true
        requestAnimationFrame(() => {
            event.target.positionInPixels = sidebarState.width
            requestAnimationFrame(() => {
                ignoringReposition = false
            })
        })
    } else {
        // Normal resize: update stored width
        lastKnownPosition = newWidth
        sidebarState.width = newWidth
        saveSidebarState({ open: true, width: newWidth })
        updateSidebarClosedClass(false)
    }
}

// Handle sidebar toggle (called when checkbox changes)
function handleSidebarToggle(event) {
    const checked = event.target.checked
    if (isMobile()) {
        // Mobile: checked = open
        updateSidebarClosedClass(!checked)
    } else {
        // Desktop: checked = closed
        const isOpen = !checked
        lastKnownPosition = isOpen ? sidebarState.width : 0
        saveSidebarState({ open: isOpen, width: sidebarState.width })
        updateSidebarClosedClass(checked)
    }
}

// Toggle body class to indicate sidebar is closed.
// Used by child components (e.g. MessageInput) to adjust layout
// when the sidebar toggle button overlaps their content.
function updateSidebarClosedClass(closed) {
    document.body.classList.toggle('sidebar-closed', closed)
}
</script>

<template>
    <div class="project-view-wrapper">
        <!-- Hidden checkbox for pure CSS sidebar toggle -->
        <input type="checkbox" id="sidebar-toggle-state" class="sidebar-toggle-checkbox" :checked="initialSidebarChecked" @change="handleSidebarToggle"/>

        <wa-split-panel
            class="project-view"
            :position-in-pixels="DEFAULT_SIDEBAR_WIDTH"
            primary="start"
            snap="125px 200px 300px 400px"
            snap-threshold="30"
            @wa-reposition="handleSplitReposition"
        >
            <!-- Divider handle for touch devices -->
            <wa-icon slot="divider" name="grip-lines-vertical" class="divider-handle"></wa-icon>

            <!-- Sidebar -->
        <aside slot="start" class="sidebar">
            <div class="sidebar-header">
                <div class="sidebar-header-row">
                    <wa-button id="back-button" class="back-button" variant="brand" appearance="outlined" size="small" @click="handleBackHome">
                        <wa-icon name="arrow-left"></wa-icon>
                    </wa-button>
                    <AppTooltip for="back-button">Back to projects list</AppTooltip>
                    <wa-select
                        id="project-selector"
                        :value.attr="isAllProjectsMode ? ALL_PROJECTS_ID : projectId"
                        @change="handleProjectChange"
                        class="project-selector"
                        size="small"
                    >
                        <span
                            v-if="!isAllProjectsMode"
                            slot="start"
                            class="selected-project-dot"
                            :style="selectedProjectColor ? { '--dot-color': selectedProjectColor } : null"
                        ></span>
                        <wa-option :value="ALL_PROJECTS_ID">
                            All Projects
                        </wa-option>
                        <wa-divider></wa-divider>
                        <wa-option
                            v-for="p in allProjects"
                            :key="p.id"
                            :value="p.id"
                            :label="store.getProjectDisplayName(p.id)"
                        >
                            <span class="project-option">
                                <ProjectBadge :project-id="p.id" />
                                <ProjectProcessIndicator :project-id="p.id" size="small" />
                            </span>
                        </wa-option>
                    </wa-select>
                </div>

                <div class="sidebar-header-row">
                    <!-- Session list options dropdown -->
                    <wa-dropdown
                        placement="bottom-end"
                        class="session-options-dropdown"
                        @wa-select="handleSessionOptionsSelect"
                    >
                        <wa-button
                            id="session-options-button"
                            slot="trigger"
                            variant="neutral"
                            appearance="filled-outlined"
                            size="small"
                        >
                            <wa-icon name="sliders"></wa-icon>
                        </wa-button>
                        <wa-dropdown-item
                            type="checkbox"
                            value="show-archived"
                            :checked="showArchivedSessions"
                        >
                            Show archived sessions
                        </wa-dropdown-item>
                        <wa-dropdown-item
                            type="checkbox"
                            value="compact-view"
                            :checked="compactView"
                        >
                            Compact view
                        </wa-dropdown-item>
                    </wa-dropdown>
                    <AppTooltip for="session-options-button">Session list options</AppTooltip>

                    <!-- Search/filter input -->
                    <wa-input
                        ref="searchInputRef"
                        v-model="searchQuery"
                        placeholder="Filter sessions..."
                        size="small"
                        with-clear
                        class="session-search"
                        @keydown="handleSearchKeydown"
                    >
                        <wa-icon slot="start" name="magnifying-glass"></wa-icon>
                    </wa-input>
                </div>
            </div>

            <wa-divider></wa-divider>

            <div class="sidebar-sessions">
                <!-- Error state (only for initial load failure) -->
                <FetchErrorPanel
                    v-if="didSessionsFailToLoad && !areSessionsFetched"
                    :loading="isInitialLoading"
                    @retry="handleRetry"
                >
                    Failed to load sessions
                </FetchErrorPanel>

                <!-- Initial loading state (only before first fetch) -->
                <div v-else-if="isInitialLoading" class="sessions-loading">
                    <wa-spinner></wa-spinner>
                    <span>Loading...</span>
                </div>

                <!-- Normal content (shown once we have sessions, handles its own "load more" state) -->
                <SessionList
                    v-else
                    ref="sessionListRef"
                    :project-id="effectiveProjectId"
                    :session-id="sessionId"
                    :show-project-name="isAllProjectsMode"
                    :search-query="searchQuery"
                    :show-archived="showArchivedSessions"
                    :compact-view="compactView"
                    @select="handleSessionSelect"
                    @focus-search="focusSearchInput"
                />

                <!-- Floating "New session" button -->
                <!-- In single project mode: split button (main action + dropdown for other projects) -->
                <wa-button-group
                    v-if="!isAllProjectsMode"
                    class="new-session-split-button"
                    label="New session actions"
                >
                    <!-- Main button: creates session in current project -->
                    <wa-button
                        id="new-session-button"
                        variant="brand"
                        appearance="accent"
                        size="small"
                        @click="handleNewSession()"
                    >
                        <wa-icon name="plus"></wa-icon>
                        <span>New session</span>
                    </wa-button>

                    <!-- Dropdown arrow: choose a different project -->
                    <wa-dropdown
                        placement="top-end"
                        @wa-select="(e) => handleNewSession(e.detail.item.value)"
                    >
                        <wa-button
                            id="new-session-project-picker"
                            slot="trigger"
                            variant="brand"
                            appearance="accent"
                            size="small"
                        >
                            <wa-icon name="chevron-up" label="Choose another project"></wa-icon>
                        </wa-button>
                        <wa-dropdown-item
                            v-for="p in allProjects"
                            :key="p.id"
                            :value="p.id"
                        >
                            <ProjectBadge :project-id="p.id" />
                        </wa-dropdown-item>
                    </wa-dropdown>
                    <AppTooltip for="new-session-button">Create a new session in this project</AppTooltip>
                    <AppTooltip for="new-session-project-picker">Choose a different project</AppTooltip>
                </wa-button-group>

                <!-- In all projects mode: dropdown to choose project -->
                <wa-dropdown
                    v-if="isAllProjectsMode"
                    id="new-session-dropdown"
                    class="new-session-dropdown"
                    placement="top-end"
                    @wa-select="(e) => handleNewSession(e.detail.item.value)"
                >
                    <wa-button
                        id="new-session-all-projects-button"
                        slot="trigger"
                        variant="brand"
                        appearance="accent"
                        size="small"
                    >
                        <wa-icon slot="end" name="chevron-up"></wa-icon>
                        <wa-icon name="plus"></wa-icon>
                        <span>New session</span>
                    </wa-button>
                    <wa-dropdown-item
                        v-for="p in allProjects"
                        :key="p.id"
                        :value="p.id"
                    >
                        <ProjectBadge :project-id="p.id" />
                    </wa-dropdown-item>
                </wa-dropdown>
                <AppTooltip v-if="isAllProjectsMode" for="new-session-all-projects-button">Create a new session</AppTooltip>
            </div>

            <wa-divider></wa-divider>

            <div class="sidebar-footer">
                <div v-if="quotaHasOauth && quotaComputed" class="sidebar-footer-usage">
                    <div id="quota-five-hour" class="usage-quota" v-if="quotaFiveHour">
                        <wa-progress-ring
                            class="usage-ring"
                            :value="Math.min(quotaFiveHour.utilization ?? 0, 100)"
                            :style="{ '--indicator-color': quotaFiveHourRingColor }"
                        ><span class="wa-font-weight-bold">{{ Math.round(quotaFiveHour.utilization ?? 0) }}%</span></wa-progress-ring>
                        <div class="usage-quota-info">
                            <span class="usage-quota-label">5h quota</span>
                            <wa-relative-time v-if="quotaFiveHour.resetsAt" class="usage-quota-reset" :date.prop="resetsAtToDate(quotaFiveHour.resetsAt)" format="short" numeric="always" sync></wa-relative-time>
                        </div>
                    </div>
                    <AppTooltip v-if="quotaFiveHour" for="quota-five-hour" hoist force>
                        <div class="quota-tooltip">
                            <div class="quota-tooltip-row"><span class="quota-tooltip-label">Usage</span><span>{{ (quotaFiveHour.utilization ?? 0).toFixed(1) }}%</span></div>
                            <div class="quota-tooltip-note" v-if="!quotaFiveHour.resetsAt"><wa-icon name="info-circle"></wa-icon> Period not started yet</div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.timePct != null"><span class="quota-tooltip-label">Time elapsed</span><span>{{ quotaFiveHour.timePct.toFixed(1) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.burnRate != null"><span class="quota-tooltip-label">Burn rate</span><span>{{ (quotaFiveHour.burnRate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row quota-tooltip-row-danger" v-if="showCosts && quotaFiveHourCost?.cutoffAt"><span class="quota-tooltip-label"><wa-icon name="triangle-exclamation"></wa-icon> Cutoff</span><span>{{ formatResetTime(quotaFiveHourCost.cutoffAt) }}</span></div>
                            <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="showCosts && quotaFiveHourCost?.cutoffAt"><wa-icon name="triangle-exclamation"></wa-icon> Quota will be exhausted at current pace</div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.resetsAt"><span class="quota-tooltip-label">Reset</span><span>{{ formatResetTime(quotaFiveHour.resetsAt) }}</span></div>
                            <template v-if="showCosts && quotaFiveHourCost && quotaFiveHourCost.spent != null">
                                <wa-divider class="quota-tooltip-divider"></wa-divider>
                                <div class="quota-tooltip-row"><span class="quota-tooltip-label">Spent</span><CostDisplay :cost="quotaFiveHourCost.spent" /></div>
                                <div class="quota-tooltip-row" v-if="quotaFiveHourCost.estimatedPeriod != null"><span class="quota-tooltip-label">Est. 5h</span><CostDisplay :cost="quotaFiveHourCost.estimatedPeriod" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaFiveHourCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Capped — burn rate exceeds 100%</div>
                                <div class="quota-tooltip-row" v-if="quotaFiveHourCost.estimatedMonthly != null"><span class="quota-tooltip-label">Est. 30 days</span><CostDisplay :cost="quotaFiveHourCost.estimatedMonthly" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaFiveHourCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Based on capped 5h estimate</div>
                            </template>
                            <wa-button size="small" variant="brand" appearance="outlined" href="https://claude.ai/settings/usage" target="_blank" rel="noopener" class="quota-stale-button">View on claude.ai</wa-button>
                        </div>
                    </AppTooltip>
                    <div id="quota-seven-day" class="usage-quota" v-if="quotaSevenDay">
                        <wa-progress-ring
                            class="usage-ring"
                            :value="Math.min(quotaSevenDay.utilization ?? 0, 100)"
                            :style="{ '--indicator-color': quotaSevenDayRingColor }"
                        ><span class="wa-font-weight-bold">{{ Math.round(quotaSevenDay.utilization ?? 0) }}%</span></wa-progress-ring>
                        <div class="usage-quota-info">
                            <span class="usage-quota-label">7d quota</span>
                            <wa-relative-time v-if="quotaSevenDay.resetsAt" class="usage-quota-reset" :date.prop="resetsAtToDate(quotaSevenDay.resetsAt)" format="short" numeric="always" sync></wa-relative-time>
                        </div>
                    </div>
                    <AppTooltip v-if="quotaSevenDay" for="quota-seven-day" hoist force>
                        <div class="quota-tooltip">
                            <div class="quota-tooltip-row"><span class="quota-tooltip-label">Usage</span><span>{{ (quotaSevenDay.utilization ?? 0).toFixed(1) }}%</span></div>
                            <div class="quota-tooltip-note" v-if="!quotaSevenDay.resetsAt"><wa-icon name="info-circle"></wa-icon> Period not started yet</div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.timePct != null"><span class="quota-tooltip-label">Time elapsed</span><span>{{ quotaSevenDay.timePct.toFixed(1) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.burnRate != null"><span class="quota-tooltip-label">Burn rate</span><span>{{ (quotaSevenDay.burnRate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row quota-tooltip-row-danger" v-if="showCosts && quotaSevenDayCost?.cutoffAt"><span class="quota-tooltip-label"><wa-icon name="triangle-exclamation"></wa-icon> Cutoff</span><span>{{ formatResetTime(quotaSevenDayCost.cutoffAt) }}</span></div>
                            <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="showCosts && quotaSevenDayCost?.cutoffAt"><wa-icon name="triangle-exclamation"></wa-icon> Quota will be exhausted at current pace</div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.resetsAt"><span class="quota-tooltip-label">Reset</span><span>{{ formatResetTime(quotaSevenDay.resetsAt) }}</span></div>
                            <template v-if="showCosts && quotaSevenDayCost && quotaSevenDayCost.spent != null">
                                <wa-divider class="quota-tooltip-divider"></wa-divider>
                                <div class="quota-tooltip-row"><span class="quota-tooltip-label">Spent</span><CostDisplay :cost="quotaSevenDayCost.spent" /></div>
                                <div class="quota-tooltip-row" v-if="quotaSevenDayCost.estimatedPeriod != null"><span class="quota-tooltip-label">Est. 7d</span><CostDisplay :cost="quotaSevenDayCost.estimatedPeriod" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaSevenDayCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Capped — burn rate exceeds 100%</div>
                                <div class="quota-tooltip-row" v-if="quotaSevenDayCost.estimatedMonthly != null"><span class="quota-tooltip-label">Est. 30 days</span><CostDisplay :cost="quotaSevenDayCost.estimatedMonthly" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaSevenDayCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Based on capped 7d estimate</div>
                            </template>
                            <wa-button size="small" variant="brand" appearance="outlined" href="https://claude.ai/settings/usage" target="_blank" rel="noopener" class="quota-stale-button">View on claude.ai</wa-button>
                        </div>
                    </AppTooltip>
                    <div id="quota-extra-usage" class="usage-quota" v-if="quotaExtraUsage">
                        <wa-progress-ring
                            class="usage-ring"
                            :value="Math.min(quotaExtraUsage.utilization ?? 0, 100)"
                            :style="{ '--indicator-color': quotaExtraUsageRingColor }"
                        ><span class="wa-font-weight-bold">{{ Math.round(quotaExtraUsage.utilization ?? 0) }}%</span></wa-progress-ring>
                        <div class="usage-quota-info">
                            <span class="usage-quota-label">Extra usage</span>
                            <wa-relative-time class="usage-quota-reset" :date.prop="extraUsageResetDate()" format="short" numeric="always" sync></wa-relative-time>
                        </div>
                    </div>
                    <AppTooltip v-if="quotaExtraUsage" for="quota-extra-usage" hoist force>
                        <div class="quota-tooltip">
                            <div class="quota-tooltip-row"><span class="quota-tooltip-label">Used</span><span>{{ quotaExtraUsage.usedCredits ?? 0 }} credits</span></div>
                            <div class="quota-tooltip-row"><span class="quota-tooltip-label">Monthly limit</span><span>{{ quotaExtraUsage.monthlyLimit ?? '?' }} credits</span></div>
                            <div class="quota-tooltip-row"><span class="quota-tooltip-label">Reset</span><span>{{ formatResetTime(extraUsageResetDate()) }}</span></div>
                            <wa-button size="small" variant="brand" appearance="outlined" href="https://claude.ai/settings/usage" target="_blank" rel="noopener" class="quota-stale-button">View on claude.ai</wa-button>
                        </div>
                    </AppTooltip>
                    <wa-icon v-if="quotaIsStale" id="quota-stale-warning" name="triangle-exclamation" class="quota-stale-icon"></wa-icon>
                    <AppTooltip v-if="quotaIsStale" for="quota-stale-warning" hoist force>
                        <div class="quota-tooltip">
                            <div class="quota-stale-header"><wa-icon name="triangle-exclamation" class="quota-stale-header-icon"></wa-icon><span>Data may be outdated</span></div>
                            <div class="quota-tooltip-row"><span class="quota-tooltip-label">Last update</span><span>{{ quotaLastUpdateFormatted }}</span></div>
                            <wa-button size="small" variant="brand" appearance="outlined" href="https://claude.ai/settings/usage" target="_blank" rel="noopener" class="quota-stale-button">View usage on claude.ai</wa-button>
                        </div>
                    </AppTooltip>
                </div>

                <wa-divider></wa-divider>

                <div class="sidebar-footer-buttons">
                    <!-- Sidebar Toggle button (label for hidden checkbox, wa-button inside for styling) -->
                    <label for="sidebar-toggle-state" class="sidebar-toggle" id="sidebar-toggle-label">
                        <span class="sidebar-backdrop"></span>
                        <wa-button id="sidebar-toggle-button" variant="neutral" appearance="filled-outlined" size="small">
                            <wa-icon class="icon-collapse" name="angles-left"></wa-icon>
                            <wa-icon class="icon-expand" name="angles-right"></wa-icon>
                        </wa-button>
                    </label>
                    <AppTooltip for="sidebar-toggle-label">Toggle sidebar</AppTooltip>

                    <!-- Placeholder to occupy the same space a the sidebar toggle button that is absolute for goot reasons -->
                    <wa-button variant="neutral" appearance="filled-outlined" size="small" style="visibility: hidden; pointer-events: none"><wa-icon name="angles-left"></wa-icon></wa-button>

                    <SettingsPopover />
                </div>
            </div>

        </aside>

        <!-- Main content area -->
        <main slot="end" class="main-content">
            <div v-show="sessionId" class="session-content">
                <router-view v-slot="{ Component }">
                    <KeepAlive :max="settingsStore.getMaxCachedSessions">
                        <component :is="Component" :key="route.params.sessionId" />
                    </KeepAlive>
                </router-view>
            </div>
            <ProjectDetailPanel v-if="!sessionId" :project-id="effectiveProjectId" />
        </main>
    </wa-split-panel>

    <!-- Shared rename dialog (single instance for sidebar + session header) -->
    <SessionRenameDialog
        ref="renameDialogRef"
        :session="sessionToRename"
    />
    </div>
</template>

<style scoped>
.project-view-wrapper {
    height: 100vh;
    height: 100dvh;
}

/* Hidden checkbox for CSS-only toggle */
.sidebar-toggle-checkbox {
    position: absolute;
    opacity: 0;
    pointer-events: none;
}

.project-view {
    height: 100%;
    --min: 20px;
    --max: 500px;
    transition: grid-template-columns var(--transition-duration) ease;
    &::part(divider) {
        z-index: 2;
        transition: opacity var(--transition-duration) ease;
    }
}

wa-split-panel::part(divider) {
    /* same color/width as normal dividers */
    background-color: var(--wa-color-surface-border);
    width: 4px;
}
/* Divider handle: hidden by default, shown only on touch devices */
.divider-handle {
    color: var(--wa-color-surface-border);
    display: none;
    scale: 3;
}

@media (pointer: coarse) {
    .divider-handle {
        display: inline;
    }
}

.sidebar {
    --transition-duration: .3s;
    height: 100vh;
    height: 100dvh;
    background: var(--wa-color-surface-default);
    display: flex;
    flex-direction: column;
    position: relative;
    /* Enable container queries on the sidebar */
    container-type: inline-size;
    container-name: sidebar;
}

.sidebar-header {
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    justify-content: stretch;
    padding: var(--wa-space-s);
    gap: var(--wa-space-s);
    background: var(--main-header-footer-bg-color);
}

.sidebar-header-row {
    width: 100%;
    display: flex;
    justify-content: stretch;
    align-items: center;
    gap: var(--wa-space-s);
}

.session-search {
    flex: 1;
    min-width: 3.5rem;
    max-width: 100%;
    &:hover {
        z-index: 10;
        min-width: min(10rem, calc(100vw - 100px));
    }
}

.session-options-dropdown {
    flex-shrink: 0;
}

.project-selector {
    flex: 1;
    /* we allow options to be larger than the width of the select */
    &::part(listbox) {
        overflow: visible;
        width: max-content;
    }
    wa-option {
        max-width: min(400px, calc(100vw - 100px));
    }
    &:hover {
        z-index: 10;
        min-width: min(10rem, calc(100vw - 100px));
    }
}
@container sidebar (width <= 13rem) {
    .project-selector {
        &::part(form-control-input) {
            xwidth: min-content;
        }
        &::part(combobox) {
            padding-inline: var(--wa-space-xs);
        }
        &::part(display-input) {
        }
        .selected-project-dot {
            margin-inline-end: var(--wa-space-xs);
        }
        &::part(expand-icon) {
            margin-inline-start: var(--wa-space-xs);
        }
    }
}

.selected-project-dot {
    width: 0.75em;
    height: 0.75em;
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

.project-option {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    width: 100%;
    justify-content: space-between;
}

.sidebar wa-divider {
    flex-shrink: 0;
    --width: 4px;
    --spacing: 0;
}

.sidebar-sessions {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    padding: 0;
    display: flex;
    flex-direction: column;
    position: relative;
}

.main-content {
    flex: 1;
    min-width: 0;
    height: 100%;
    overflow: hidden;
    background: var(--wa-color-surface-default);
    z-index: 1;
    container-type: inline-size;
    container-name: main-content;
}

.session-content {
    height: 100%;
}

.sessions-loading {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-xl);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

/* Floating "New session" split button (single project mode) */
.new-session-split-button {
    position: absolute;
    bottom: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 5;

    /* Style the main button label */
    & > wa-button::part(label) {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }

    /* Visual separator between main button and dropdown trigger */
    & wa-dropdown wa-button::part(base) {
        border-left: 1px solid rgba(255, 255, 255, 0.3);
    }

    /* Limit dropdown panel height for scrolling */
    & wa-dropdown::part(panel) {
        max-height: 300px;
        overflow-y: auto;
    }
}

/* New session dropdown (for All Projects mode) - floating like the button */
.new-session-dropdown {
    /* Override display:contents to allow absolute positioning */
    display: block;
    position: absolute;
    bottom: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 5;
    /* Only take the width needed by the trigger button */
    width: fit-content;

    /* Style the trigger button label */
    & > wa-button::part(label) {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }

    /* Limit dropdown panel height for scrolling */
    &::part(panel) {
        max-height: 300px;
        overflow-y: auto;
    }
}


@container sidebar (width <= 13rem) {
    /* Split button in narrow sidebar */
    .new-session-split-button {
        & > wa-button {
            &::part(base) {
                padding: var(--wa-space-s);
            }
            & > span {
                display: none;
            }
        }
    }
    /* Dropdown button in All Projects mode */
    .new-session-dropdown > wa-button {
        &::part(base) {
            padding: var(--wa-space-s);
        }
        &::part(caret) {
            display: none;
        }
        & > span {
            display: none;
        }
    }
}

.sidebar-footer {
    flex-shrink: 0;
}

.sidebar-footer-usage {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: center;
    column-gap: var(--wa-space-l);
    row-gap: var(--wa-space-xs);
    padding: var(--wa-space-xs) var(--wa-space-s);
}

.usage-quota {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    max-width: 100%;
}

.usage-ring {
    --size: 2rem;
    --track-width: 3px;
    font-size: var(--wa-font-size-2xs);
}

.usage-quota-info {
    display: flex;
    flex-direction: column;
    line-height: 1.2;
}

/* Remove text in narrow sidebar */
@container sidebar (width <= 15rem) {
    .usage-quota-info {
        display: none;
    }
}

.usage-quota-label {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-bold);
    color: var(--wa-color-neutral-content);
}

.usage-quota-reset {
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-neutral-muted);
}

.quota-tooltip {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.quota-tooltip-row {
    display: flex;
    justify-content: space-between;
    gap: var(--wa-space-l);
    white-space: nowrap;
}

.quota-tooltip-label {
    font-weight: var(--wa-font-weight-bold);
}

.quota-tooltip-divider {
    --spacing: var(--wa-space-2xs);
}

.quota-tooltip-note {
    font-size: var(--wa-font-size-2xs);
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.quota-tooltip-row-danger {
    color: var(--wa-color-danger);
}

.quota-stale-icon {
    color: var(--wa-color-warning);
    font-size: var(--wa-font-size-l);
}

.quota-stale-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    font-weight: var(--wa-font-weight-bold);
}

.quota-stale-header-icon {
    color: var(--wa-color-warning);
}

.quota-stale-button {
    align-self: stretch;
}

.sidebar-footer-buttons {
    flex-shrink: 0;
    display: flex;
    gap: var(--wa-space-s);
    align-items: center;
    justify-content: space-between;
    padding: var(--wa-space-s);
    position: relative;
    background: var(--main-header-footer-bg-color);
}

/* Sidebar toggle label */
.sidebar-toggle {
    position: absolute;
    bottom: var(--wa-space-s);
    left: var(--wa-space-s);
    z-index: 10;
    cursor: pointer;

    /* wa-button inside label: disable pointer events so clicks go to label */
    wa-button {
        pointer-events: none;
        wa-icon {
            position: relative;
            top: -1px;
        }
    }

    /* Default state: sidebar is expanded, show collapse icon */
    .icon-collapse {
        display: inline;
    }
    .icon-expand {
        display: none;
    }
}

/* Container query: when sidebar is collapsed (≤ 50px), show expand icon */
@container sidebar (width <= 50px) {
    .sidebar-toggle .icon-collapse {
        display: none;
    }

    .sidebar-toggle .icon-expand {
        display: inline;
    }
}

/* Desktop: checkbox checked = sidebar collapsed */
.project-view-wrapper:has(.sidebar-toggle-checkbox:checked) .project-view {
    grid-template-columns: 0 var(--divider-width) auto !important;
    &::part(divider) {
        opacity: 0;
        pointer-events: none;
    }
}

/* Media query: mobile behavior - sidebar as overlay */
@media (width < 640px) {
    /* Use dynamic viewport height on mobile to account for browser chrome */
    .project-view-wrapper {
        height: 100vh;
        height: 100dvh;
    }

    /* Split panel always shows content at full width, so replace grid of project view by a block, sidebar will be an overlay */
    .project-view {
        display: block;
        &::part(divider) {
            display: none;
        }

    }

    /* Sidebar becomes a fixed drawer */
    .sidebar {
        --sidebar-width: min(300px, 80vw);
        position: absolute;
        left: 0;
        top: 0;
        width: var(--sidebar-width);
        height: 100vh;
        height: 100dvh;
        z-index: 100;
        transform: translateX(-100%);
        transition: transform var(--transition-duration) ease;
        box-shadow: var(--wa-shadow-xl);
        border-right: solid var(--wa-color-neutral-border-normal) 0.25rem;
    }

    /* Toggle button sticks out from the sidebar when closed */
    .sidebar-toggle {
        /* Position at right edge of sidebar, offset to stick out */
        transform: translateX(var(--sidebar-width));
        transition: transform var(--transition-duration) ease;
        .icon-collapse {
            display: none;
        }
        .icon-expand {
            display: inline;
        }
    }

    /* Backdrop positioned sticky inside the label
       so clicking on it closes the sidebar */
    .sidebar-toggle {
        /* try to reproduce the same size as the button
          else the label expands because of the backdrop sticky positioned */
        --checkbox-label-size: calc(var(--wa-form-control-height) - var(--wa-shadow-offset-y-s) * 2 + 2px);
        height: var(--checkbox-label-size);
        width: var(--checkbox-label-size);
        wa-button {
            position: absolute;
            top: 0;
            left: 0;
        }
        .sidebar-backdrop {
            display: block;
            position: sticky;
            top: 0;
            left: 0;
            /* Its top starts from the label so we have to move it up and on the right of the sidebar */
            --translate-x: 0px;
            translate: calc(var(--translate-x) - var(--wa-space-s)) calc(-100vh + var(--checkbox-label-size) + var(--wa-space-s));
            width: 100vw;
            height: 100vh;
            pointer-events: none;
            transition: background var(--transition-duration) ease, translate var(--transition-duration) ease;
            background: transparent;
        }
    }

    /* When sidebar is open, button goes back inside */
    .project-view-wrapper:has(.sidebar-toggle-checkbox:checked)  {
        .sidebar {
            transform: translateX(0);
        }

         .sidebar-toggle {
            transform: translateX(0);
            .icon-collapse {
                display: inline;
            }
            .icon-expand {
                display: none;
            }

            .sidebar-backdrop {
                pointer-events: all;
                background: rgba(0, 0, 0, 0.5);
                --translate-x: var(--sidebar-width);
            }
        }


    }
}
</style>
