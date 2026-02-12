<script setup>
import { computed, watch, ref, readonly, provide, onActivated, onDeactivated } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import SessionHeader from '../components/SessionHeader.vue'
import SessionItemsList from '../components/SessionItemsList.vue'
import SessionContent from '../components/SessionContent.vue'
import FilesPanel from '../components/FilesPanel.vue'
import GitPanel from '../components/GitPanel.vue'
import TerminalPanel from '../components/TerminalPanel.vue'

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const settingsStore = useSettingsStore()

// Reference to session header for opening rename dialog
const sessionHeaderRef = ref(null)

// Reference to session items list for scroll compensation
const sessionItemsListRef = ref(null)

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

onActivated(() => {
    isActive.value = true

    // Check viewport height and start listening for resize events
    checkViewportHeight()
    window.addEventListener('resize', checkViewportHeight)

    // Restore last active tab from store (KeepAlive preserves component state,
    // but the route is global — navigating to a session always lands on /session/:id
    // which maps to 'main'. If the user was on a different tab, restore it.)
    restoreActiveTab()
})

onDeactivated(() => {
    isActive.value = false

    // Stop listening for resize events while deactivated
    window.removeEventListener('resize', checkViewportHeight)
})

provide('sessionActive', readonly(isActive))

// ═══════════════════════════════════════════════════════════════════════════
// Auto-hide header/footer on small viewports (behind feature flag)
// ═══════════════════════════════════════════════════════════════════════════

// Whether tooltips are enabled (from settings)
const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)

// Whether auto-hide is enabled (from settings)
const autoHideEnabled = computed(() => settingsStore.isAutoHideHeaderFooterEnabled)

// Threshold for small viewport detection (in pixels)
const SMALL_VIEWPORT_HEIGHT = 800

// Track if we're on a small viewport
const isSmallViewport = ref(false)

// Track header hidden state
const isHeaderHidden = ref(false)

// Track footer hidden state (inverted behavior: hidden when scrolling UP)
const isFooterHidden = ref(false)

// Effective values: only apply when feature flag is enabled
const effectiveHeaderHidden = computed(() => autoHideEnabled.value && isHeaderHidden.value)
const effectiveFooterHidden = computed(() => autoHideEnabled.value && isFooterHidden.value)
const effectiveTrackScrollDirection = computed(() => autoHideEnabled.value && isSmallViewport.value)

/**
 * Check viewport height and update isSmallViewport.
 */
function checkViewportHeight() {
    isSmallViewport.value = window.innerHeight < SMALL_VIEWPORT_HEIGHT
    // Reset header and footer visibility when viewport becomes large again
    if (!isSmallViewport.value) {
        if (isHeaderHidden.value) {
            isHeaderHidden.value = false
        }
        if (isFooterHidden.value) {
            isFooterHidden.value = false
        }
    }
}

/**
 * Handle scroll direction changes from SessionItemsList.
 * @param {'up' | 'down'} direction
 */
function onScrollDirection(direction) {
    if (!autoHideEnabled.value || !isSmallViewport.value) return

    if (direction === 'down') {
        // Scroll down: hide header, show footer
        isHeaderHidden.value = true
        isFooterHidden.value = false
    } else if (direction === 'up') {
        // Scroll up: show header, hide footer
        isHeaderHidden.value = false
        isFooterHidden.value = true
    }
}

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
 * Handle tab change event from wa-tab-group.
 * Updates the URL to reflect the new active tab.
 */
function onTabShow(event) {
    const panel = event.detail?.name
    if (!panel) return

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
 * Open the rename dialog for the session.
 * Called when user sends a message from a draft session without a title.
 * Shows a contextual hint since the message is being sent in parallel.
 */
function handleNeedsTitle() {
    sessionHeaderRef.value?.openRenameDialog({ showHint: true })
}
</script>

<template>
    <div class="session-view">
        <!-- Main session header (always visible, above tabs) -->
        <SessionHeader
            v-if="session"
            ref="sessionHeaderRef"
            :session-id="sessionId"
            mode="session"
            :hidden="effectiveHeaderHidden"
        />

        <wa-tab-group
            v-if="session"
            :active="activeTabId"
            @wa-tab-show="onTabShow"
            class="session-tabs"
        >
            <!-- Tab navigation -->
            <wa-tab slot="nav" panel="main">
                <wa-button
                    :appearance="activeTabId === 'main' ? 'outlined' : 'plain'"
                    :variant="activeTabId === 'main' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Chat
                    <wa-icon
                        v-if="store.getPendingRequest(sessionId)"
                        slot="end"
                        :id="`session-tab-chat-${sessionId}-pending-request`"
                        name="hand"
                        class="pending-request-indicator"
                    ></wa-icon>
                </wa-button>
                <wa-tooltip v-if="tooltipsEnabled && store.getPendingRequest(sessionId)" :for="`session-tab-chat-${sessionId}-pending-request`">Waiting for your response</wa-tooltip>
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
                </wa-button>
            </wa-tab>
            <wa-tab v-if="hasGitRepo" slot="nav" panel="git">
                <wa-button
                    :appearance="activeTabId === 'git' ? 'outlined' : 'plain'"
                    :variant="activeTabId === 'git' ? 'brand' : 'neutral'"
                    size="small"
                >
                    Git
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
                    :track-scroll-direction="effectiveTrackScrollDirection"
                    :footer-hidden="effectiveFooterHidden"
                    @needs-title="handleNeedsTitle"
                    @scroll-direction="onScrollDirection"
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
                    :project-id="session?.project_id"
                    :session-id="session?.id"
                    :git-directory="session?.git_directory"
                    :project-git-root="store.getProject(session?.project_id)?.git_root"
                    :project-directory="store.getProject(session?.project_id)?.directory"
                    :active="isActive && activeTabId === 'files'"
                    :is-draft="session?.draft === true"
                />
            </wa-tab-panel>
            <wa-tab-panel v-if="hasGitRepo" name="git">
                <GitPanel
                    :project-id="session?.project_id"
                    :session-id="session?.id"
                    :git-directory="session?.git_directory || store.getProject(session?.project_id)?.git_root"
                    :initial-branch="session?.git_branch || ''"
                    :active="isActive && activeTabId === 'git'"
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
</style>
