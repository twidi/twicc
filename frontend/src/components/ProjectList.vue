<script setup>
import { ref, computed } from 'vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate } from '../utils/date'
import ProjectEditDialog from './ProjectEditDialog.vue'
import ProjectBadge from './ProjectBadge.vue'
import ProjectProcessIndicator from './ProjectProcessIndicator.vue'

const store = useDataStore()
const settingsStore = useSettingsStore()

// Tooltips setting
const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)
// Costs setting
const showCosts = computed(() => settingsStore.areCostsShown)

// Format cost as USD string (e.g., "$0.42")
function formatCost(cost) {
    if (cost == null) return null
    return `$${cost.toFixed(2)}`
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
                <wa-tooltip v-if="tooltipsEnabled" :for="`edit-button-${project.id}`">Edit project (name and color)</wa-tooltip>
                <div v-if="project.directory" class="project-directory">{{ project.directory }}</div>
                <div class="project-meta">
                    <span :id="`sessions-count-${project.id}`" class="sessions-count">
                        {{ project.sessions_count }} session{{ project.sessions_count !== 1 ? 's' : '' }}
                    </span>
                    <wa-tooltip v-if="tooltipsEnabled" :for="`sessions-count-${project.id}`">Number of sessions</wa-tooltip>
                    <span :id="`project-mtime-${project.id}`" class="project-mtime">{{ formatDate(project.mtime) }}</span>
                    <wa-tooltip v-if="tooltipsEnabled" :for="`project-mtime-${project.id}`">Last activity</wa-tooltip>
                    <template v-if="showCosts">
                        <span :id="`project-cost-${project.id}`" class="project-cost">{{ project.total_cost != null ? formatCost(project.total_cost) : '-' }}</span>
                        <wa-tooltip v-if="tooltipsEnabled" :for="`project-cost-${project.id}`">Total project cost</wa-tooltip>
                    </template>
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

.project-meta {
    display: flex;
    justify-content: space-between;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.empty-state {
    text-align: center;
    padding: var(--wa-space-xl);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}
</style>
