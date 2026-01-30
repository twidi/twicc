<script setup>
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import SessionHeader from '../components/SessionHeader.vue'
import SessionItemsList from '../components/SessionItemsList.vue'
import SessionContent from '../components/SessionContent.vue'

const route = useRoute()
const router = useRouter()
const store = useDataStore()

// Current session from route params
const projectId = computed(() => route.params.projectId)
const sessionId = computed(() => route.params.sessionId)
const subagentId = computed(() => route.params.subagentId)

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
 * Handle display mode change from the header.
 */
function onModeChange(newMode) {
    store.setDisplayMode(newMode)
}

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
            name: 'session',
            params: {
                projectId: projectId.value,
                sessionId: sessionId.value
            }
        })
    } else if (panel.startsWith('agent-')) {
        // Navigate to subagent
        const agentId = panel.replace('agent-', '')
        router.push({
            name: 'session-subagent',
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
                name: 'session-subagent',
                params: {
                    projectId: projectId.value,
                    sessionId: sessionId.value,
                    subagentId: prevTab.agentId
                }
            })
        } else {
            // No more subagent tabs, go to main
            router.push({
                name: 'session',
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
 * Get short display ID for a subagent (first 3 characters).
 */
function getShortId(agentId) {
    return agentId.substring(0, 3)
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
</script>

<template>
    <div class="session-view">
        <!-- Main session header (always visible, above tabs) -->
        <SessionHeader
            v-if="session"
            :session-id="sessionId"
            mode="session"
            @mode-change="onModeChange"
        />

        <wa-divider v-if="session"></wa-divider>

        <!-- Tab system -->
        <wa-tab-group
            v-if="session"
            :active="activeTabId"
            @wa-tab-show="onTabShow"
            class="session-tabs"
        >
            <!-- Tab navigation -->
            <wa-tab slot="nav" panel="main">Session</wa-tab>

            <!-- Subagent tabs with close button -->
            <template v-for="tab in openSubagentTabs" :key="tab.id">
                <wa-tab slot="nav" :panel="tab.id">
                    <span class="subagent-tab-content">
                        <span>Agent {{ getShortId(tab.agentId) }}</span>
                        <wa-icon
                            name="xmark"
                            label="Close tab"
                            class="tab-close-icon"
                            @click.stop="closeTab(tab.id)"
                        ></wa-icon>
                    </span>
                </wa-tab>
            </template>

            <!-- Main session panel -->
            <wa-tab-panel name="main">
                <SessionItemsList
                    :session-id="sessionId"
                    :project-id="projectId"
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
}

.session-view > wa-divider {
    flex-shrink: 0;
}

.session-tabs {
    flex: 1;
    min-height: 0;
    overflow: hidden;
}

.session-tabs::part(base) {
    height: 100%;
    overflow: hidden;
}

.session-tabs::part(body) {
    flex: 1;
    min-height: 0;
    overflow: hidden;
}

.session-tabs::part(nav) {
    padding-left: var(--wa-space-m);
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
    font-size: 0.75rem;
    opacity: 0.5;
    cursor: pointer;
    transition: opacity 0.15s ease;
}

.tab-close-icon:hover {
    opacity: 1;
}

.empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 200px;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-m);
}
</style>

<style>
/* Non-scoped styles for shadow DOM parts - scoped styles don't work with ::part() */
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

.session-tabs wa-tab-panel[active] {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.session-tabs wa-tab-panel[active]::part(base) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}
</style>
