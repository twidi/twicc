<script setup>
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import SessionList from '../components/SessionList.vue'
import FetchErrorPanel from '../components/FetchErrorPanel.vue'
import SettingsPopover from '../components/SettingsPopover.vue'
import ProjectBadge from '../components/ProjectBadge.vue'
import ProjectProcessIndicator from '../components/ProjectProcessIndicator.vue'

const route = useRoute()
const router = useRouter()
const store = useDataStore()

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
function handleNewSession() {
    if (isAllProjectsMode.value || !projectId.value) return
    const newSessionId = store.createDraftSession(projectId.value)
    router.push({
        name: 'session',
        params: { projectId: projectId.value, sessionId: newSessionId }
    })
}

const SIDEBAR_WIDTH = 300
// Sidebar collapse threshold in pixels
const SIDEBAR_COLLAPSE_THRESHOLD = 50
// Mobile breakpoint (must match CSS media query)
const MOBILE_BREAKPOINT = 640

// Check if we're on mobile (for initial sidebar state)
const isMobile = () => window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`).matches

// Initial checkbox state: on mobile, checked = open, so check when no session
// On desktop, checked = closed, so never check initially (sidebar always open)
const initialSidebarChecked = computed(() => {
    if (typeof window === 'undefined') return false
    return isMobile() && !sessionId.value
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
})

// Handle split panel reposition: auto-collapse when dragged to threshold
function handleSplitReposition(event) {
    const checkbox = document.getElementById('sidebar-toggle-state')
    if (!checkbox) return

    if (event.target.positionInPixels <= SIDEBAR_COLLAPSE_THRESHOLD) {
        checkbox.checked = true
        requestAnimationFrame(() => {
            event.target.positionInPixels = SIDEBAR_WIDTH;
        });
    }
}
</script>

<template>
    <div class="project-view-wrapper">
        <!-- Hidden checkbox for pure CSS sidebar toggle -->
        <input type="checkbox" id="sidebar-toggle-state" class="sidebar-toggle-checkbox" :checked="initialSidebarChecked"/>

        <wa-split-panel
            class="project-view"
            :position-in-pixels="SIDEBAR_WIDTH"
            primary="start"
            snap="50px 150px 300px 400px"
            @wa-reposition="handleSplitReposition"
        >
            <!-- Divider handle for touch devices -->
            <wa-icon slot="divider" name="grip-lines-vertical" class="divider-handle"></wa-icon>

            <!-- Sidebar -->
        <aside slot="start" class="sidebar">
            <div class="sidebar-header">
                <wa-button id="back-button" class="back-button" variant="brand" appearance="outlined" size="small" @click="handleBackHome">
                    <wa-icon name="arrow-left"></wa-icon>
                </wa-button>
                <wa-tooltip for="back-button">Back to projects list</wa-tooltip>
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
                    :project-id="effectiveProjectId"
                    :session-id="sessionId"
                    :show-project-name="isAllProjectsMode"
                    @select="handleSessionSelect"
                />

                <!-- Floating "New session" button (only in single project mode) -->
                <wa-button
                    v-if="!isAllProjectsMode"
                    id="new-session-button"
                    class="new-session-button"
                    variant="brand"
                    appearance="filled"
                    size="small"
                    @click="handleNewSession"
                >
                    <wa-icon name="plus"></wa-icon>
                    <span>New session</span>
                </wa-button>
                <wa-tooltip for="new-session-button">Create a new session in this project</wa-tooltip>
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
                <wa-tooltip for="sidebar-toggle-label">Toggle sidebar</wa-tooltip>

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

/* Divider handle: hidden by default, shown only on touch devices */
.divider-handle {
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
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-s);
    overflow: visible;
}

.project-selector {
    flex: 1;
    /* we allow options to be larger than the width of the select */
    min-width: min(200px, 100%);
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
    margin: 0;
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

/* Floating "New session" button */
.new-session-button {
    position: absolute;
    bottom: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 5;
    &::part(label) {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }
}

@container sidebar (width <= 150px) {
    .new-session-button {
        &::part(base) {
            padding: var(--wa-space-s);
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
