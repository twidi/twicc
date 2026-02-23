<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import ProjectList from '../components/ProjectList.vue'
import FetchErrorPanel from '../components/FetchErrorPanel.vue'
import SettingsPopover from '../components/SettingsPopover.vue'
import ActivitySparkline from '../components/ActivitySparkline.vue'
import AppTooltip from '../components/AppTooltip.vue'

const router = useRouter()
const store = useDataStore()

// Total sessions count (sum of all projects)
const totalSessionsCount = computed(() =>
    store.getProjects.reduce((sum, p) => sum + (p.sessions_count || 0), 0)
)

// Loading and error states
const isLoading = computed(() => store.isProjectsListLoading)
const hasError = computed(() => store.didProjectsListFailToLoad)

function handleProjectSelect(project) {
    router.push({ name: 'project', params: { projectId: project.id } })
}

// Global weekly activity from the store
const globalWeeklyActivity = computed(() => store.weeklyActivity._global || [])

async function handleRetry() {
    await store.loadHomeData()
}
</script>

<template>
    <div class="home-view">
        <header class="home-header">
            <h1>Claude Code Projects</h1>
            <span id="home-global-sparkline" class="global-sparkline">
                <ActivitySparkline :data="globalWeeklyActivity" />
            </span>
            <AppTooltip for="home-global-sparkline">Overall activity (user messages sent per week)</AppTooltip>
            <router-link :to="{ name: 'projects-all' }" class="view-all-link">
                View all sessions ({{ totalSessionsCount }})
            </router-link>
        </header>
        <main class="home-content">
            <!-- Error state -->
            <FetchErrorPanel
                v-if="hasError"
                :loading="isLoading"
                @retry="handleRetry"
            >
                Failed to load projects
            </FetchErrorPanel>

            <!-- Loading state -->
            <div v-else-if="isLoading" class="loading-state">
                <wa-spinner></wa-spinner>
                <span>Loading projects...</span>
            </div>

            <!-- Normal content -->
            <ProjectList v-else @select="handleProjectSelect" />
        </main>

        <div class="home-settings">
            <SettingsPopover />
        </div>
    </div>
</template>

<style scoped>
.home-view {
    padding: var(--wa-space-l);
    max-width: 900px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    /* The root has overflow:hidden for ProjectView/SessionView which manage their
       own internal scroll. HomeView needs its own scrollable container. */
    height: 100vh;
    overflow-y: auto;
}

.home-header {
    display: flex;
    justify-content: start;
    align-items: baseline;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    margin-bottom: var(--wa-space-xl);
    flex-shrink: 0;
}

.home-header h1 {
    margin: 0;
    font-size: var(--wa-font-size-2xl);
    font-weight: 700;
    color: var(--wa-color-text-normal);
}

.view-all-link {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-decoration: none;
    margin-left: auto;
}

.view-all-link:hover {
    color: var(--wa-color-text-normal);
    text-decoration: underline;
}

.global-sparkline {
    flex-shrink: 0;
}

.home-content {
    flex: 1;
    display: flex;
    flex-direction: column;
}

.loading-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    flex: 1;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-m);
}

.home-settings {
    position: fixed;
    bottom: var(--wa-space-s);
    left: var(--wa-space-s);
}
</style>
