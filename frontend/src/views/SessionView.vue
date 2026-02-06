<script setup>
import { computed, watch, ref, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import SessionHeader from '../components/SessionHeader.vue'
import SessionItemsList from '../components/SessionItemsList.vue'
import SessionContent from '../components/SessionContent.vue'

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const settingsStore = useSettingsStore()

// Tooltips setting
const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)

// Reference to session header for opening rename dialog
const sessionHeaderRef = ref(null)

// Reference to session items list for scroll compensation
const sessionItemsListRef = ref(null)

// ═══════════════════════════════════════════════════════════════════════════
// Auto-hide header on small viewports
// ═══════════════════════════════════════════════════════════════════════════

// Threshold for small viewport detection (in pixels)
const SMALL_VIEWPORT_HEIGHT = 800

// Track if we're on a small viewport
const isSmallViewport = ref(false)

// Track header hidden state
const isHeaderHidden = ref(false)

// Track footer hidden state (inverted behavior: hidden when scrolling UP)
const isFooterHidden = ref(false)

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
    if (!isSmallViewport.value) return

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

// Reset devtools panel to default height on divider double-click.
// wa-split-panel's drag handler calls preventDefault() on mousedown, which prevents
// native dblclick events from firing. We detect double-clicks manually by tracking
// rapid consecutive pointerdown events on the split panel host element, filtering
// only those whose composedPath includes the shadow DOM divider.
function resetDevToolsPanelToDefault() {
    if (isMobile()) return
    const splitPanel = document.querySelector('.session-content-split')
    if (splitPanel) {
        ignoringDevToolsReposition = true
        splitPanel.positionInPixels = DEFAULT_DEVTOOLS_PANEL_HEIGHT
        requestAnimationFrame(() => {
            ignoringDevToolsReposition = false
        })
        devToolsPanelState.height = DEFAULT_DEVTOOLS_PANEL_HEIGHT
        saveDevToolsPanelState({ open: true, height: DEFAULT_DEVTOOLS_PANEL_HEIGHT })
    }
}

const DOUBLE_CLICK_DELAY = 400 // ms
let lastDevToolsDividerPointerDown = 0

function handleDevToolsSplitPanelPointerDown(event) {
    // Only react to pointerdown events that originate from the divider
    // (check composedPath to see through shadow DOM boundary)
    const splitPanel = event.currentTarget
    const path = event.composedPath()
    if (!path.includes(splitPanel.divider)) return

    const now = Date.now()
    if (now - lastDevToolsDividerPointerDown < DOUBLE_CLICK_DELAY) {
        lastDevToolsDividerPointerDown = 0
        resetDevToolsPanelToDefault()
    } else {
        lastDevToolsDividerPointerDown = now
    }
}

// Setup viewport height detection + restore devtools panel height
onMounted(() => {
    checkViewportHeight()
    window.addEventListener('resize', checkViewportHeight)

    // Restore devtools panel height from localStorage (mirrors sidebar pattern)
    const splitPanel = document.querySelector('.session-content-split')
    if (splitPanel) {
        if (devToolsPanelState.height !== DEFAULT_DEVTOOLS_PANEL_HEIGHT) {
            splitPanel.positionInPixels = devToolsPanelState.height
        }
        // Listen for pointerdown on the host element (capture phase) to detect
        // double-clicks on the divider. Native dblclick is blocked by the drag handler.
        splitPanel.addEventListener('pointerdown', handleDevToolsSplitPanelPointerDown, true)
    }
})

onUnmounted(() => {
    window.removeEventListener('resize', checkViewportHeight)
    // Clean up divider pointerdown listener
    const splitPanel = document.querySelector('.session-content-split')
    splitPanel?.removeEventListener('pointerdown', handleDevToolsSplitPanelPointerDown, true)
})

// Current session from route params
const projectId = computed(() => route.params.projectId)
const sessionId = computed(() => route.params.sessionId)
const subagentId = computed(() => route.params.subagentId)

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() =>
    route.name === 'projects-session' ||
    route.name === 'projects-session-subagent'
)

// Session data
const session = computed(() => store.getSession(sessionId.value))

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

// Active tab ID ('main' for session, 'agent-xxx' for subagents)
// Computed from route
const activeTabId = computed(() => {
    if (subagentId.value) {
        return `agent-${subagentId.value}`
    }
    return 'main'
})

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

// Watch subagentId to open tab when navigating to a subagent URL
watch(subagentId, (newSubagentId) => {
    if (newSubagentId) {
        openSubagentTab(newSubagentId)
    }
    // Update active tab in store
    if (sessionId.value) {
        store.setSessionActiveTab(sessionId.value, activeTabId.value)
    }
}, { immediate: true })

/**
 * Open the rename dialog for the session.
 * Called when user sends a message from a draft session without a title.
 * Shows a contextual hint since the message is being sent in parallel.
 */
function handleNeedsTitle() {
    sessionHeaderRef.value?.openRenameDialog({ showHint: true })
}

// ═══════════════════════════════════════════════════════════════════════════
// DevTools Panel state persistence (mirrors sidebar pattern from ProjectView)
// ═══════════════════════════════════════════════════════════════════════════

const DEVTOOLS_PANEL_STORAGE_KEY = 'twicc-devtools-panel-state'
const DEFAULT_DEVTOOLS_PANEL_HEIGHT = 500
const DEVTOOLS_PANEL_COLLAPSE_THRESHOLD = 70
const MOBILE_BREAKPOINT = 640

const isMobile = () => window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`).matches

// Load panel state from localStorage
function loadDevToolsPanelState() {
    try {
        const stored = localStorage.getItem(DEVTOOLS_PANEL_STORAGE_KEY)
        if (stored) {
            const parsed = JSON.parse(stored)
            return {
                open: typeof parsed.open === 'boolean' ? parsed.open : false,
                height: typeof parsed.height === 'number' && parsed.height > 0 ? parsed.height : DEFAULT_DEVTOOLS_PANEL_HEIGHT,
            }
        }
    } catch (e) {
        console.warn('Failed to load devtools panel state from localStorage:', e)
    }
    return { open: false, height: DEFAULT_DEVTOOLS_PANEL_HEIGHT }
}

// Save panel state to localStorage
function saveDevToolsPanelState(state) {
    try {
        localStorage.setItem(DEVTOOLS_PANEL_STORAGE_KEY, JSON.stringify(state))
    } catch (e) {
        console.warn('Failed to save devtools panel state to localStorage:', e)
    }
}

// Current panel state (non-reactive, applied once at mount — mirrors sidebarState pattern)
const devToolsPanelState = loadDevToolsPanelState()

// Initial checkbox state: checked = open (NOT inverted like sidebar)
const initialDevToolsPanelChecked = computed(() => {
    return devToolsPanelState.open
})

// Guard flag to ignore reposition events triggered by height restore after auto-collapse.
// When we auto-collapse and restore the stored height, wa-split-panel fires a new
// wa-reposition event. Without this guard, we'd process that event as a normal resize.
let ignoringDevToolsReposition = false

// Handle split panel reposition: auto-collapse when dragged to threshold, persist height.
// Ignored on mobile where the split panel is not used (panel is an overlay).
function handleDevToolsPanelReposition(event) {
    // Stop propagation to prevent this event from bubbling up to ProjectView's
    // sidebar split panel handler, which would misinterpret the panel height
    // as a sidebar width and auto-collapse the sidebar.
    event.stopPropagation()
    if (isMobile()) return
    if (ignoringDevToolsReposition) return

    const checkbox = document.getElementById('devtools-panel-toggle-state')
    if (!checkbox) return

    const newHeight = event.target.positionInPixels

    if (newHeight <= DEVTOOLS_PANEL_COLLAPSE_THRESHOLD) {
        // Auto-collapse: mark as closed, reset height to stored value
        checkbox.checked = false
        saveDevToolsPanelState({ open: false, height: devToolsPanelState.height })
        // Restore height, ignoring the resulting reposition event
        ignoringDevToolsReposition = true
        requestAnimationFrame(() => {
            event.target.positionInPixels = devToolsPanelState.height
            requestAnimationFrame(() => {
                ignoringDevToolsReposition = false
            })
        })
    } else {
        // Normal resize: update stored height
        devToolsPanelState.height = newHeight
        saveDevToolsPanelState({ open: true, height: newHeight })
    }
}

// Handle devtools panel toggle (called when checkbox changes)
function handleDevToolsPanelToggle(event) {
    const isOpen = event.target.checked  // checked = open (NOT inverted like sidebar)
    saveDevToolsPanelState({ open: isOpen, height: devToolsPanelState.height })
}

// Handle devtools tab switch: update button appearance/variant to match active state
function handleDevToolsTabShow(event) {
    const tabGroup = event.target.closest('wa-tab-group')
    if (!tabGroup) return
    tabGroup.querySelectorAll('wa-tab').forEach(tab => {
        const button = tab.querySelector('wa-button')
        if (!button) return
        const isActive = tab.getAttribute('panel') === event.detail?.name
        button.setAttribute('appearance', isActive ? 'outlined' : 'plain')
        button.setAttribute('variant', isActive ? 'brand' : 'neutral')
    })
}
</script>

<template>
    <div class="session-view">
        <!-- Hidden checkbox for pure CSS devtools panel toggle -->
        <input type="checkbox" id="devtools-panel-toggle-state" class="devtools-panel-toggle-checkbox" :checked="initialDevToolsPanelChecked" @change="handleDevToolsPanelToggle"/>

        <!-- Main session header (always visible, above tabs) -->
        <SessionHeader
            v-if="session"
            ref="sessionHeaderRef"
            :session-id="sessionId"
            mode="session"
            :hidden="isHeaderHidden"
        />

        <!-- Split panel: main content (start) + devtools panel (end) -->
        <wa-split-panel
            v-if="session"
            class="session-content-split"
            :position-in-pixels="DEFAULT_DEVTOOLS_PANEL_HEIGHT"
            primary="end"
            orientation="vertical"
            snap="75px 300px 600px 900x"
            snap-threshold="30"
            @wa-reposition="handleDevToolsPanelReposition"
        >
            <wa-icon slot="divider" name="grip-lines" class="divider-handle"></wa-icon>

            <!-- Main content slot -->
            <div slot="start" class="session-main-content">
                <wa-tab-group
                    :active="activeTabId"
                    @wa-tab-show="onTabShow"
                    class="session-tabs"
                    :class="{ 'one-tab-only': !openSubagentTabs.length }"
                >
                    <!-- Tab navigation -->
                    <wa-tab slot="nav" panel="main">
                        <wa-button
                            :appearance="activeTabId === 'main' ? 'outlined' : 'plain'"
                            :variant="activeTabId === 'main' ? 'brand' : 'neutral'"
                            size="small"
                        >
                            Session
                        </wa-button>
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

                    <!-- Main session panel -->
                    <wa-tab-panel name="main">
                        <SessionItemsList
                            ref="sessionItemsListRef"
                            :session-id="sessionId"
                            :project-id="projectId"
                            :track-scroll-direction="isSmallViewport"
                            :footer-hidden="isFooterHidden"
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
                </wa-tab-group>
            </div>

            <!-- DevTools Panel -->
            <aside slot="end" class="devtools-panel">
                <div class="devtools-panel-content">
                    <wa-tab-group class="devtools-tabs" active="git" @wa-tab-show="handleDevToolsTabShow">
                        <wa-tab slot="nav" panel="git">
                            <wa-button appearance="outlined" variant="brand" size="small">Git</wa-button>
                        </wa-tab>
                        <wa-tab slot="nav" panel="files">
                            <wa-button appearance="plain" variant="neutral" size="small">Files</wa-button>
                        </wa-tab>
                        <wa-tab slot="nav" panel="terminal">
                            <wa-button appearance="plain" variant="neutral" size="small">Terminal</wa-button>
                        </wa-tab>

                        <wa-tab-panel name="git">
                            <div class="devtools-panel-placeholder">Git (coming soon)</div>
                        </wa-tab-panel>
                        <wa-tab-panel name="files">
                            <div class="devtools-panel-placeholder">Files (coming soon)</div>
                        </wa-tab-panel>
                        <wa-tab-panel name="terminal">
                            <div class="devtools-panel-placeholder">Terminal (coming soon)</div>
                        </wa-tab-panel>
                    </wa-tab-group>
                </div>

                <label for="devtools-panel-toggle-state" class="devtools-panel-toggle" id="devtools-panel-toggle-label">
                    <span class="devtools-panel-backdrop"></span>
                    <wa-button variant="neutral" appearance="filled-outlined" size="small">
                        <wa-icon class="icon-collapse" name="angles-down"></wa-icon>
                        <wa-icon class="icon-expand" name="angles-up"></wa-icon>
                    </wa-button>
                </label>
                <wa-tooltip v-if="tooltipsEnabled" for="devtools-panel-toggle-label">Toggle dev tools panel</wa-tooltip>
            </aside>
        </wa-split-panel>

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
   DevTools Panel - Hidden checkbox for CSS-only toggle
   ═══════════════════════════════════════════════════════════════════════════ */

.devtools-panel-toggle-checkbox {
    position: absolute;
    opacity: 0;
    pointer-events: none;
}

/* ═══════════════════════════════════════════════════════════════════════════
   DevTools Panel - Split panel (tab-group + devtools)
   ═══════════════════════════════════════════════════════════════════════════ */

.session-content-split {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    --min: 0px;
    --max: 80%;
    &::part(divider) {
        z-index: 2;
        background-color: var(--wa-color-surface-border);
        height: 4px;
        transition: opacity .3s ease;
    }
}

/* Start slot wrapper: contains the wa-tab-group */
.session-main-content {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-height: 0;
}

/* Divider handle (visible on touch devices only) */
.divider-handle {
    color: var(--wa-color-surface-border);
    display: none;
    scale: 3;
}

@media (pointer: coarse) {
    .divider-handle {
        display: flex;
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
   Tab group styles (unchanged, now inside .session-main-content)
   ═══════════════════════════════════════════════════════════════════════════ */

.session-tabs {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    font-size: var(--wa-font-size-s);
    --indicator-color: transparent;
    --track-width: 4px;
    &.one-tab-only::part(tabs) {
        display: none;
    }
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

/* ═══════════════════════════════════════════════════════════════════════════
   DevTools Panel - Panel container and content
   ═══════════════════════════════════════════════════════════════════════════ */

.devtools-panel {
    --transition-duration: .3s;
    background: var(--wa-color-surface-default);
    display: flex;
    flex-direction: column;
    position: relative;
}

.devtools-panel-content {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    padding: 0;
    display: flex;
    flex-direction: column;
}

.devtools-tabs {
    flex: 1;
    min-height: 0;
    font-size: var(--wa-font-size-s);
    --indicator-color: transparent;
    --track-width: 4px;

    &::part(base) {
        height: 100%;
        flex-direction: column;
    }

    &::part(body) {
        flex: 1;
        min-height: 0;
        overflow: hidden;
    }

    wa-tab-panel {
        height: 100%;

        &::part(base) {
            height: 100%;
            padding: 0;
        }
    }

}

.devtools-panel-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

/* ═══════════════════════════════════════════════════════════════════════════
   DevTools Panel - Toggle button
   ═══════════════════════════════════════════════════════════════════════════ */

.devtools-panel-toggle {
    position: absolute;
    top: calc(-1 * var(--wa-space-s));
    left: 50%;
    transform: translateX(-50%) translateY(-100%);
    z-index: 10;
    cursor: pointer;
    opacity: 1;
    transition: opacity var(--transition-duration) ease;

    wa-button {
        pointer-events: none;
        wa-icon {
            position: relative;
            top: -1px;
        }
    }

    /* Default state: show expand icon (panel closed) */
    .icon-collapse {
        display: none;
    }
    .icon-expand {
        display: inline;
    }
}

/* When panel is open (checkbox checked), show collapse icon instead */
.session-view:has(.devtools-panel-toggle-checkbox:checked) .devtools-panel-toggle {
    top: calc(-1 * var(--wa-space-m));

    .icon-collapse {
        display: inline;
    }
    .icon-expand {
        display: none;
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
   DevTools Panel - Desktop collapse (checkbox NOT checked = collapsed)
   ═══════════════════════════════════════════════════════════════════════════ */

.session-view:has(.devtools-panel-toggle-checkbox:not(:checked)) .session-content-split {
    grid-template-rows: auto 0 0 !important;
    &::part(divider) {
        opacity: 0;
        pointer-events: none;
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
   DevTools Panel - Mobile overlay
   ═══════════════════════════════════════════════════════════════════════════ */

/* Hide backdrop by default (shown on mobile only) */
.devtools-panel-backdrop {
    display: none;
}

@media (width < 640px) {
    /* On mobile, the split panel divider is hidden — no resize on mobile */
    .session-content-split {
        &::part(divider) {
            display: none;
        }
    }

    /* Remove the desktop collapse override on mobile — let the split panel be normal */
    .session-view:has(.devtools-panel-toggle-checkbox:not(:checked)) .session-content-split {
        grid-template-rows: revert !important;
        &::part(divider) {
            opacity: revert;
            pointer-events: revert;
        }
    }

    /* Panel becomes a fixed drawer from the bottom */
    .devtools-panel {
        --panel-height: min(90vh, 90dvh);
        position: fixed;
        left: 0;
        bottom: 0;
        right: 0;
        height: var(--panel-height);
        z-index: 100;
        transform: translateY(100%);
        transition: transform var(--transition-duration) ease;
        box-shadow: var(--wa-shadow-xl);
        border-top: solid var(--wa-color-neutral-border-normal) 0.25rem;
    }

    /* Toggle button fixed at top-left when panel is closed, on the right of the sidebar one */
    .devtools-panel-toggle {
        position: fixed;
        top: calc(-1 * var(--wa-space-m));
        --toggle-left: calc(var(--wa-space-s) + 2.75rem + var(--wa-space-s));
        left: var(--toggle-left);
        transform: translateY(-100%);
        z-index: 10;
        transition: transform var(--transition-duration) ease, opacity var(--transition-duration) ease;
        /* Constrain label size for backdrop positioning */
        --checkbox-label-size: calc(var(--wa-form-control-height) - var(--wa-shadow-offset-y-s) * 2 + 2px);
        height: var(--checkbox-label-size);
        width: var(--checkbox-label-size);

        wa-button {
            position: absolute;
            top: 0;
            left: 0;
        }

        .icon-collapse {
            display: none;
        }
        .icon-expand {
            display: inline;
        }
    }

    /* Backdrop positioned sticky inside the label so clicking it closes the panel */
    .devtools-panel-backdrop {
        display: block;
        position: sticky;
        top: 0;
        left: 0;
        /* Position: move up and to the left to cover the viewport.
           The label is on the left (--toggle-left) and at the bottom.
           The he backdrop starts from the label's top-left corner.
           We need to shift it to cover the full viewport. */
        translate: calc(-1 * var(--toggle-left)) calc(-100vh + var(--checkbox-label-size) + var(--wa-space-s));
        width: 100vw;
        height: 100vh;
        pointer-events: none;
        transition: background var(--transition-duration) ease;
        background: transparent;
    }

    /* When panel is open (checkbox checked) on mobile */
    .session-view:has(.devtools-panel-toggle-checkbox:checked) {
        .devtools-panel {
            transform: translateY(0);
        }

        .devtools-panel-toggle {
            position: absolute;
            top: calc(-1 * var(--wa-space-m));
            transform: translateY(-100%);

            .icon-collapse {
                display: inline;
            }
            .icon-expand {
                display: none;
            }

            .devtools-panel-backdrop {
                pointer-events: all;
                background: rgba(0, 0, 0, 0.5);
            }
        }
    }
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

<style>
@media (width < 640px) {
    /* For whatever reason, in mobile mode, the toggle for the
       devtools panel is visible on top on the open sidebar.
       So we force hide it...
     */
    .project-view-wrapper:has(.sidebar-toggle-checkbox:checked) {
        .session-view:not(:has(.devtools-panel-toggle-checkbox:checked)) {
            .devtools-panel-toggle {
                opacity: 0;
                pointer-events: none;
            }
        }
    }
}
</style>
