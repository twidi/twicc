<script setup>
import { ref, computed } from 'vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate } from '../utils/date'
import { SESSION_TIME_FORMAT } from '../constants'
import ProjectEditDialog from './ProjectEditDialog.vue'
import ProjectBadge from './ProjectBadge.vue'
import ProjectProcessIndicator from './ProjectProcessIndicator.vue'
import ActivitySparkline from './ActivitySparkline.vue'
import CostDisplay from './CostDisplay.vue'
import AppTooltip from './AppTooltip.vue'

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
 * @param {number} timestamp - Unix timestamp in seconds
 * @returns {Date}
 */
function timestampToDate(timestamp) {
    return new Date(timestamp * 1000)
}


const emit = defineEmits(['select'])

// Ref for the edit dialog component
const editDialogRef = ref(null)
// Currently selected project for editing
const editingProject = ref(null)

function handleSelect(project) {
    emit('select', project)
}

function handleEditClick(event, project) {
    // Prevent the card click from triggering navigation
    event.stopPropagation()
    editingProject.value = project
    editDialogRef.value?.open()
}
</script>

<template>
    <div class="project-list">
        <wa-card
            v-for="project in store.getProjects"
            :key="project.id"
            class="project-card"
            appearance="outlined"
            @click="handleSelect(project)"
        >
            <div class="project-info">
                <div class="project-title-row">
                    <ProjectBadge :project-id="project.id" class="project-title" />
                    <ProjectProcessIndicator :project-id="project.id" size="small" />
                </div>
                <wa-button
                    :id="`edit-button-${project.id}`"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="edit-button"
                    @click="(e) => handleEditClick(e, project)"
                >
                    <wa-icon name="pencil"></wa-icon>
                </wa-button>
                <AppTooltip :for="`edit-button-${project.id}`">Edit project (name and color)</AppTooltip>
                <div v-if="project.directory" class="project-directory">{{ project.directory }}</div>
                <div class="project-meta-wrapper">
                    <div class="project-meta">
                        <span :id="`sessions-count-${project.id}`" class="sessions-count">
                            <wa-icon auto-width name="folder-open" variant="regular"></wa-icon>
                            <span>{{ project.sessions_count }} session{{ project.sessions_count !== 1 ? 's' : '' }}</span>
                        </span>
                        <AppTooltip :for="`sessions-count-${project.id}`">Number of sessions</AppTooltip>
                        <template v-if="showCosts">
                            <CostDisplay :id="`project-cost-${project.id}`" :cost="project.total_cost" class="project-cost" />
                            <AppTooltip :for="`project-cost-${project.id}`">Total project cost</AppTooltip>
                        </template>
                        <span :id="`project-mtime-${project.id}`" class="project-mtime">
                            <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                            <wa-relative-time v-if="useRelativeTime" :date.prop="timestampToDate(project.mtime)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                            <span v-else>{{ formatDate(project.mtime) }}</span>
                        </span>
                        <AppTooltip :for="`project-mtime-${project.id}`">{{ useRelativeTime ? `Last activity: ${formatDate(project.mtime)}` : 'Last activity' }}</AppTooltip>
                    </div>
                    <div :id="`project-sparkline-${project.id}`" class="project-graph">
                        <ActivitySparkline :id-suffix="project.id" :data="store.weeklyActivity[project.id] || []" />
                    </div>
                    <AppTooltip :for="`project-sparkline-${project.id}`">Project activity (user messages sent per week)</AppTooltip>
                </div>
            </div>
        </wa-card>
        <div v-if="store.getProjects.length === 0" class="empty-state">
            No projects found
        </div>
    </div>

    <!-- Edit dialog (rendered outside the list) -->
    <ProjectEditDialog ref="editDialogRef" :project="editingProject" />
</template>

<style scoped>
.project-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.project-card {
    cursor: pointer;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
    &::part(body) {
        position: relative;
    }
}

.project-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--wa-shadow-m);
}

.project-info {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.project-title-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xl);
    /* Leave space for the edit button */
    padding-right: calc(var(--wa-space-s) + 1.5em);
}

.project-title {
    font-weight: 600;
    font-size: var(--wa-font-size-m);
    min-width: 0;
}

.edit-button {
    position: absolute;
    top: calc(var(--spacing) / 2);
    right: calc(var(--spacing) / 2);
}

.project-directory {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    word-break: break-all;
}

.project-meta-wrapper {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.project-meta {
    display: flex;
    justify-content: start;
    gap: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);

    & > span {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }

}

.empty-state {
    text-align: center;
    padding: var(--wa-space-xl);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}
</style>
