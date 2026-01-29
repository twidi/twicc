<script setup>
import { computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import SessionList from '../components/SessionList.vue'
import FetchErrorPanel from '../components/FetchErrorPanel.vue'

const route = useRoute()
const router = useRouter()
const store = useDataStore()

// Current project from route params
const projectId = computed(() => route.params.projectId)
const sessionId = computed(() => route.params.sessionId || null)

// All projects for the selector (already sorted by mtime desc in store)
const allProjects = computed(() => store.getProjects)

// Loading and error states for sessions
const areSessionsLoading = computed(() => store.areSessionsLoading(projectId.value))
const didSessionsFailToLoad = computed(() => store.didSessionsFailToLoad(projectId.value))

// Load sessions when project changes
watch(projectId, async (newProjectId) => {
    if (newProjectId) {
        await store.loadSessions(newProjectId, { isInitialLoading: true })
    }
}, { immediate: true })

// Retry loading sessions
async function handleRetry() {
    if (projectId.value) {
        await store.loadSessions(projectId.value, { isInitialLoading: true })
    }
}

// Handle project change from selector
function handleProjectChange(event) {
    const newProjectId = event.target.value
    if (newProjectId && newProjectId !== projectId.value) {
        router.push({ name: 'project', params: { projectId: newProjectId } })
    }
}

// Handle session selection
function handleSessionSelect(session) {
    router.push({
        name: 'session',
        params: { projectId: projectId.value, sessionId: session.id }
    })
}

// Navigate back to home
function handleBackHome() {
    router.push({ name: 'home' })
}
</script>

<template>
    <div class="project-view">
        <!-- Sidebar -->
        <aside class="sidebar">
            <div class="sidebar-header">
                <button class="back-button" @click="handleBackHome">
                    <wa-icon name="arrow-left"></wa-icon>
                </button>
                <wa-select
                    :value.attr="projectId"
                    @change="handleProjectChange"
                    class="project-selector"
                    size="small"
                >
                    <wa-option
                        v-for="p in allProjects"
                        :key="p.id"
                        :value="p.id"
                    >
                        {{ p.id }}
                    </wa-option>
                </wa-select>
            </div>

            <wa-divider></wa-divider>

            <div class="sidebar-sessions">
                <h3 class="sessions-title">Sessions</h3>

                <!-- Error state -->
                <FetchErrorPanel
                    v-if="didSessionsFailToLoad"
                    :loading="areSessionsLoading"
                    @retry="handleRetry"
                >
                    Failed to load sessions
                </FetchErrorPanel>

                <!-- Loading state -->
                <div v-else-if="areSessionsLoading" class="sessions-loading">
                    <wa-spinner></wa-spinner>
                    <span>Loading...</span>
                </div>

                <!-- Normal content -->
                <SessionList
                    v-else
                    :project-id="projectId"
                    :session-id="sessionId"
                    @select="handleSessionSelect"
                />
            </div>
        </aside>

        <!-- Main content area -->
        <main class="main-content">
            <router-view v-if="sessionId" />
            <div v-else class="empty-state">
                <p>Select a session from the list</p>
            </div>
        </main>
    </div>
</template>

<style scoped>
.project-view {
    display: flex;
    height: 100vh;
    overflow: hidden;
}

.sidebar {
    width: 280px;
    min-width: 280px;
    height: 100%;
    background: var(--wa-color-surface-alt);
    border-right: 1px solid var(--wa-color-surface-border);
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.sidebar-header {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-m);
}

.back-button {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    height: 32px;
    border: none;
    background: transparent;
    border-radius: var(--wa-radius-s);
    cursor: pointer;
    color: var(--wa-color-text);
    transition: background-color 0.15s ease;
}

.back-button:hover {
    background: var(--wa-color-surface-active);
}

.project-selector {
    flex: 1;
}

.sidebar wa-divider {
    flex-shrink: 0;
}

.sidebar-sessions {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: var(--wa-space-m);
    display: flex;
    flex-direction: column;
}

.sessions-title {
    margin: 0 0 var(--wa-space-m) 0;
    font-size: var(--wa-font-size-s);
    font-weight: 600;
    text-transform: uppercase;
    color: var(--wa-color-text-quiet);
    letter-spacing: 0.05em;
}

.main-content {
    flex: 1;
    min-width: 0;
    height: 100%;
    overflow: hidden;
    background: var(--wa-color-surface-default);
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
</style>
