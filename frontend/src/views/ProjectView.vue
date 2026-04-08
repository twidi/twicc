<script setup>
import { computed, ref, watch, onMounted, onUnmounted, onBeforeUnmount, provide, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { useWorkspacesStore } from '../stores/workspaces'
import { THEME_MODE } from '../constants'
import { useCommandRegistry } from '../composables/useCommandRegistry'
import { useStartupPolling } from '../composables/useStartupPolling'
import { toWorkspaceProjectId } from '../utils/workspaceIds'
import { splitProjectsByPriority } from '../utils/projectSort'
import SessionList from '../components/SessionList.vue'
import FetchErrorPanel from '../components/FetchErrorPanel.vue'
import SettingsPopover from '../components/SettingsPopover.vue'
import ProjectBadge from '../components/ProjectBadge.vue'
import ProjectDetailPanel from '../components/ProjectDetailPanel.vue'
import SessionRenameDialog from '../components/SessionRenameDialog.vue'
import ProjectEditDialog from '../components/ProjectEditDialog.vue'
import WorkspaceManageDialog from '../components/WorkspaceManageDialog.vue'
import { getUsageRingColor, formatRecentDelta } from '../utils/usage'
import { buildProjectTree, flattenProjectTree } from '../utils/projectTree'
import CostDisplay from '../components/CostDisplay.vue'
import AppTooltip from '../components/AppTooltip.vue'
import UsageGraphDialog from '../components/UsageGraphDialog.vue'

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const settingsStore = useSettingsStore()
const { registerCommands, unregisterCommands } = useCommandRegistry()

// Poll home data during startup so sparklines and project stats update
// as sessions are indexed by background compute.
useStartupPolling(() => store.loadHomeData())

// Costs setting
const showCosts = computed(() => settingsStore.areCostsShown)

// In light mode the tooltip background is dark (WA inverts it), so buttons need a filled
// appearance to be readable on that dark background. In dark mode the tooltip is light and
// outlined buttons work fine.
const quotaButtonAppearance = computed(() =>
    settingsStore.getEffectiveTheme === THEME_MODE.DARK ? 'outlined' : 'filled'
)

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
// Usage Graph Dialog
// ════════════════════════════════════���══════════════════════════════════════

const usageGraphDialogRef = ref(null)

function openUsageGraph(period) {
    usageGraphDialogRef.value?.open(period)
}

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

// Pending drop data: set when files/text are dropped on a session list item.
// SessionView watches this and forwards to SessionItemsList once mounted.
const pendingDropData = ref(null)
provide('pendingDropData', pendingDropData)

// Current project from route params
const projectId = computed(() => route.params.projectId)
const sessionId = computed(() => route.params.sessionId || null)

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))

// Workspace state
const workspacesStore = useWorkspacesStore()
const activeWorkspaceId = computed(() => route.query.workspace || null)
const activeWorkspace = computed(() =>
    activeWorkspaceId.value ? workspacesStore.getWorkspaceById(activeWorkspaceId.value) : null
)
const isWorkspaceMode = computed(() => isAllProjectsMode.value && !!activeWorkspaceId.value)
const workspaceVisibleProjectIds = computed(() =>
    activeWorkspaceId.value ? workspacesStore.getVisibleProjectIds(activeWorkspaceId.value) : []
)
const activeWsLabel = computed(() =>
    activeWorkspace.value ? `${activeWorkspace.value.name} projects` : null
)

// Effective project ID for store operations
const effectiveProjectId = computed(() => {
    if (!isAllProjectsMode.value) return projectId.value
    if (activeWorkspaceId.value) return toWorkspaceProjectId(activeWorkspaceId.value)
    return ALL_PROJECTS_ID
})

// All projects for the selector (already sorted by mtime desc in store)
const allProjects = computed(() =>
    store.getProjects.filter(p => showArchivedProjects.value || !p.archived)
)
// Non-stale projects only — used in "new session" dropdowns to prevent creating sessions in stale projects
const nonStaleProjects = computed(() => allProjects.value.filter(p => !p.stale))
const nonStaleNamedProjects = computed(() =>
    nonStaleProjects.value.filter(p => p.name !== null)
)
const nonStaleFlatTree = computed(() => {
    const unnamed = nonStaleProjects.value.filter(p => p.name === null)
    const roots = buildProjectTree(unnamed)
    return flattenProjectTree(roots)
})

// Workspace-first split for "New session" dropdowns.
// When a workspace is active, workspace projects appear first, then others after a divider.
const wsVisibleSet = computed(() =>
    activeWorkspaceId.value ? new Set(workspaceVisibleProjectIds.value) : null
)
const wsPriorityIds = computed(() =>
    activeWorkspace.value ? activeWorkspace.value.projectIds : null
)
const splitNamedProjects = computed(() =>
    splitProjectsByPriority(nonStaleNamedProjects.value, wsPriorityIds.value, wsVisibleSet.value)
)
const splitFlatTree = computed(() => {
    const unnamed = nonStaleProjects.value.filter(p => p.name === null)
    if (!wsPriorityIds.value) {
        return { prioritized: [], others: flattenProjectTree(buildProjectTree(unnamed)) }
    }
    const { prioritized: priProjects, others: otherProjects } = splitProjectsByPriority(unnamed, wsPriorityIds.value, wsVisibleSet.value)
    return {
        prioritized: flattenProjectTree(buildProjectTree(priProjects)),
        others: flattenProjectTree(buildProjectTree(otherProjects)),
    }
})

// Projects not in the active workspace (for the "other" section in the dropdown)
const otherProjectsOutsideWorkspace = computed(() => {
    if (!activeWorkspaceId.value) return []
    const wsSet = new Set(workspaceVisibleProjectIds.value)
    return allProjects.value.filter(p => !wsSet.has(p.id))
})

// Named/unnamed split for the sidebar project selector dropdown (all projects, or "other" projects when workspace active)
const selectorNamedProjects = computed(() =>
    allProjects.value.filter(p => p.name !== null)
)
const selectorFlatTree = computed(() => {
    const unnamed = allProjects.value.filter(p => p.name === null)
    return flattenProjectTree(buildProjectTree(unnamed))
})
const otherNamedProjects = computed(() =>
    otherProjectsOutsideWorkspace.value.filter(p => p.name !== null)
)
const otherFlatTree = computed(() => {
    const unnamed = otherProjectsOutsideWorkspace.value.filter(p => p.name === null)
    if (!unnamed.length) return []
    return flattenProjectTree(buildProjectTree(unnamed))
})

// Whether the current project (single-project mode) is stale
const isCurrentProjectStale = computed(() => {
    if (isAllProjectsMode.value) return false
    const project = store.getProject(projectId.value)
    return project?.stale ?? false
})

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

// Show archived sessions filter (persistent setting, browser-local via settings store)
const showArchivedSessions = computed(() => settingsStore.isShowArchivedSessions)

// Show archived projects filter (persistent setting, browser-local via settings store)
const showArchivedProjects = computed(() => settingsStore.isShowArchivedProjects)

// Compact view (persistent setting, browser-local via settings store)
const compactView = computed(() => settingsStore.isCompactSessionList)

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

function openAdvancedSearch() {
    window.dispatchEvent(new CustomEvent('twicc:open-search'))
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
        settingsStore.setShowArchivedSessions(item.checked)
    } else if (item.value === 'compact-view') {
        settingsStore.setCompactSessionList(item.checked)
    }
}

// Handle project/workspace selection from the dropdown selector
function handleSelectorSelect(event) {
    const value = event.detail?.item?.value
    if (!value) return

    if (value === ALL_PROJECTS_ID) {
        // Clear workspace, go to all projects. Preserve session/sub-route if one is open.
        if (sessionId.value && projectId.value) {
            // Map single-project route names to all-projects equivalents
            let targetName = route.name
            if (!targetName.startsWith('projects-')) {
                targetName = 'projects-' + targetName
            }
            // Use workspace: '' to explicitly signal "no workspace" — the navigation guard
            // won't re-propagate because '' !== undefined. afterEach cleans the URL.
            router.push({ name: targetName, params: route.params, query: { workspace: '' } })
        } else {
            router.push({ name: 'projects-all', query: {} })
        }
    } else if (value.startsWith('workspace:')) {
        const wsId = value.slice('workspace:'.length)
        if (sessionId.value && projectId.value) {
            const ws = workspacesStore.getWorkspaceById(wsId)
            if (ws?.projectIds.includes(projectId.value)) {
                router.push({ name: 'projects-session', params: { projectId: projectId.value, sessionId: sessionId.value }, query: { workspace: wsId } })
            } else {
                router.push({ name: 'projects-all', query: { workspace: wsId } })
            }
        } else {
            router.push({ name: 'projects-all', query: { workspace: wsId } })
        }
    } else if (value === '__manage_workspaces__') {
        manageWorkspacesDialogRef.value?.open()
    } else if (value === 'ws-all') {
        router.push({ name: 'projects-all', query: { workspace: activeWorkspaceId.value } })
    } else {
        // Regular project selection — navigation guard handles workspace propagation
        const targetProjectId = value
        if (sessionId.value && projectId.value === targetProjectId) {
            router.push({ name: 'session', params: { projectId: targetProjectId, sessionId: sessionId.value } })
        } else {
            router.push({ name: 'project', params: { projectId: targetProjectId } })
        }
    }
}

// Handle session selection (toggle: clicking already-selected session deselects it)
function handleSessionSelect(session) {
    if (session.id === sessionId.value) {
        // Deselect: navigate back to project root (keep workspace if active)
        if (isAllProjectsMode.value) {
            router.push({ name: 'projects-all', query: activeWorkspaceId.value ? { workspace: activeWorkspaceId.value } : {} })
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

/**
 * Handle files/text dropped on a session list item via drag-hover.
 * Navigates to the session and stores the drop data for SessionView to process.
 */
function handleDropOnSession({ session, files, text }) {
    // Session is already active (onActivate navigated to it before the drop).
    // Just store the drop data — SessionView will pick it up.
    pendingDropData.value = { sessionId: session.id, files, text }
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

// Create project from the "New session" dropdown
const createProjectDialogRef = ref(null)

// Workspace management dialog
const manageWorkspacesDialogRef = ref(null)

function handleNewSessionSelect(e) {
    const value = e.detail.item.value
    if (value === '__new_project__') {
        createProjectDialogRef.value?.open()
    } else {
        handleNewSession(value)
    }
}

function handleProjectCreated(project) {
    handleNewSession(project.id)
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
// Only reacts when a session transitions from non-archived to archived while
// we are viewing it, not when navigating to a session that was already archived
// (e.g. from search results).
//
// The key challenge: when navigating to a session in a different project, the
// session data isn't in the store yet. The computed returns null (not loaded),
// then when loadSessions() completes, it flips to true. We must not treat this
// as an "archive action" — we use a three-state computed (null/false/true) and
// a flag that tracks whether we've ever seen the session as non-archived.
const currentSessionArchived = computed(() => {
    if (!sessionId.value) return null
    const session = store.sessions[sessionId.value]
    if (!session) return null  // session not loaded yet
    return session.archived ?? false
})

// Tracks whether we've confirmed that the current session was non-archived
// after its data loaded. Only redirect when this is true (meaning the session
// was actively archived while we were viewing it).
let sessionConfirmedNonArchived = false

watch(sessionId, () => {
    // When navigating between two non-archived sessions in the same project,
    // currentSessionArchived stays false (no value change), so its watcher
    // won't fire to re-confirm the flag. We must check immediately here.
    const archived = currentSessionArchived.value
    sessionConfirmedNonArchived = archived === false
})

watch(currentSessionArchived, (archived) => {
    if (archived === null) return  // session not loaded yet, ignore
    if (!archived) {
        // Session is loaded and not archived — record this so we can
        // detect a future false→true transition as an archive action.
        sessionConfirmedNonArchived = true
        return
    }
    // archived is true — only redirect if we previously saw it as non-archived
    if (!sessionConfirmedNonArchived) return
    if (showArchivedSessions.value) return  // session stays visible, no need to navigate

    const nextPath = store.getNextMruPath(sessionId.value)
    if (nextPath) {
        router.push(nextPath)
    }
}, { immediate: true })

// Redirect away from workspace if it becomes non-activable, archived (when hidden), or deleted
watch(
    [activeWorkspaceId, () => workspacesStore.workspaces, () => settingsStore.isShowArchivedWorkspaces, () => settingsStore.isShowArchivedProjects],
    () => {
        if (!activeWorkspaceId.value) return
        const ws = workspacesStore.getWorkspaceById(activeWorkspaceId.value)
        const shouldClear = (
            !ws ||
            !workspacesStore.isActivable(ws.id) ||
            (ws.archived && !settingsStore.isShowArchivedWorkspaces)
        )
        if (shouldClear) {
            router.replace({ name: 'projects-all', query: {} })
        }
    }
)

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

    // Register contextual commands in the command palette
    registerCommands([
        {
            id: 'ui.toggle-sidebar',
            label: 'Toggle Sidebar',
            icon: 'table-columns',
            category: 'ui',
            action: () => {
                const checkbox = document.getElementById('sidebar-toggle-state')
                if (checkbox) {
                    checkbox.checked = !checkbox.checked
                    checkbox.dispatchEvent(new Event('change'))
                }
            },
        },
        {
            id: 'ui.focus-search',
            label: 'Focus Session Filter',
            icon: 'magnifying-glass',
            category: 'ui',
            action: () => {
                searchInputRef.value?.focus()
            },
        },
    ])

    // Listen for custom events to open dialogs (triggered by command palette)
    window.addEventListener('twicc:open-new-project-dialog', openNewProjectDialog)
    window.addEventListener('twicc:open-new-workspace-dialog', openNewWorkspaceDialog)
    window.addEventListener('twicc:open-manage-workspaces-dialog', openManageWorkspacesDialog)
    window.addEventListener('twicc:open-edit-workspace-dialog', openEditWorkspaceDialog)
})

function openNewProjectDialog() {
    createProjectDialogRef.value?.open()
}
function openNewWorkspaceDialog() {
    manageWorkspacesDialogRef.value?.openNew()
}
function openManageWorkspacesDialog() {
    manageWorkspacesDialogRef.value?.open()
}
function openEditWorkspaceDialog(e) {
    manageWorkspacesDialogRef.value?.openForWorkspace(e.detail?.workspaceId)
}

onBeforeUnmount(() => {
    unregisterCommands(['ui.toggle-sidebar', 'ui.focus-search'])
    window.removeEventListener('twicc:open-new-project-dialog', openNewProjectDialog)
    window.removeEventListener('twicc:open-new-workspace-dialog', openNewWorkspaceDialog)
    window.removeEventListener('twicc:open-manage-workspaces-dialog', openManageWorkspacesDialog)
    window.removeEventListener('twicc:open-edit-workspace-dialog', openEditWorkspaceDialog)
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

    // Desktop: checkbox checked = sidebar closed. Ignore reposition events when closed —
    // the sidebar is hidden via CSS (grid-template-columns: 0), so any events are
    // spurious layout recalculations (e.g. initial mount, KeepAlive reactivation).
    if (checkbox.checked) return

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
                    <wa-dropdown
                        id="project-selector"
                        class="project-selector"
                        @wa-select="handleSelectorSelect"
                    >
                        <wa-button
                            slot="trigger"
                            variant="brand"
                            appearance="outlined"
                            size="small"
                            class="project-selector-trigger"
                        >
                            <span v-if="!isAllProjectsMode" class="selected-project-dot" :style="selectedProjectColor ? { '--dot-color': selectedProjectColor } : null"></span>
                            <span v-if="isWorkspaceMode" class="project-selector-label"><wa-icon name="layer-group" auto-width :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"></wa-icon> {{ activeWorkspace?.name }}</span>
                            <span v-else-if="isAllProjectsMode" class="project-selector-label">All Projects</span>
                            <span v-else class="project-selector-label">{{ store.getProjectDisplayName(projectId) }}</span>

                            <wa-icon slot="end" name="chevron-down" class="project-selector-caret"></wa-icon>
                        </wa-button>

                        <!-- When no workspace is active -->
                        <template v-if="!activeWorkspaceId">
                            <wa-dropdown-item :value="ALL_PROJECTS_ID">
                                <wa-icon slot="icon" name="check" :style="{ visibility: isAllProjectsMode ? 'visible' : 'hidden' }"></wa-icon>
                                All Projects
                            </wa-dropdown-item>

                            <template v-if="workspacesStore.getSelectableWorkspaces.length">
                                <wa-divider></wa-divider>
                                <wa-dropdown-item disabled class="section-header-item">
                                    <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                    Workspaces
                                </wa-dropdown-item>
                                <wa-dropdown-item
                                    v-for="ws in workspacesStore.getSelectableWorkspaces"
                                    :key="ws.id"
                                    :value="'workspace:' + ws.id"
                                >
                                    <wa-icon slot="icon" name="layer-group" :style="ws.color ? { color: ws.color } : null"></wa-icon>
                                    {{ ws.name }}
                                </wa-dropdown-item>
                                <wa-dropdown-item value="__manage_workspaces__">
                                    <wa-icon slot="icon" name="gear"></wa-icon>
                                    Manage workspaces...
                                </wa-dropdown-item>
                            </template>

                            <!-- Named projects -->
                            <wa-divider v-if="selectorNamedProjects.length"></wa-divider>
                            <wa-dropdown-item
                                v-for="p in selectorNamedProjects"
                                :key="p.id"
                                :value="p.id"
                            >
                                <wa-icon slot="icon" name="check" :style="{ visibility: !isAllProjectsMode && projectId === p.id ? 'visible' : 'hidden' }"></wa-icon>
                                <ProjectBadge :project-id="p.id" />
                            </wa-dropdown-item>

                            <!-- Unnamed projects (directory tree) -->
                            <wa-divider v-if="selectorFlatTree.length"></wa-divider>
                            <template v-for="item in selectorFlatTree" :key="item.key">
                                <wa-dropdown-item
                                    v-if="item.isFolder"
                                    disabled
                                    class="tree-folder-dropdown-item"
                                >
                                    <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                    <span class="tree-folder-label" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                        {{ item.segment }}
                                    </span>
                                </wa-dropdown-item>
                                <wa-dropdown-item
                                    v-else
                                    :value="item.project.id"
                                >
                                    <wa-icon slot="icon" name="check" :style="{ visibility: !isAllProjectsMode && projectId === item.project.id ? 'visible' : 'hidden' }"></wa-icon>
                                    <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                        <ProjectBadge :project-id="item.project.id" />
                                    </span>
                                </wa-dropdown-item>
                            </template>
                        </template>

                        <!-- When a workspace is active -->
                        <template v-else>
                            <wa-dropdown-item :value="ALL_PROJECTS_ID">
                                <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                All Projects
                            </wa-dropdown-item>

                            <wa-divider></wa-divider>
                            <wa-dropdown-item disabled class="section-header-item">
                                <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                Workspaces
                            </wa-dropdown-item>
                            <wa-dropdown-item
                                v-for="ws in workspacesStore.getSelectableWorkspaces"
                                :key="ws.id"
                                :value="'workspace:' + ws.id"
                            >
                                <wa-icon slot="icon" :name="ws.id === activeWorkspaceId ? 'check' : 'layer-group'" :style="ws.id !== activeWorkspaceId && ws.color ? { color: ws.color } : null"></wa-icon>
                                {{ ws.name }}
                            </wa-dropdown-item>
                            <wa-dropdown-item value="__manage_workspaces__">
                                <wa-icon slot="icon" name="gear"></wa-icon>
                                Manage workspaces...
                            </wa-dropdown-item>

                            <wa-divider></wa-divider>
                            <!-- Workspace projects sub-section -->
                            <wa-dropdown-item disabled class="section-header-item">
                                <wa-icon slot="icon" name="layer-group" :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"></wa-icon>
                                {{ activeWorkspace?.name }} projects
                            </wa-dropdown-item>
                            <wa-dropdown-item value="ws-all">
                                <wa-icon slot="icon" name="check" :style="{ visibility: isAllProjectsMode ? 'visible' : 'hidden' }"></wa-icon>
                                All projects
                            </wa-dropdown-item>
                            <wa-dropdown-item
                                v-for="pid in workspaceVisibleProjectIds"
                                :key="pid"
                                :value="pid"
                            >
                                <wa-icon slot="icon" name="check" :style="{ visibility: !isAllProjectsMode && projectId === pid ? 'visible' : 'hidden' }"></wa-icon>
                                <ProjectBadge :project-id="pid" />
                            </wa-dropdown-item>

                            <!-- Other projects (not in workspace) -->
                            <template v-if="otherNamedProjects.length || otherFlatTree.length">
                                <wa-divider></wa-divider>
                                <wa-dropdown-item disabled class="section-header-item">
                                    <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                    Other projects
                                </wa-dropdown-item>
                            </template>
                            <!-- Other named projects -->
                            <wa-dropdown-item
                                v-for="p in otherNamedProjects"
                                :key="p.id"
                                :value="p.id"
                            >
                                <wa-icon slot="icon" name="check" :style="{ visibility: !isAllProjectsMode && projectId === p.id ? 'visible' : 'hidden' }"></wa-icon>
                                <ProjectBadge :project-id="p.id" />
                            </wa-dropdown-item>
                            <!-- Other unnamed projects (directory tree) -->
                            <wa-divider v-if="otherNamedProjects.length && otherFlatTree.length"></wa-divider>
                            <template v-for="item in otherFlatTree" :key="item.key">
                                <wa-dropdown-item
                                    v-if="item.isFolder"
                                    disabled
                                    class="tree-folder-dropdown-item"
                                >
                                    <wa-icon slot="icon" name="check" style="visibility: hidden;"></wa-icon>
                                    <span class="tree-folder-label" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                        {{ item.segment }}
                                    </span>
                                </wa-dropdown-item>
                                <wa-dropdown-item
                                    v-else
                                    :value="item.project.id"
                                >
                                    <wa-icon slot="icon" name="check" :style="{ visibility: !isAllProjectsMode && projectId === item.project.id ? 'visible' : 'hidden' }"></wa-icon>
                                    <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                        <ProjectBadge :project-id="item.project.id" />
                                    </span>
                                </wa-dropdown-item>
                            </template>
                        </template>
                    </wa-dropdown>
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
                    <wa-button
                        id="search-advanced-button"
                        variant="neutral"
                        appearance="filled-outlined"
                        size="small"
                        class="search-advanced-button"
                        @click="openAdvancedSearch"
                    >
                        <wa-icon name="plus"></wa-icon>
                    </wa-button>
                    <AppTooltip for="search-advanced-button">Full-text search (Ctrl+Shift+F)</AppTooltip>
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
                    :show-project-name="isAllProjectsMode && (!activeWorkspace || workspaceVisibleProjectIds.length > 1)"
                    :search-query="searchQuery"
                    :show-archived="showArchivedSessions"
                    :show-archived-projects="showArchivedProjects"
                    :compact-view="compactView"
                    @select="handleSessionSelect"
                    @drop-data="handleDropOnSession"
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
                        :disabled="isCurrentProjectStale"
                        @click="handleNewSession()"
                    >
                        <wa-icon name="plus"></wa-icon>
                        <span>New session</span>
                    </wa-button>

                    <!-- Dropdown arrow: choose a different project -->
                    <wa-dropdown
                        placement="top-end"
                        @wa-select="handleNewSessionSelect"
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
                        <wa-dropdown-item value="__new_project__">
                            <wa-icon slot="icon" name="plus"></wa-icon>
                            New project
                        </wa-dropdown-item>

                        <!-- Workspace projects first (when workspace active) -->
                        <template v-if="splitNamedProjects.prioritized.length || splitFlatTree.prioritized.length">
                            <wa-divider></wa-divider>
                            <wa-dropdown-item v-if="activeWsLabel" disabled class="section-header-item"><wa-icon name="layer-group" auto-width :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"></wa-icon> {{ activeWsLabel }}</wa-dropdown-item>
                        </template>
                        <wa-dropdown-item
                            v-for="p in splitNamedProjects.prioritized"
                            :key="p.id"
                            :value="p.id"
                        >
                            <ProjectBadge :project-id="p.id" />
                        </wa-dropdown-item>
                        <template v-for="item in splitFlatTree.prioritized" :key="'wsp-' + item.key">
                            <wa-dropdown-item
                                v-if="item.isFolder"
                                disabled
                                class="tree-folder-dropdown-item"
                            >
                                <span class="tree-folder-label" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                    {{ item.segment }}
                                </span>
                            </wa-dropdown-item>
                            <wa-dropdown-item
                                v-else
                                :value="item.project.id"
                            >
                                <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                    <ProjectBadge :project-id="item.project.id" />
                                </span>
                            </wa-dropdown-item>
                        </template>

                        <!-- Other projects -->
                        <template v-if="activeWsLabel && (splitNamedProjects.others.length || splitFlatTree.others.length)">
                            <wa-divider></wa-divider>
                            <wa-dropdown-item disabled class="section-header-item">Other projects</wa-dropdown-item>
                        </template>
                        <wa-divider v-else-if="splitNamedProjects.others.length"></wa-divider>
                        <wa-dropdown-item
                            v-for="p in splitNamedProjects.others"
                            :key="p.id"
                            :value="p.id"
                        >
                            <ProjectBadge :project-id="p.id" />
                        </wa-dropdown-item>

                        <!-- Other unnamed projects (flattened tree) -->
                        <wa-divider v-if="splitFlatTree.others.length"></wa-divider>
                        <template v-for="item in splitFlatTree.others" :key="item.key">
                            <wa-dropdown-item
                                v-if="item.isFolder"
                                disabled
                                class="tree-folder-dropdown-item"
                            >
                                <span class="tree-folder-label" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                    {{ item.segment }}
                                </span>
                            </wa-dropdown-item>
                            <wa-dropdown-item
                                v-else
                                :value="item.project.id"
                            >
                                <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                    <ProjectBadge :project-id="item.project.id" />
                                </span>
                            </wa-dropdown-item>
                        </template>
                    </wa-dropdown>
                </wa-button-group>

                <template v-if="!isAllProjectsMode">
                    <AppTooltip for="new-session-button">Create a new session in this project</AppTooltip>
                    <AppTooltip for="new-session-project-picker">Choose a different project</AppTooltip>
                </template>

                <!-- In all projects mode: dropdown to choose project -->
                <wa-dropdown
                    v-if="isAllProjectsMode"
                    id="new-session-dropdown"
                    class="new-session-dropdown"
                    placement="top-end"
                    @wa-select="handleNewSessionSelect"
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
                    <wa-dropdown-item value="__new_project__">
                        <wa-icon slot="icon" name="plus"></wa-icon>
                        New project
                    </wa-dropdown-item>

                    <!-- Workspace projects first (when workspace active) -->
                    <template v-if="splitNamedProjects.prioritized.length || splitFlatTree.prioritized.length">
                        <wa-divider></wa-divider>
                        <wa-dropdown-item v-if="activeWsLabel" disabled class="section-header-item"><wa-icon name="layer-group" auto-width :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"></wa-icon> {{ activeWsLabel }}</wa-dropdown-item>
                    </template>
                    <wa-dropdown-item
                        v-for="p in splitNamedProjects.prioritized"
                        :key="p.id"
                        :value="p.id"
                    >
                        <ProjectBadge :project-id="p.id" />
                    </wa-dropdown-item>
                    <template v-for="item in splitFlatTree.prioritized" :key="'wsp-' + item.key">
                        <wa-dropdown-item
                            v-if="item.isFolder"
                            disabled
                            class="tree-folder-dropdown-item"
                        >
                            <span class="tree-folder-label" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                {{ item.segment }}
                            </span>
                        </wa-dropdown-item>
                        <wa-dropdown-item
                            v-else
                            :value="item.project.id"
                        >
                            <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                <ProjectBadge :project-id="item.project.id" />
                            </span>
                        </wa-dropdown-item>
                    </template>

                    <!-- Other projects -->
                    <template v-if="activeWsLabel && (splitNamedProjects.others.length || splitFlatTree.others.length)">
                        <wa-divider></wa-divider>
                        <wa-dropdown-item disabled class="section-header-item">Other projects</wa-dropdown-item>
                    </template>
                    <wa-divider v-else-if="splitNamedProjects.others.length"></wa-divider>
                    <wa-dropdown-item
                        v-for="p in splitNamedProjects.others"
                        :key="p.id"
                        :value="p.id"
                    >
                        <ProjectBadge :project-id="p.id" />
                    </wa-dropdown-item>

                    <!-- Other unnamed projects (flattened tree) -->
                    <wa-divider v-if="splitFlatTree.others.length"></wa-divider>
                    <template v-for="item in splitFlatTree.others" :key="item.key">
                        <wa-dropdown-item
                            v-if="item.isFolder"
                            disabled
                            class="tree-folder-dropdown-item"
                        >
                            <span class="tree-folder-label" :style="{ paddingLeft: `${item.depth * 12}px` }">
                                {{ item.segment }}
                            </span>
                        </wa-dropdown-item>
                        <wa-dropdown-item
                            v-else
                            :value="item.project.id"
                        >
                            <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                                <ProjectBadge :project-id="item.project.id" />
                            </span>
                        </wa-dropdown-item>
                    </template>
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
                            <div class="quota-tooltip-row quota-tooltip-row-danger" v-if="quotaFiveHourCost?.cutoffAt"><span class="quota-tooltip-label"><wa-icon name="triangle-exclamation"></wa-icon> Cutoff</span><span>{{ formatResetTime(quotaFiveHourCost.cutoffAt) }}</span></div>
                            <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaFiveHourCost?.cutoffAt"><wa-icon name="triangle-exclamation"></wa-icon> Quota will be exhausted at current pace</div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.recentLong.rate != null && !quotaFiveHour.recentLong.isFallback"><span class="quota-tooltip-label">Burn rate (last {{ formatRecentDelta(quotaFiveHour.recentLong.deltaMs, false, quotaFiveHour.recentLong.lookbackMs) }})</span><span>{{ (quotaFiveHour.recentLong.rate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.recentShort.rate != null && !quotaFiveHour.recentShort.isFallback"><span class="quota-tooltip-label">Burn rate (last {{ formatRecentDelta(quotaFiveHour.recentShort.deltaMs, false, quotaFiveHour.recentShort.lookbackMs) }})</span><span>{{ (quotaFiveHour.recentShort.rate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaFiveHour.resetsAt"><span class="quota-tooltip-label">Reset</span><span>{{ formatResetTime(quotaFiveHour.resetsAt) }}</span></div>
                            <template v-if="showCosts && quotaFiveHourCost && quotaFiveHourCost.spent != null">
                                <wa-divider class="quota-tooltip-divider"></wa-divider>
                                <div class="quota-tooltip-row"><span class="quota-tooltip-label">Spent</span><CostDisplay :cost="quotaFiveHourCost.spent" /></div>
                                <div class="quota-tooltip-row" v-if="quotaFiveHourCost.estimatedPeriod != null"><span class="quota-tooltip-label">Est. 5h</span><CostDisplay :cost="quotaFiveHourCost.estimatedPeriod" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaFiveHourCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Capped — burn rate exceeds 100%</div>
                                <div class="quota-tooltip-row" v-if="quotaFiveHourCost.estimatedMonthly != null"><span class="quota-tooltip-label">Est. 30 days</span><CostDisplay :cost="quotaFiveHourCost.estimatedMonthly" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaFiveHourCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Based on capped 5h estimate</div>
                            </template>
                            <div class="quota-tooltip-buttons">
                                <wa-button size="small" variant="brand" :appearance="quotaButtonAppearance" href="https://claude.ai/settings/usage" target="_blank" rel="noopener">View on claude.ai</wa-button>
                                <wa-button size="small" variant="brand" :appearance="quotaButtonAppearance" @click="openUsageGraph('five-hour')"><wa-icon slot="prefix" name="chart-line"></wa-icon>View graph</wa-button>
                            </div>
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
                            <div class="quota-tooltip-row quota-tooltip-row-danger" v-if="quotaSevenDayCost?.cutoffAt"><span class="quota-tooltip-label"><wa-icon name="triangle-exclamation"></wa-icon> Cutoff</span><span>{{ formatResetTime(quotaSevenDayCost.cutoffAt) }}</span></div>
                            <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaSevenDayCost?.cutoffAt"><wa-icon name="triangle-exclamation"></wa-icon> Quota will be exhausted at current pace</div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.recentLong.rate != null && !quotaSevenDay.recentLong.isFallback"><span class="quota-tooltip-label">Burn rate (last {{ formatRecentDelta(quotaSevenDay.recentLong.deltaMs, true, quotaSevenDay.recentLong.lookbackMs) }})</span><span>{{ (quotaSevenDay.recentLong.rate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.recentShort.rate != null && !quotaSevenDay.recentShort.isFallback"><span class="quota-tooltip-label">Burn rate (last {{ formatRecentDelta(quotaSevenDay.recentShort.deltaMs, true, quotaSevenDay.recentShort.lookbackMs) }})</span><span>{{ (quotaSevenDay.recentShort.rate * 100).toFixed(0) }}%</span></div>
                            <div class="quota-tooltip-row" v-if="quotaSevenDay.resetsAt"><span class="quota-tooltip-label">Reset</span><span>{{ formatResetTime(quotaSevenDay.resetsAt) }}</span></div>
                            <template v-if="showCosts && quotaSevenDayCost && quotaSevenDayCost.spent != null">
                                <wa-divider class="quota-tooltip-divider"></wa-divider>
                                <div class="quota-tooltip-row"><span class="quota-tooltip-label">Spent</span><CostDisplay :cost="quotaSevenDayCost.spent" /></div>
                                <div class="quota-tooltip-row" v-if="quotaSevenDayCost.estimatedPeriod != null"><span class="quota-tooltip-label">Est. 7d</span><CostDisplay :cost="quotaSevenDayCost.estimatedPeriod" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaSevenDayCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Capped — burn rate exceeds 100%</div>
                                <div class="quota-tooltip-row" v-if="quotaSevenDayCost.estimatedMonthly != null"><span class="quota-tooltip-label">Est. 30 days</span><CostDisplay :cost="quotaSevenDayCost.estimatedMonthly" /></div>
                                <div class="quota-tooltip-note quota-tooltip-row-danger" v-if="quotaSevenDayCost.capped"><wa-icon name="triangle-exclamation"></wa-icon> Based on capped 7d estimate</div>
                            </template>
                            <div class="quota-tooltip-buttons">
                                <wa-button size="small" variant="brand" :appearance="quotaButtonAppearance" href="https://claude.ai/settings/usage" target="_blank" rel="noopener">View on claude.ai</wa-button>
                                <wa-button size="small" variant="brand" :appearance="quotaButtonAppearance" @click="openUsageGraph('seven-day')"><wa-icon slot="prefix" name="chart-line"></wa-icon>View graph</wa-button>
                            </div>
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
                            <wa-button size="small" variant="brand" :appearance="quotaButtonAppearance" href="https://claude.ai/settings/usage" target="_blank" rel="noopener" class="quota-stale-button">View on claude.ai</wa-button>
                        </div>
                    </AppTooltip>
                    <wa-icon v-if="quotaIsStale" id="quota-stale-warning" name="triangle-exclamation" class="quota-stale-icon"></wa-icon>
                    <AppTooltip v-if="quotaIsStale" for="quota-stale-warning" hoist force>
                        <div class="quota-tooltip">
                            <div class="quota-stale-header"><wa-icon name="triangle-exclamation" class="quota-stale-header-icon"></wa-icon><span>Data may be outdated</span></div>
                            <div class="quota-tooltip-row"><span class="quota-tooltip-label">Last update</span><span>{{ quotaLastUpdateFormatted }}</span></div>
                            <wa-button size="small" variant="brand" :appearance="quotaButtonAppearance" href="https://claude.ai/settings/usage" target="_blank" rel="noopener" class="quota-stale-button">View usage on claude.ai</wa-button>
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

    <!-- Create project dialog (opened from "New session" dropdown) -->
    <ProjectEditDialog ref="createProjectDialogRef" @saved="handleProjectCreated" />

    <!-- Usage graph dialog -->
    <UsageGraphDialog ref="usageGraphDialogRef" />

    <!-- Workspace management dialog -->
    <WorkspaceManageDialog ref="manageWorkspacesDialogRef" />
    </div>
</template>

<style scoped>
.project-view-wrapper {
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

.search-advanced-button {
    flex-shrink: 0;
}

.session-options-dropdown {
    flex-shrink: 0;
}

.project-selector {
    flex: 1;
    overflow: hidden;
    display: inline-flex;
    max-width: min(50rem, calc(100vw - 100px));
    &::part(menu) {
       max-width: min(50rem, calc(100vw - 2rem)) !important
    }
}
@media (width < 400px) {
    .project-selector {
        &::part(menu) {
            width: min(50rem, calc(100vw - 2rem)) !important
        }
    }
}


.project-selector-trigger {
    width: 100%;
    background: var(--wa-color-surface-default);
    &::part(base) {
        justify-content: space-between;
    }
    &::part(start) {
        display: none;
    }
    &::part(label) {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
        overflow: hidden;
    }
}
.project-selector {
    &:hover, &[open] {
        overflow: visible;
        .project-selector-trigger {
            z-index: 11;
        }
    }
}

@container sidebar (width <= 13rem) {
    .project-selector-trigger {
        &::part(base) {
            padding-inline: var(--wa-space-xs);
        }
        .project-selector-caret {
            margin-inline-start: var(--wa-space-3xs);
        }
    }
}

.project-selector-label {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    & > wa-icon {
        margin-right: var(--wa-space-2xs);
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

.tree-folder-dropdown-item {
    opacity: 1;
    cursor: default;
}

.section-header-item {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    opacity: 1;
    cursor: default;
    color: var(--wa-color-text-quiet);
    wa-icon {
        font-size: var(--wa-font-size-s);
        color: var(--wa-color-text-normal);
        margin-inline: 0.2em;
    }
}

.tree-folder-label {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
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

    /* Limit dropdown menu height: set the variable directly on #menu (via ::part)
       so it overrides the value inherited from wa-popup's inline style */
    & wa-dropdown::part(menu) {
        --auto-size-available-height: 50dvh;
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

    /* Limit dropdown menu height: set the variable directly on #menu (via ::part)
       so it overrides the value inherited from wa-popup's inline style */
    &::part(menu) {
        --auto-size-available-height: 50dvh;
    }

    /* Style the trigger button label */
    & > wa-button::part(label) {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
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

.quota-tooltip-buttons {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.quota-tooltip-buttons wa-button {
    width: 100%;
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
            translate: calc(var(--translate-x) - var(--wa-space-s)) calc(-100dvh + var(--checkbox-label-size) + var(--wa-space-s));
            width: 100vw;
            height: 100dvh;
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
