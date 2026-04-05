<script setup>
import { computed, watch, ref, readonly, provide, inject, onActivated, onDeactivated, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { useCommandRegistry } from '../composables/useCommandRegistry'
import { killProcess, requestTitleSuggestion, notifySessionViewed, forceNotifySessionViewed } from '../composables/useWebSocket'
import { useDragHover } from '../composables/useDragHover'
import { PROCESS_STATE } from '../constants'
import SessionHeader from '../components/SessionHeader.vue'
import SessionItemsList from '../components/SessionItemsList.vue'
import SessionContent from '../components/SessionContent.vue'
import FilesPanel from '../components/FilesPanel.vue'
import GitPanel from '../components/GitPanel.vue'
import TerminalPanel from '../components/TerminalPanel.vue'
import AppTooltip from '../components/AppTooltip.vue'
import ProcessIndicator from '../components/ProcessIndicator.vue'
import { useCodeCommentsStore } from '../stores/codeComments'

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const settingsStore = useSettingsStore()
const codeCommentsStore = useCodeCommentsStore()
const { registerCommands, unregisterCommands } = useCommandRegistry()

// Reference to session header for opening rename dialog
const sessionHeaderRef = ref(null)

// Reference to session items list for scroll compensation
const sessionItemsListRef = ref(null)

// Reference to FilesPanel for cross-tab file reveal
const filesPanelRef = ref(null)

// ═══════════════════════════════════════════════════════════════════════════
// KeepAlive lifecycle: active state, listener setup/teardown
// ═══════════════════════════════════════════════════════════════════════════

const isActive = ref(true)

/**
 * Restore the last active tab when returning to this session via KeepAlive.
 * The route always lands on /session/:id (= 'main'), but the user may have
 * been on a different tab. We use router.replace to silently correct the URL.
 */
function restoreActiveTab() {
    const saved = store.getSessionOpenTabs(sessionId.value)
    const savedTab = saved?.activeTab
    if (!savedTab || savedTab === 'main' || savedTab === activeTabId.value) return

    const params = { projectId: projectId.value, sessionId: sessionId.value }

    if (savedTab.startsWith('agent-')) {
        const agentId = savedTab.replace('agent-', '')
        router.replace({
            name: isAllProjectsMode.value ? 'projects-session-subagent' : 'session-subagent',
            params: { ...params, subagentId: agentId }
        })
    } else if (['files', 'git', 'terminal'].includes(savedTab)) {
        // Don't restore git tab if session has no git repo
        if (savedTab === 'git' && !hasGitRepo.value) return
        router.replace({
            name: isAllProjectsMode.value ? `projects-session-${savedTab}` : `session-${savedTab}`,
            params
        })
    }
}

onMounted(() => {
    // Mark session as viewed on first render
    notifySessionViewed(sessionId.value)
    // Listen for tab keyboard shortcuts (dispatched by App.vue)
    window.addEventListener('twicc:tab-shortcut', handleTabShortcut)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:tab-shortcut', handleTabShortcut)
})

onActivated(() => {
    isActive.value = true

    // Start observing compact tab overflow
    startCompactTabsObserver()

    // Register contextual session commands in the command palette
    registerSessionCommands()

    // Restore last active tab from store (KeepAlive preserves component state,
    // but the route is global — navigating to a session always lands on /session/:id
    // which maps to 'main'. If the user was on a different tab, restore it.)
    restoreActiveTab()

    // Mark session as viewed when re-activated (KeepAlive navigation back)
    notifySessionViewed(sessionId.value)
})

onDeactivated(() => {
    isActive.value = false

    // Force-send session_viewed to ensure last_viewed_at is fresh before leaving.
    // Without this, the throttle can cause last_viewed_at to be stale (set at navigation time)
    // while last_new_content_at was updated during viewing — making the session appear unread.
    forceNotifySessionViewed(sessionId.value)

    // Stop observing compact tab overflow
    stopCompactTabsObserver()

    // Unregister contextual session commands from the command palette
    unregisterCommands(SESSION_COMMAND_IDS)

    // Cancel any pending drag-hover timer
    chatTabDragHover.cancel()
})

provide('sessionActive', readonly(isActive))

// ─── Cross-tab root directory sync (Files ↔ Git) ─────────────────────────────

/**
 * Shared git directory path for synchronizing root selection between
 * the Files tab and the Git tab.
 * Updated when the user manually selects a git root in either panel.
 * Each panel watches this via prop and selects the matching root,
 * without re-emitting (preventing infinite loops).
 */
const syncedGitDirPath = ref(null)

function onRootChanged(path) {
    syncedGitDirPath.value = path
}

// ─── Cross-tab file reveal (Git → Files) ─────────────────────────────────────

/**
 * Switch to the Files tab and reveal a specific file.
 * Provided to descendant components (e.g., FilePane in the Git panel).
 *
 * Before revealing, ensures the Files tab root matches the Git tab's
 * current git directory (handles the case where Files tab was on a
 * non-git root like "Project directory").
 *
 * @param {string} absolutePath — the absolute filesystem path to reveal
 */
async function viewFileInFilesTab(absolutePath, { lineNum = null } = {}) {
    // Ensure the Files tab root can reach the file.
    // Determine the root directory that contains this file path and switch to it.
    const gitDir = session.value?.git_directory
    const sessionCwd = session.value?.cwd
    const projectGitRoot = store.getProject(session.value?.project_id)?.git_root
    const projectDir = store.getProject(session.value?.project_id)?.directory
    const matchingRoot = [gitDir, sessionCwd, projectDir, projectGitRoot].find(
        root => root && absolutePath.startsWith(root + '/')
    )
    if (matchingRoot) {
        filesPanelRef.value?.setRootByPath(matchingRoot)
    }
    switchToTab('files')
    // Wait for the tab panel to become active and the FilesPanel to be ready
    await nextTick()
    await filesPanelRef.value?.revealFile(absolutePath, { lineNum })
}

provide('viewFileInFilesTab', viewFileInFilesTab)

function insertTextAtCursor(text) {
    sessionItemsListRef.value?.insertTextAtCursor(text)
}
provide('insertTextAtCursor', insertTextAtCursor)

// Current session from route params
// IMPORTANT: projectId and sessionId are captured at creation time (not reactive
// computeds from route.params) because with KeepAlive, the route changes globally
// when switching sessions. If these were reactive, ALL cached SessionView instances
// would see the NEW session's params, breaking deactivation hooks and item lookups.
// The KeepAlive key (route.params.sessionId) ensures each instance gets the correct
// value at creation time and keeps it permanently.
const projectId = ref(route.params.projectId)
const sessionId = ref(route.params.sessionId)
const subagentId = computed(() => route.params.subagentId)

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))

// Session data
const session = computed(() => store.getSession(sessionId.value))

// Whether the session is in a git repository:
// - session has resolved git info (git_directory + git_branch from tool_use), OR
// - the project itself is inside a git repo (git_root resolved from project directory)
const hasGitRepo = computed(() =>
    (!!session.value?.git_directory && !!session.value?.git_branch)
    || !!store.getProject(session.value?.project_id)?.git_root
)

// Code comments indicators per tab
const hasFilesComments = computed(() =>
    codeCommentsStore.countBySource(projectId.value, sessionId.value, 'files') > 0
)
const hasGitComments = computed(() =>
    codeCommentsStore.countBySource(projectId.value, sessionId.value, 'git') > 0
)
const hasChatComments = computed(() =>
    codeCommentsStore.getCommentsBySession(projectId.value, sessionId.value)
        .some(c => c.source === 'tool' && !c.subagentSessionId)
)
function hasAgentComments(agentSessionId) {
    return codeCommentsStore.getCommentsBySession(projectId.value, sessionId.value)
        .some(c => c.subagentSessionId === agentSessionId)
}

const activeTabHasComments = computed(() => {
    const tabId = activeTabId.value
    if (tabId === 'main') return hasChatComments.value
    if (tabId === 'files') return hasFilesComments.value
    if (tabId === 'git') return hasGitComments.value
    if (tabId.startsWith('agent-')) return hasAgentComments(tabId.replace('agent-', ''))
    return false
})

// Tabs state - computed from store (automatically updates when session changes)
// Format: [{ id: 'agent-xxx', agentId: 'xxx' }, ...]
const openSubagentTabs = computed(() => {
    const saved = store.getSessionOpenTabs(sessionId.value)
    if (!saved) return []

    return saved.tabs
        .filter(id => id !== 'main' && id.startsWith('agent-'))
        .map(id => ({
            id,
            agentId: id.replace('agent-', '')
        }))
})

// Active tab ID ('main' for session, 'agent-xxx' for subagents, 'files'/'git'/'terminal' for tool tabs)
// Computed from route
const activeTabId = computed(() => {
    if (subagentId.value) {
        return `agent-${subagentId.value}`
    }
    const name = route.name
    if (name === 'session-files' || name === 'projects-session-files') return 'files'
    if (name === 'session-git' || name === 'projects-session-git') return 'git'
    if (name === 'session-terminal' || name === 'projects-session-terminal') return 'terminal'
    return 'main'
})

// Human-readable label for the active tab (used in compact header)
const activeTabLabel = computed(() => {
    const tabId = activeTabId.value
    if (tabId === 'main') return 'Chat'
    if (tabId === 'files') return 'Files'
    if (tabId === 'git') return 'Git'
    if (tabId === 'terminal') return 'Terminal'
    if (tabId.startsWith('agent-')) {
        return 'Agent';
    }
    return null
})

// Process state for the active subagent tab (used in compact header label)
const activeTabProcessState = computed(() => {
    const tabId = activeTabId.value
    if (!tabId.startsWith('agent-')) return null
    const agentId = tabId.replace('agent-', '')
    return store.getProcessState(agentId) || null
})

// Redirect away from git tab if the session has no git repo
// (handles direct URL navigation and dynamic changes)
// Guards:
// - skip when deactivated (KeepAlive)
// - skip when route belongs to another session
// - skip when project data hasn't loaded yet (avoid premature redirect on
//   direct URL navigation — hasGitRepo depends on project.git_root which is
//   only available after loadProjects() completes)
watch([activeTabId, hasGitRepo], ([tabId, hasGit]) => {
    if (tabId === 'git' && !hasGit) {
        if (!isActive.value) return
        if (route.params.sessionId !== sessionId.value) return
        if (!store.getProject(session.value?.project_id)) return
        router.replace({
            name: isAllProjectsMode.value ? 'projects-session' : 'session',
            params: { projectId: projectId.value, sessionId: sessionId.value }
        })
    }
}, { immediate: true })

/**
 * Navigate to a specific tab by panel name.
 * Used both by the wa-tab-group event handler and compact-mode tab buttons.
 * @param {string} panel - The panel name (e.g., 'main', 'agent-xxx', 'files', 'git', 'terminal')
 */
function switchToTab(panel) {
    // Ignore if already on this tab (avoid infinite loop)
    if (panel === activeTabId.value) return

    if (panel === 'main') {
        // Navigate to session without subagent
        router.push({
            name: isAllProjectsMode.value ? 'projects-session' : 'session',
            params: {
                projectId: projectId.value,
                sessionId: sessionId.value
            }
        })
    } else if (panel.startsWith('agent-')) {
        // Navigate to subagent
        const agentId = panel.replace('agent-', '')
        router.push({
            name: isAllProjectsMode.value ? 'projects-session-subagent' : 'session-subagent',
            params: {
                projectId: projectId.value,
                sessionId: sessionId.value,
                subagentId: agentId
            }
        })
    } else if (['files', 'git', 'terminal'].includes(panel)) {
        // Navigate to tool tab
        router.push({
            name: isAllProjectsMode.value ? `projects-session-${panel}` : `session-${panel}`,
            params: {
                projectId: projectId.value,
                sessionId: sessionId.value
            }
        })
    }
}

/**
 * Navigate to a tab and collapse the compact header overlay.
 * Used by the compact-mode tab buttons inside the header slot.
 * @param {string} panel
 */
function switchToTabAndCollapse(panel) {
    switchToTab(panel)
    if (sessionHeaderRef.value?.isCompactExpanded) {
        sessionHeaderRef.value.isCompactExpanded = false
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard shortcuts: tab navigation (Alt+Shift+1-4, ←/→, ↑)
// Events dispatched by App.vue, handled here by the active instance only.
// ═══════════════════════════════════════════════════════════════════════════

// Ordered list of all visible tabs (for sequential ←/→ navigation).
// Matches the visual order in the wa-tab-group: main, subagents, files, [git], terminal.
const orderedTabs = computed(() => {
    const tabs = ['main']
    for (const tab of openSubagentTabs.value) {
        tabs.push(tab.id)
    }
    tabs.push('files')
    if (hasGitRepo.value) tabs.push('git')
    tabs.push('terminal')
    return tabs
})

// Tab visit history for Alt+Shift+↑ (last-visited, Alt+Tab-like behavior).
// Plain array (not reactive) — no template depends on it.
// Persists as long as the component is KeepAlive'd.
const tabHistory = []
const MAX_TAB_HISTORY = 50

function pushTabHistory(tabId) {
    if (tabHistory.length > 0 && tabHistory[tabHistory.length - 1] === tabId) return
    tabHistory.push(tabId)
    if (tabHistory.length > MAX_TAB_HISTORY) tabHistory.shift()
}

// Track tab transitions for history (separate from the store sync watcher).
// oldTabId is undefined on the first call, so we guard with `if (oldTabId)`.
watch(activeTabId, (newTabId, oldTabId) => {
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    if (oldTabId) pushTabHistory(oldTabId)
})

// Direct tab mapping: Alt+Shift+{1,2,3,4} → fixed tabs (subagents are skipped)
const DIRECT_TAB_MAP = { 1: 'main', 2: 'files', 3: 'git', 4: 'terminal' }

/**
 * Handle keyboard tab shortcut events dispatched from App.vue.
 * Only the active SessionView instance processes the event (KeepAlive guard).
 */
function handleTabShortcut(event) {
    if (!isActive.value) return

    const { type, index } = event.detail

    if (type === 'direct') {
        const targetTab = DIRECT_TAB_MAP[index]
        if (!targetTab) return
        if (targetTab === 'git' && !hasGitRepo.value) return
        switchToTab(targetTab)
    } else if (type === 'prev' || type === 'next') {
        const tabs = orderedTabs.value
        const currentIndex = tabs.indexOf(activeTabId.value)
        if (currentIndex === -1) return
        const newIndex = type === 'next'
            ? (currentIndex + 1) % tabs.length
            : (currentIndex - 1 + tabs.length) % tabs.length
        switchToTab(tabs[newIndex])
    } else if (type === 'last-visited') {
        const tabs = orderedTabs.value
        // Walk history backwards to find the most recent tab that still exists
        // and isn't the currently active one
        for (let i = tabHistory.length - 1; i >= 0; i--) {
            const tabId = tabHistory[i]
            if (tabId !== activeTabId.value && tabs.includes(tabId)) {
                switchToTab(tabId)
                return
            }
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Drag-hover: spring-loaded tab switching (hover 1s while dragging to switch)
// ═══════════════════════════════════════════════════════════════════════════

// Drag-hover on the Chat tab: switches to it when dragging files/text over it for 1 second.
// If files/text are dropped directly on the tab, forward to SessionItemsList for processing.
const chatTabDragHover = useDragHover({
    onActivate: () => switchToTab('main'),
    shouldActivate: () => activeTabId.value !== 'main',
    onDropData: (data) => {
        // Ensure we're on the Chat tab before forwarding
        if (activeTabId.value !== 'main') {
            switchToTab('main')
        }
        nextTick(() => {
            sessionItemsListRef.value?.handleForwardedDrop(data)
        })
    },
})

// Pick up pending drop data from ProjectView (when files/text were dropped on a session list item).
const pendingDropData = inject('pendingDropData', ref(null))
watch(pendingDropData, (data) => {
    if (!data || data.sessionId !== sessionId.value) return
    // Consume the pending data
    pendingDropData.value = null
    // Ensure we're on the Chat tab
    if (activeTabId.value !== 'main') {
        switchToTab('main')
    }
    nextTick(() => {
        sessionItemsListRef.value?.handleForwardedDrop(data)
    })
})

/**
 * Handle tab change event from wa-tab-group.
 * Updates the URL to reflect the new active tab.
 */
function onTabShow(event) {
    const panel = event.detail?.name
    if (!panel) return
    switchToTab(panel)
}

// ═══════════════════════════════════════════════════════════════════════════
// Compact tab nav: scroll overflow controls
// (mirrors wa-tab-group's native scroll behavior)
// ═══════════════════════════════════════════════════════════════════════════

const compactTabScrollArea = ref(null)
const compactTabsCanScrollStart = ref(false)
const compactTabsCanScrollEnd = ref(false)
const compactTabsHasOverflow = ref(false)
let compactTabsResizeObserver = null

/**
 * Update scroll control visibility based on overflow and current scroll position.
 * - hasOverflow: whether the tab area overflows at all (controls DOM presence)
 * - canScrollStart: whether there is hidden content to the left (controls opacity)
 * - canScrollEnd: whether there is hidden content to the right (controls opacity)
 */
function updateCompactTabsScrollControls() {
    const el = compactTabScrollArea.value
    if (!el) {
        compactTabsHasOverflow.value = false
        compactTabsCanScrollStart.value = false
        compactTabsCanScrollEnd.value = false
        return
    }
    const tolerance = 1 // Same safety margin as wa-tab-group
    compactTabsHasOverflow.value = el.scrollWidth > el.clientWidth + tolerance
    compactTabsCanScrollStart.value = el.scrollLeft > tolerance
    compactTabsCanScrollEnd.value = el.scrollLeft + el.clientWidth < el.scrollWidth - tolerance
}

/**
 * Scroll the compact tabs by one viewport width in the given direction.
 * @param {'start' | 'end'} direction
 */
function scrollCompactTabs(direction) {
    const el = compactTabScrollArea.value
    if (!el) return
    const delta = direction === 'start' ? -el.clientWidth : el.clientWidth
    el.scroll({ left: el.scrollLeft + delta, behavior: 'smooth' })
}

/**
 * Handle native scroll events on the compact tab area to update arrow visibility.
 */
function onCompactTabsScroll() {
    updateCompactTabsScrollControls()
}

// Start/stop the ResizeObserver + scroll listener with KeepAlive lifecycle
function startCompactTabsObserver() {
    nextTick(() => {
        const el = compactTabScrollArea.value
        if (!el) return
        updateCompactTabsScrollControls()
        el.addEventListener('scroll', onCompactTabsScroll, { passive: true })
        compactTabsResizeObserver = new ResizeObserver(() => updateCompactTabsScrollControls())
        compactTabsResizeObserver.observe(el)
    })
}

function stopCompactTabsObserver() {
    compactTabScrollArea.value?.removeEventListener('scroll', onCompactTabsScroll)
    if (compactTabsResizeObserver) {
        compactTabsResizeObserver.disconnect()
        compactTabsResizeObserver = null
    }
}

// Recalculate scroll controls when the number of tabs changes
watch(openSubagentTabs, () => {
    nextTick(() => updateCompactTabsScrollControls())
})

/**
 * Close a subagent tab.
 * @param {string} tabId - The tab ID to close (e.g., 'agent-xxx')
 */
function closeTab(tabId) {
    const tabs = openSubagentTabs.value
    const index = tabs.findIndex(t => t.id === tabId)
    if (index === -1) return

    // Remove the tab from store
    store.removeSessionTab(sessionId.value, tabId)

    // If this was the active tab, navigate to the tab on the left
    if (activeTabId.value === tabId) {
        if (index > 0) {
            // Go to the previous subagent tab (use current tabs, not yet updated)
            const prevTab = tabs[index - 1]
            router.push({
                name: isAllProjectsMode.value ? 'projects-session-subagent' : 'session-subagent',
                params: {
                    projectId: projectId.value,
                    sessionId: sessionId.value,
                    subagentId: prevTab.agentId
                }
            })
        } else {
            // No more subagent tabs, go to main
            router.push({
                name: isAllProjectsMode.value ? 'projects-session' : 'session',
                params: {
                    projectId: projectId.value,
                    sessionId: sessionId.value
                }
            })
        }
    }
}

/**
 * Open a subagent tab if not already open.
 * @param {string} agentId - The agent ID
 */
function openSubagentTab(agentId) {
    store.addSessionTab(sessionId.value, `agent-${agentId}`)
}

/**
 * Get short display ID for a subagent.
 */
function getAgentShortId(agentId) {
    return agentId.substring(0, 8)
}

// Watch subagentId to open tab when navigating to a subagent URL.
// Two guards prevent incorrect tab additions with KeepAlive (same logic as activeTabId watcher):
// 1. isActive: skip when deactivated — don't react to route changes while cached
// 2. sessionId check: skip when the route belongs to a different session
watch(subagentId, (newSubagentId) => {
    if (!newSubagentId) return
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    openSubagentTab(newSubagentId)
}, { immediate: true })

// Sync active tab in store when the route changes for THIS session.
// Two guards prevent incorrect overwrites with KeepAlive:
// 1. isActive: when reactivating, the watcher fires before onActivated sets isActive=true,
//    so we skip the sync and let restoreActiveTab() handle it properly.
// 2. sessionId check: when navigating to a DIFFERENT session, the route changes but this
//    cached instance is still alive — we must not sync another session's route as ours.
watch(activeTabId, (newTabId) => {
    if (!sessionId.value) return
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    store.setSessionActiveTab(sessionId.value, newTabId)
}, { immediate: true })

/**
 * Handle a session that needs a title after sending its first message.
 * If title auto-apply is enabled, requests a suggestion and applies it
 * automatically when it arrives (same flow as the rename dialog's Save).
 * Otherwise, opens the rename dialog.
 */
function handleNeedsTitle() {
    if (settingsStore.isTitleAutoApply && settingsStore.isTitleGenerationEnabled) {
        const sid = sessionId.value
        const pid = projectId.value
        const prompt = store.getDraftMessage(sid)?.message?.trim()
        if (!prompt) return

        requestTitleSuggestion(sid, prompt, settingsStore.getTitleSystemPrompt)

        // Track the suggested title once received. This variable is captured by the
        // watcher closure and survives across reactive flushes (broadcasts, etc.).
        let pendingTitle = null

        const unwatch = watch(
            () => ({
                suggestion: store.getTitleSuggestion(sid),
                suggestionEntry: store.getTitleSuggestionEntry(sid),
                session: store.getSession(sid),
            }),
            ({ suggestion, suggestionEntry, session }) => {
                if (!session) return

                // Generation definitively failed (response received but no suggestion
                // after all backend retries) — stop watching, leave session untitled.
                if (suggestionEntry && !suggestion) {
                    unwatch()
                    return
                }

                // Capture the title from the first valid suggestion
                if (suggestion && !pendingTitle) {
                    pendingTitle = suggestion
                }

                if (!pendingTitle) return

                // Re-apply the suggested title only if the session has no title
                // (broadcast replaced the session object without the title yet).
                // If the session has a different non-empty title (user renamed
                // manually, or watcher detected a custom-title), respect it.
                if (!session.title) {
                    session.title = pendingTitle
                }

                // Once the session is real (exists in DB), persist via API and
                // stop watching. renameSession does optimistic update + PATCH.
                if (!session.draft) {
                    unwatch()
                    store.renameSession(pid, sid, pendingTitle)
                }
            }
        )
    } else {
        sessionHeaderRef.value?.openRenameDialog({ showHint: true })
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Command palette: contextual session commands
// ═══════════════════════════════════════════════════════════════════════════

const SESSION_COMMAND_IDS = [
    'session.rename',
    'session.archive',
    'session.unarchive',
    'session.pin',
    'session.unpin',
    'session.stop',
    'session.delete-draft',
    'session.focus-input',
]

function registerSessionCommands() {
    registerCommands([
        {
            id: 'session.rename',
            label: 'Rename Session',
            icon: 'pencil',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !s.draft
            },
            action: () => sessionHeaderRef.value?.openRenameDialog(),
        },
        {
            id: 'session.archive',
            label: 'Archive Session',
            icon: 'box-archive',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !s.draft && !s.archived
            },
            action: async () => {
                const ps = store.getProcessState(sessionId.value)
                if (ps && ps.state !== PROCESS_STATE.DEAD && !ps.synthetic) {
                    killProcess(sessionId.value)
                }
                await store.setSessionArchived(projectId.value, sessionId.value, true)
            },
        },
        {
            id: 'session.unarchive',
            label: 'Unarchive Session',
            icon: 'box-open',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !!s.archived
            },
            action: () => store.setSessionArchived(projectId.value, sessionId.value, false),
        },
        {
            id: 'session.pin',
            label: 'Pin Session',
            icon: 'thumbtack',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !s.pinned
            },
            action: () => store.setSessionPinned(projectId.value, sessionId.value, true),
        },
        {
            id: 'session.unpin',
            label: 'Unpin Session',
            icon: 'thumbtack',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !!s.pinned
            },
            action: () => store.setSessionPinned(projectId.value, sessionId.value, false),
        },
        {
            id: 'session.stop',
            label: 'Stop Process',
            icon: 'stop',
            category: 'session',
            when: () => {
                const ps = store.getProcessState(sessionId.value)
                return !!ps && ps.state !== PROCESS_STATE.DEAD && !ps.synthetic
            },
            action: () => killProcess(sessionId.value),
        },
        {
            id: 'session.delete-draft',
            label: 'Delete Draft',
            icon: 'trash',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !!s.draft
            },
            action: () => {
                store.deleteDraftSession(sessionId.value)
                if (isAllProjectsMode.value) {
                    router.push({ name: 'projects-all' })
                } else {
                    router.push({ name: 'project', params: { projectId: projectId.value } })
                }
            },
        },
        {
            id: 'session.focus-input',
            label: 'Focus Message Input',
            icon: 'keyboard',
            category: 'session',
            action: () => {
                const textarea = document.querySelector('.session-view .message-input wa-textarea')
                if (textarea) {
                    textarea.focus()
                }
            },
        },
    ])
}

onBeforeUnmount(() => {
    unregisterCommands(SESSION_COMMAND_IDS)
    chatTabDragHover.cancel()
})
</script>

<template>
    <div class="session-view">
        <!-- Main session header (always visible, above tabs) -->
        <SessionHeader
            v-if="session"
            ref="sessionHeaderRef"
            :session-id="sessionId"
            mode="session"
            :active-tab-label="activeTabLabel"
            :active-tab-has-comments="activeTabHasComments"
            :active-tab-process-state="activeTabProcessState"
        >
            <!-- Compact mode: tab navigation inside the header overlay -->
            <template #compact-extra>
                <div class="compact-tab-nav" :class="{ 'has-scroll-controls': compactTabsHasOverflow }">
                    <!-- Scroll left button (faded when at the start) -->
                    <wa-button
                        v-if="compactTabsHasOverflow"
                        class="compact-tab-scroll compact-tab-scroll-start"
                        :class="{ 'scroll-disabled': !compactTabsCanScrollStart }"
                        appearance="plain"
                        size="small"
                        :disabled="!compactTabsCanScrollStart"
                        @click="scrollCompactTabs('start')"
                    >
                        <wa-icon name="chevron-left" variant="solid" label="Scroll left"></wa-icon>
                    </wa-button>

                    <!-- Scrollable tabs container -->
                    <div class="compact-tab-scroll-area" ref="compactTabScrollArea">
                        <wa-button
                            :appearance="activeTabId === 'main' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'main' ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse('main')"
                            @dragenter="chatTabDragHover.onDragenter"
                            @dragleave="chatTabDragHover.onDragleave"
                            @dragover="chatTabDragHover.onDragover"
                            @drop="chatTabDragHover.onDrop"
                            :class="{ 'drag-hover-pending': chatTabDragHover.isPending.value }"
                        >
                            Chat
                            <wa-icon v-if="hasChatComments" slot="end" name="comment" variant="regular" class="tab-comments-indicator"></wa-icon>
                            <wa-icon
                                v-if="store.getPendingRequest(sessionId)"
                                slot="end"
                                name="hand"
                                class="pending-request-indicator"
                            ></wa-icon>
                        </wa-button>

                        <wa-button
                            v-for="tab in openSubagentTabs"
                            :key="tab.id"
                            :appearance="activeTabId === tab.id ? 'outlined' : 'plain'"
                            :variant="activeTabId === tab.id ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse(tab.id)"
                        >
                            <span class="subagent-tab-content">
                                <span>Agent "{{ getAgentShortId(tab.agentId) }}"</span>
                                <ProcessIndicator
                                    v-if="store.getProcessState(tab.agentId)"
                                    :state="store.getProcessState(tab.agentId).state"
                                    size="small"
                                />
                                <wa-icon v-if="hasAgentComments(tab.agentId)" name="comment" variant="regular" class="tab-comments-indicator"></wa-icon>
                                <span class="tab-close-icon" @click.stop="closeTab(tab.id)">
                                    <wa-icon name="xmark" label="Close tab"></wa-icon>
                                </span>
                            </span>
                        </wa-button>

                        <wa-button
                            :appearance="activeTabId === 'files' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'files' ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse('files')"
                        >
                            Files
                            <wa-icon v-if="hasFilesComments" slot="end" name="comment" variant="regular" class="tab-comments-indicator"></wa-icon>
                        </wa-button>

                        <wa-button
                            v-if="hasGitRepo"
                            :appearance="activeTabId === 'git' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'git' ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse('git')"
                        >
                            Git
                            <wa-icon v-if="hasGitComments" slot="end" name="comment" variant="regular" class="tab-comments-indicator"></wa-icon>
                        </wa-button>

                        <wa-button
                            :appearance="activeTabId === 'terminal' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'terminal' ? 'brand' : 'neutral'"
                            size="small"
                            @click="switchToTabAndCollapse('terminal')"
                        >Terminal</wa-button>
                    </div>

                    <!-- Scroll right button (faded when at the end) -->
                    <wa-button
                        v-if="compactTabsHasOverflow"
                        class="compact-tab-scroll compact-tab-scroll-end"
                        :class="{ 'scroll-disabled': !compactTabsCanScrollEnd }"
                        appearance="plain"
                        size="small"
                        :disabled="!compactTabsCanScrollEnd"
                        @click="scrollCompactTabs('end')"
                    >
                        <wa-icon name="chevron-right" variant="solid" label="Scroll right"></wa-icon>
                    </wa-button>
                </div>
            </template>
        </SessionHeader>

        <wa-tab-group
            v-if="session"
            :active="activeTabId"
            @wa-tab-show="onTabShow"
            class="session-tabs"
        >
            <!-- Tab navigation -->
            <wa-tab slot="nav" panel="main"
                @dragenter="chatTabDragHover.onDragenter"
                @dragleave="chatTabDragHover.onDragleave"
                @dragover="chatTabDragHover.onDragover"
                @drop="chatTabDragHover.onDrop"
                :class="{ 'drag-hover-pending': chatTabDragHover.isPending.value }"
            >
                <wa-button
                    :appearance="activeTabId === 'main' ? 'outlined' : 'plain'"
                    :variant="activeTabId === 'main' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Chat
                    <wa-icon v-if="hasChatComments" slot="end" name="comment" variant="regular" class="tab-comments-indicator"></wa-icon>
                    <wa-icon
                        v-if="store.getPendingRequest(sessionId)"
                        slot="end"
                        :id="`session-tab-chat-${sessionId}-pending-request`"
                        name="hand"
                        class="pending-request-indicator"
                    ></wa-icon>
                </wa-button>
                <AppTooltip v-if="store.getPendingRequest(sessionId)" :for="`session-tab-chat-${sessionId}-pending-request`">Waiting for your response</AppTooltip>
            </wa-tab>

            <!-- Subagent tabs with close button -->
            <template v-for="tab in openSubagentTabs" :key="tab.id">
                <wa-tab slot="nav" :panel="tab.id">
                    <wa-button
                        :appearance="activeTabId === tab.id ? 'outlined' : 'plain'"
                        :variant="activeTabId === tab.id ? 'brand' : 'neutral'"
                        size="small"
                    >
                        <span class="subagent-tab-content">
                            <span>Agent "{{ getAgentShortId(tab.agentId) }}"</span>
                            <ProcessIndicator
                                v-if="store.getProcessState(tab.agentId)"
                                :state="store.getProcessState(tab.agentId).state"
                                size="small"
                            />
                            <wa-icon v-if="hasAgentComments(tab.agentId)" name="comment" variant="regular" class="tab-comments-indicator"></wa-icon>
                            <span class="tab-close-icon" @click.stop="closeTab(tab.id)">
                                <wa-icon name="xmark" label="Close tab"></wa-icon>
                            </span>
                        </span>
                    </wa-button>
                </wa-tab>
            </template>

            <!-- Tool tabs (always visible, not closeable) -->
            <wa-tab slot="nav" panel="files">
                <wa-button
                    :appearance="activeTabId === 'files' ? 'outlined' : 'plain'"
                    :variant="activeTabId === 'files' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Files
                    <wa-icon v-if="hasFilesComments" slot="end" name="comment" variant="regular" class="tab-comments-indicator"></wa-icon>
                </wa-button>
            </wa-tab>
            <wa-tab v-if="hasGitRepo" slot="nav" panel="git">
                <wa-button
                    :appearance="activeTabId === 'git' ? 'outlined' : 'plain'"
                    :variant="activeTabId === 'git' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Git
                    <wa-icon v-if="hasGitComments" slot="end" name="comment" variant="regular" class="tab-comments-indicator"></wa-icon>
                </wa-button>
            </wa-tab>
            <wa-tab slot="nav" panel="terminal">
                <wa-button
                    :appearance="activeTabId === 'terminal' ? 'outlined' : 'plain'"
                    :variant="activeTabId === 'terminal' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Terminal
                </wa-button>
            </wa-tab>

            <!-- Main session panel -->
            <wa-tab-panel name="main">
                <SessionItemsList
                    ref="sessionItemsListRef"
                    :session-id="sessionId"
                    :project-id="projectId"
                    @needs-title="handleNeedsTitle"
                />
            </wa-tab-panel>

            <!-- Subagent panels -->
            <wa-tab-panel
                v-for="tab in openSubagentTabs"
                :key="tab.id"
                :name="tab.id"
            >
                <SessionContent
                    :session-id="tab.agentId"
                    :parent-session-id="sessionId"
                    :project-id="projectId"
                />
            </wa-tab-panel>

            <!-- Tool panels -->
            <wa-tab-panel name="files">
                <FilesPanel
                    ref="filesPanelRef"
                    :project-id="session?.project_id"
                    :session-id="session?.id"
                    :synced-git-dir="syncedGitDirPath"
                    :git-directory="session?.git_directory"
                    :session-cwd="session?.cwd"
                    :project-git-root="store.getProject(session?.project_id)?.git_root"
                    :project-directory="store.getProject(session?.project_id)?.directory"
                    :active="isActive && activeTabId === 'files'"
                    :is-draft="session?.draft === true"
                    @root-changed="onRootChanged"
                />
            </wa-tab-panel>
            <wa-tab-panel v-if="hasGitRepo" name="git">
                <GitPanel
                    :project-id="session?.project_id"
                    :session-id="session?.id"
                    :synced-git-dir="syncedGitDirPath"
                    :git-directory="session?.git_directory"
                    :project-git-root="store.getProject(session?.project_id)?.git_root"
                    :initial-branch="session?.git_branch || ''"
                    :active="isActive && activeTabId === 'git'"
                    :is-draft="session?.draft === true"
                    @root-changed="onRootChanged"
                />
            </wa-tab-panel>
            <wa-tab-panel name="terminal">
                <TerminalPanel
                    :session-id="session?.id"
                    :active="isActive && activeTabId === 'terminal'"
                />
            </wa-tab-panel>
        </wa-tab-group>

        <!-- No session state -->
        <div v-else class="empty-state">
            <wa-spinner></wa-spinner>
            <span>Loading session...</span>
        </div>
    </div>
</template>

<style scoped>
.session-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    position: relative;
}

.session-view > wa-divider {
    flex-shrink: 0;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Tab group styles
   ═══════════════════════════════════════════════════════════════════════════ */

.session-tabs {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    --indicator-color: transparent;
    --track-width: 4px;
}

.session-tabs::part(base) {
    height: 100%;
    overflow: hidden;
}

.session-tabs::part(body) {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}

.session-tabs :deep(wa-tab-panel::part(base)) {
    padding: 0;
}

wa-tab::part(base) {
    padding: var(--wa-space-xs);
}

/* Active tab panel needs to fill available space and handle overflow */
.session-tabs :deep(wa-tab-panel[active]) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.session-tabs :deep(wa-tab-panel[active])::part(base) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

/* Subagent tab content wrapper */
.subagent-tab-content {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.tab-close-icon {
    aspect-ratio: 1;
    height: 3em;
    margin-right: -1em;
    width: auto;
    font-size: 0.75rem;
    opacity: 0.5;
    cursor: pointer;
    transition: opacity 0.15s ease;
    display: grid;
    place-items: center;
}

.tab-close-icon:hover {
    opacity: 1;
}

.tab-comments-indicator {
    color: var(--wa-color-brand);
    font-size: var(--wa-font-size-xs);
    flex-shrink: 0;
}

.pending-request-indicator {
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-s);
    animation: pending-pulse 1.5s ease-in-out infinite;
    flex-shrink: 0;
    align-self: center;
}

@keyframes pending-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}


/* ═══════════════════════════════════════════════════════════════════════════
   Empty state
   ═══════════════════════════════════════════════════════════════════════════ */

.empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 200px;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}

/* ═══════════════════════════════════════════════════════════════════════════
   Compact mode: tab nav inside header overlay
   ═══════════════════════════════════════════════════════════════════════════ */

/* Hidden by default on large viewports */
.compact-tab-nav {
    display: none;
}

@media (max-height: 900px) {
    /* Hide the real tab-group nav in compact mode */
    .session-tabs::part(nav) {
        display: none;
    }

    /* Show the compact tab nav inside the header overlay */
    .compact-tab-nav {
        display: flex;
        align-items: center;
        position: relative;
        padding-inline: var(--wa-space-xs);
        padding-bottom: var(--wa-space-xs);
    }

    /* When overflowing, add padding on both sides for the scroll arrows */
    .compact-tab-nav.has-scroll-controls {
        padding-inline: calc(var(--wa-space-xs) + 1.5em);
    }

    /* Scrollable area: horizontal scroll with hidden scrollbar */
    .compact-tab-scroll-area {
        display: flex;
        gap: var(--wa-space-2xs);
        overflow-x: auto;
        scrollbar-width: none; /* Firefox */
        flex: 1;
        min-width: 0;
    }

    .compact-tab-scroll-area::-webkit-scrollbar {
        height: 0; /* Chrome/Safari */
    }

    /* Prevent tabs from shrinking */
    .compact-tab-scroll-area > wa-button {
        flex-shrink: 0;
    }

    /* Scroll arrow buttons — same style as wa-tab-group */
    .compact-tab-scroll {
        position: absolute;
        top: 0;
        bottom: 0;
        width: 1.5em;
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 1;
        transition: opacity 0.15s ease;
    }

    .compact-tab-scroll.scroll-disabled {
        opacity: 0;
        pointer-events: none;
    }

    .compact-tab-scroll-start {
        left: var(--wa-space-xs);
    }

    .compact-tab-scroll-end {
        right: var(--wa-space-xs);
    }
}
</style>
