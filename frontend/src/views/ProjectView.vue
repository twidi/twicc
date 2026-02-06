<script setup>
import { computed, ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import SessionList from '../components/SessionList.vue'
import FetchErrorPanel from '../components/FetchErrorPanel.vue'
import SettingsPopover from '../components/SettingsPopover.vue'
import ProjectBadge from '../components/ProjectBadge.vue'
import ProjectProcessIndicator from '../components/ProjectProcessIndicator.vue'

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const settingsStore = useSettingsStore()

// Tooltips setting
const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)

// Current project from route params
const projectId = computed(() => route.params.projectId)
const sessionId = computed(() => route.params.sessionId || null)

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() =>
    route.name === 'projects-all' ||
    route.name === 'projects-session' ||
    route.name === 'projects-session-subagent'
)

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

// Reference to SessionList for keyboard navigation
const sessionListRef = ref(null)

// Clear search when project changes
watch(effectiveProjectId, () => {
    searchQuery.value = ''
})

/**
 * Handle keyboard events from the search input.
 * Delegates navigation keys to the SessionList component.
 *
 * @param {KeyboardEvent} event
 */
function handleSearchKeydown(event) {
    // Navigation keys that should be handled by SessionList
    const navigationKeys = ['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageUp', 'PageDown', 'Enter', 'Escape']

    if (navigationKeys.includes(event.key) && sessionListRef.value) {
        const handled = sessionListRef.value.handleKeyNavigation(event)
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

// Handle session selection
function handleSessionSelect(session) {
    if (isAllProjectsMode.value) {
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

// Apply stored sidebar width and body class once on mount
onMounted(() => {
    const splitPanel = document.querySelector('.project-view')
    if (splitPanel && sidebarState.width !== DEFAULT_SIDEBAR_WIDTH) {
        splitPanel.positionInPixels = sidebarState.width
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

// Handle split panel reposition: auto-collapse when dragged to threshold, and persist width.
// Ignored on mobile where the split panel is not used (sidebar is an overlay).
function handleSplitReposition(event) {
    if (isMobile()) return
    if (ignoringReposition) return

    const checkbox = document.getElementById('sidebar-toggle-state')
    if (!checkbox) return

    const newWidth = event.target.positionInPixels

    if (newWidth <= SIDEBAR_COLLAPSE_THRESHOLD) {
        // Auto-collapse: mark as closed, reset width to stored value
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
            snap="125px 220px 300px 400px"
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
                    <wa-tooltip v-if="tooltipsEnabled" for="back-button">Back to projects list</wa-tooltip>
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
                    </wa-dropdown>
                    <wa-tooltip v-if="tooltipsEnabled" for="session-options-button">Session list options</wa-tooltip>

                    <!-- Search/filter input -->
                    <wa-input
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
                    @select="handleSessionSelect"
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
                </wa-button-group>
                <wa-tooltip v-if="tooltipsEnabled" for="new-session-button">Create a new session in this project</wa-tooltip>
                <wa-tooltip v-if="tooltipsEnabled" for="new-session-project-picker">Choose a different project</wa-tooltip>

                <!-- In all projects mode: dropdown to choose project -->
                <wa-dropdown
                    v-if="isAllProjectsMode"
                    id="new-session-dropdown"
                    class="new-session-dropdown"
                    placement="top-end"
                    @wa-select="(e) => handleNewSession(e.detail.item.value)"
                >
                    <wa-button
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
            </div>

            <wa-divider></wa-divider>

            <div class="sidebar-footer">
                <!-- Sidebar Toggle button (label for hidden checkbox, wa-button inside for styling) -->
                <label for="sidebar-toggle-state" class="sidebar-toggle" id="sidebar-toggle-label">
                    <span class="sidebar-backdrop"></span>
                    <wa-button id="sidebar-toggle-button" variant="neutral" appearance="filled-outlined" size="small">
                        <wa-icon class="icon-collapse" name="angles-left"></wa-icon>
                        <wa-icon class="icon-expand" name="angles-right"></wa-icon>
                    </wa-button>
                </label>
                <wa-tooltip v-if="tooltipsEnabled" for="sidebar-toggle-label">Toggle sidebar</wa-tooltip>

                <!-- Placeholder to occupy the same space a the sidebar toggle button that is absolute for goot reasons -->
                <wa-button variant="neutral" appearance="filled-outlined" size="small" style="visibility: hidden; pointer-events: none"><wa-icon name="angles-left"></wa-icon></wa-button>

                <SettingsPopover />
            </div>

        </aside>

        <!-- Main content area -->
        <main slot="end" class="main-content">
            <router-view v-if="sessionId" />
            <div v-else class="empty-state">
                <p>Select a session from the list</p>
            </div>
        </main>
    </wa-split-panel>
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
}

.empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
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
        position: fixed;
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
