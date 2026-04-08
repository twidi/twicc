<script setup>
// ProjectDetailPanel.vue - Detail panel shown when no session is selected.
// Handles three modes:
// - Single project mode: shows project name, directory, sessions count, cost, date, edit button, sparkline
// - All projects mode: shows aggregate info (total sessions, total cost, last update, global sparkline)
// - Workspace mode: shows workspace name, aggregated stats from member projects, manage button

import { ref, computed } from 'vue'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { useWorkspacesStore } from '../stores/workspaces'
import { isWorkspaceProjectId, extractWorkspaceId } from '../utils/workspaceIds'
import { aggregateWeeklyActivity } from '../utils/activityAggregation'
import { formatDate } from '../utils/date'
import { SESSION_TIME_FORMAT } from '../constants'
import ProjectBadge from './ProjectBadge.vue'
import AggregatedProcessIndicator from './AggregatedProcessIndicator.vue'
import ProjectEditDialog from './ProjectEditDialog.vue'
import WorkspaceManageDialog from './WorkspaceManageDialog.vue'
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
const workspacesStore = useWorkspacesStore()

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
const isWorkspaceMode = computed(() => isWorkspaceProjectId(props.projectId))
const isSingleProjectMode = computed(() => !isAllProjectsMode.value && !isWorkspaceMode.value)

// Workspace data
const workspaceId = computed(() => isWorkspaceMode.value ? extractWorkspaceId(props.projectId) : null)
const workspace = computed(() => workspaceId.value ? workspacesStore.getWorkspaceById(workspaceId.value) : null)
const workspaceProjectIds = computed(() =>
    workspaceId.value ? workspacesStore.getVisibleProjectIds(workspaceId.value) : []
)
const workspaceProjects = computed(() =>
    workspaceProjectIds.value.map(pid => store.getProject(pid)).filter(Boolean)
)

// Single project data
const project = computed(() => isSingleProjectMode.value ? store.getProject(props.projectId) : null)

// All projects data (for aggregate mode)
const allProjects = computed(() => store.getProjects)

// Display name
const displayName = computed(() => {
    if (isAllProjectsMode.value) return 'All Projects'
    if (isWorkspaceMode.value) return workspace.value?.name || 'Workspace'
    return store.getProjectDisplayName(props.projectId)
})

// Directory (single project only)
const directory = computed(() => project.value?.directory || null)

// The list of projects to aggregate over (for all-projects and workspace modes)
const aggregatedProjects = computed(() => {
    if (isWorkspaceMode.value) return workspaceProjects.value
    if (isAllProjectsMode.value) return allProjects.value
    return []
})

// Sessions count
const sessionsCount = computed(() => {
    if (isSingleProjectMode.value) return project.value?.sessions_count || 0
    return aggregatedProjects.value.reduce((sum, p) => sum + (p.sessions_count || 0), 0)
})

// Total cost
const totalCost = computed(() => {
    if (isSingleProjectMode.value) return project.value?.total_cost ?? null
    const sum = aggregatedProjects.value.reduce((s, p) => s + (p.total_cost || 0), 0)
    return sum > 0 ? sum : null
})

// Last activity (mtime)
const mtime = computed(() => {
    if (isSingleProjectMode.value) return project.value?.mtime || null
    if (aggregatedProjects.value.length === 0) return null
    return Math.max(...aggregatedProjects.value.map(p => p.mtime || 0))
})

// Weekly activity data — for workspace mode, aggregate from member projects
const weeklyActivity = computed(() => {
    if (isAllProjectsMode.value) return store.weeklyActivity._global || []
    if (isWorkspaceMode.value) {
        return aggregateWeeklyActivity(workspaceProjectIds.value, store.weeklyActivity)
    }
    return store.weeklyActivity[props.projectId] || []
})

// Edit dialog ref (single project only)
const editDialogRef = ref(null)
// Workspace manage dialog ref
const manageDialogRef = ref(null)

function handleEditClick() {
    if (isWorkspaceMode.value) {
        manageDialogRef.value?.openForWorkspace(workspaceId.value)
    } else {
        editDialogRef.value?.open()
    }
}
</script>

<template>
    <div class="project-detail-panel">
        <div class="detail-content">
            <!-- Header: project/workspace name + process indicator + edit button -->
            <div class="detail-header">
                <div class="detail-title-row">
                    <!-- Single project mode -->
                    <template v-if="isSingleProjectMode">
                        <ProjectBadge :project-id="projectId" class="detail-title" />
                        <span :id="`detail-sparkline-${projectId}`" class="detail-sparkline">
                            <ActivitySparkline
                                :id-suffix="`${projectId}-detail`"
                                :data="weeklyActivity"
                            />
                        </span>
                        <AppTooltip :for="`detail-sparkline-${projectId}`">Project activity (message turns per week)</AppTooltip>
                        <AggregatedProcessIndicator :project-ids="[projectId]" size="small" />
                    </template>
                    <!-- Workspace or All Projects mode -->
                    <template v-else>
                        <h2 class="detail-title all-projects-title">
                            <wa-icon v-if="isWorkspaceMode" name="layer-group" auto-width :style="workspace?.color ? { color: workspace.color } : null"></wa-icon>
                            {{ displayName }}
                        </h2>
                        <span :id="`detail-sparkline-${projectId}`" class="detail-sparkline">
                            <ActivitySparkline
                                :id-suffix="`${projectId}-detail`"
                                :data="weeklyActivity"
                            />
                        </span>
                        <AppTooltip :for="`detail-sparkline-${projectId}`">{{ isWorkspaceMode ? 'Workspace' : 'Overall' }} activity (message turns per week)</AppTooltip>
                        <AggregatedProcessIndicator v-if="isWorkspaceMode" :project-ids="workspaceProjectIds" size="small" />
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
                    <wa-icon :name="isWorkspaceMode ? 'gear' : 'pencil'"></wa-icon>
                </wa-button>
                <AppTooltip v-if="isWorkspaceMode" for="detail-edit-button">Manage workspace</AppTooltip>
                <AppTooltip v-else-if="isSingleProjectMode" for="detail-edit-button">Edit project (name and color)</AppTooltip>
            </div>

            <!-- Directory (single project only) -->
            <div v-if="isSingleProjectMode && directory" class="detail-directory">
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
            <ContributionGraphs :project-id="projectId" :project-ids="isWorkspaceMode ? workspaceProjectIds : null" />

        </div>

        <!-- Edit dialog (single project only) -->
        <ProjectEditDialog v-if="isSingleProjectMode" ref="editDialogRef" :project="project" />
        <!-- Workspace manage dialog -->
        <WorkspaceManageDialog v-if="isWorkspaceMode" ref="manageDialogRef" />
    </div>
</template>

<style scoped>
.project-detail-panel {
    container: project-detail / inline-size;
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
