<script setup>
import { computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useStartupPolling } from '../composables/useStartupPolling'
import ProjectList from '../components/ProjectList.vue'
import FetchErrorPanel from '../components/FetchErrorPanel.vue'
import SettingsPopover from '../components/SettingsPopover.vue'
import ActivitySparkline from '../components/ActivitySparkline.vue'
import AppTooltip from '../components/AppTooltip.vue'
import StartupProgressCallout from '../components/StartupProgressCallout.vue'

const router = useRouter()
const store = useDataStore()

// Poll home data during startup so sparklines and project stats update
// as sessions are indexed by background compute.
useStartupPolling(() => store.loadHomeData())

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

// Allow native page scroll on HomeView (the root has overflow:hidden for
// ProjectView/SessionView which manage their own internal scroll panels).
onMounted(() => {
    document.documentElement.style.overflowY = 'auto'
})
onUnmounted(() => {
    document.documentElement.style.overflowY = ''
})
</script>

<template>
    <div class="home-view">
        <header class="home-header">
            <h1>Claude Code Projects</h1>
            <span id="home-global-sparkline" class="global-sparkline">
                <ActivitySparkline :data="globalWeeklyActivity" />
            </span>
            <AppTooltip for="home-global-sparkline">Overall activity (message turns per week)</AppTooltip>
            <wa-button v-if="totalSessionsCount > 0" class="view-all-button" variant="brand" appearance="filled-outlined" size="small" @click="router.push({ name: 'projects-all' })">
                All {{ totalSessionsCount }} session{{ totalSessionsCount === 1 ? '' : 's' }} <wa-icon slot="end" name="arrow-right"></wa-icon>
            </wa-button>
        </header>

        <!-- Startup progress (initial sync / background compute) -->
        <StartupProgressCallout />

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
    /* Native page scroll is enabled via onMounted (overrides :root overflow:hidden) */
    min-height: 100vh;
}

.home-header {
    display: flex;
    justify-content: start;
    align-items: center;
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

.view-all-button {
    margin-left: auto;
}

.global-sparkline {
    flex-shrink: 0;
}

.home-content {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
}

.loading-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    min-height: 50vh;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-m);
}

.home-settings {
    position: fixed;
    bottom: var(--wa-space-s);
    left: var(--wa-space-s);
}
</style>
