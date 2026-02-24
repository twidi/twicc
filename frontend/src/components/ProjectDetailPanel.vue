<script setup>
// ProjectDetailPanel.vue - Detail panel shown when no session is selected.
// Handles two modes:
// - Single project mode: shows project name, directory, sessions count, cost, date, edit button, sparkline
// - All projects mode: shows aggregate info (total sessions, total cost, last update, global sparkline)

import { ref, computed } from 'vue'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate } from '../utils/date'
import { SESSION_TIME_FORMAT } from '../constants'
import ProjectBadge from './ProjectBadge.vue'
import ProjectProcessIndicator from './ProjectProcessIndicator.vue'
import ProjectEditDialog from './ProjectEditDialog.vue'
import ActivitySparkline from './ActivitySparkline.vue'
import CostDisplay from './CostDisplay.vue'
import ContributionGraphs from './ContributionGraphs.vue'
import AppTooltip from './AppTooltip.vue'

const props = defineProps({
    /** Project ID or ALL_PROJECTS_ID for aggregate view */
    projectId: {
        type: String,
        required: true,
    },
})

const store = useDataStore()
const settingsStore = useSettingsStore()

// Costs setting
const showCosts = computed(() => settingsStore.areCostsShown)

// Session time format setting
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)

/**
 * Convert Unix timestamp (seconds) to Date object for wa-relative-time.
 */
function timestampToDate(timestamp) {
    return new Date(timestamp * 1000)
}

// Mode detection
const isAllProjectsMode = computed(() => props.projectId === ALL_PROJECTS_ID)

// Single project data
const project = computed(() => isAllProjectsMode.value ? null : store.getProject(props.projectId))

// All projects data (for aggregate mode)
const allProjects = computed(() => store.getProjects)

// Display name
const displayName = computed(() => {
    if (isAllProjectsMode.value) return 'All Projects'
    return store.getProjectDisplayName(props.projectId)
})

// Directory (single project only)
const directory = computed(() => project.value?.directory || null)

// Sessions count
const sessionsCount = computed(() => {
    if (isAllProjectsMode.value) {
        return allProjects.value.reduce((sum, p) => sum + (p.sessions_count || 0), 0)
    }
    return project.value?.sessions_count || 0
})

// Total cost
const totalCost = computed(() => {
    if (isAllProjectsMode.value) {
        const sum = allProjects.value.reduce((s, p) => s + (p.total_cost || 0), 0)
        return sum > 0 ? sum : null
    }
    return project.value?.total_cost ?? null
})

// Last activity (mtime)
const mtime = computed(() => {
    if (isAllProjectsMode.value) {
        // Most recent mtime among all projects
        if (allProjects.value.length === 0) return null
        return Math.max(...allProjects.value.map(p => p.mtime || 0))
    }
    return project.value?.mtime || null
})

// Weekly activity data
const weeklyActivity = computed(() => {
    if (isAllProjectsMode.value) {
        return store.weeklyActivity._global || []
    }
    return store.weeklyActivity[props.projectId] || []
})

// Edit dialog ref (single project only)
const editDialogRef = ref(null)

function handleEditClick() {
    editDialogRef.value?.open()
}
</script>

<template>
    <div class="project-detail-panel">
        <div class="detail-content">
            <!-- Header: project name + process indicator + edit button -->
            <div class="detail-header">
                <div class="detail-title-row">
                    <template v-if="!isAllProjectsMode">
                        <ProjectBadge :project-id="projectId" class="detail-title" />
                        <span :id="`detail-sparkline-${projectId}`" class="detail-sparkline">
                            <ActivitySparkline
                                :id-suffix="`${projectId}-detail`"
                                :data="weeklyActivity"
                            />
                        </span>
                        <AppTooltip :for="`detail-sparkline-${projectId}`">Project activity (message turns per week)</AppTooltip>
                        <ProjectProcessIndicator :project-id="projectId" size="small" />
                    </template>
                    <template v-else>
                        <h2 class="detail-title all-projects-title">{{ displayName }}</h2>
                        <span id="detail-sparkline-all-projects" class="detail-sparkline">
                            <ActivitySparkline
                                id-suffix="all-projects-detail"
                                :data="weeklyActivity"
                            />
                        </span>
                        <AppTooltip for="detail-sparkline-all-projects">Overall activity (message turns per week)</AppTooltip>
                    </template>
                </div>
                <wa-button
                    v-if="!isAllProjectsMode"
                    id="detail-edit-button"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="edit-button"
                    @click="handleEditClick"
                >
                    <wa-icon name="pencil"></wa-icon>
                </wa-button>
                <AppTooltip v-if="!isAllProjectsMode" for="detail-edit-button">Edit project (name and color)</AppTooltip>
            </div>

            <!-- Directory (single project only) -->
            <div v-if="!isAllProjectsMode && directory" class="detail-directory">
                <wa-icon name="folder" class="detail-icon"></wa-icon>
                <span>{{ directory }}</span>
            </div>

            <!-- Meta info -->
            <div class="detail-meta">
                <div id="detail-sessions-count" class="detail-meta-item">
                    <wa-icon name="folder-open" class="detail-icon" variant="regular"></wa-icon>
                    <span>{{ sessionsCount }} session{{ sessionsCount !== 1 ? 's' : '' }}</span>
                </div>
                <AppTooltip for="detail-sessions-count">Number of sessions</AppTooltip>

                <template v-if="showCosts">
                    <CostDisplay id="detail-cost" :cost="totalCost" class="detail-meta-item" />
                    <AppTooltip for="detail-cost">Total cost</AppTooltip>
                </template>

                <div v-if="mtime" id="detail-mtime" class="detail-meta-item">
                    <wa-icon name="clock" class="detail-icon" variant="regular"></wa-icon>
                    <span>
                        <wa-relative-time v-if="useRelativeTime" :date.prop="timestampToDate(mtime)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                        <template v-else>{{ formatDate(mtime) }}</template>
                    </span>
                </div>
                <AppTooltip v-if="mtime" for="detail-mtime">{{ useRelativeTime ? `Last activity: ${formatDate(mtime)}` : 'Last activity' }}</AppTooltip>
            </div>

            <!-- Contribution graphs (daily activity heatmaps) -->
            <ContributionGraphs :project-id="projectId" />

        </div>

        <!-- Edit dialog (single project only) -->
        <ProjectEditDialog v-if="!isAllProjectsMode" ref="editDialogRef" :project="project" />
    </div>
</template>

<style scoped>
.project-detail-panel {
    display: flex;
    align-items: start;
    justify-content: center;
    height: 100%;
    padding-top: var(--wa-space-s);
    overflow-y: auto;
    padding-bottom: 3rem;
}

.detail-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    width: 100%;
}

.detail-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
    padding-inline: var(--wa-space-m);
}

.detail-title-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    min-width: 0;
    flex: 1;
}

.detail-title {
    font-weight: 600;
    font-size: var(--wa-font-size-xl);
    min-width: 0;
}

.all-projects-title {
    margin: 0;
    color: var(--wa-color-text-normal);
}

.edit-button {
    flex-shrink: 0;
}

.detail-directory {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    word-break: break-all;
    padding-inline: var(--wa-space-m);
}

.detail-icon {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.detail-meta {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-m);
    padding-inline: var(--wa-space-m);
}

.detail-meta-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.detail-sparkline {
    flex-shrink: 0;
}

wa-divider {
    --spacing: 0;
    --width: 4px;
}
</style>
