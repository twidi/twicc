<script setup>
import { ref, computed } from 'vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { buildProjectTree } from '../utils/projectTree'
import ProjectEditDialog from './ProjectEditDialog.vue'
import ProjectCard from './ProjectCard.vue'
import ProjectTreeNode from './ProjectTreeNode.vue'

const store = useDataStore()
const settingsStore = useSettingsStore()

// Show archived projects setting
const showArchivedProjects = computed(() => settingsStore.isShowArchivedProjects)

// Named projects (have a user-assigned name), sorted by mtime desc (from store)
const namedProjects = computed(() =>
    store.getProjects.filter(p => p.name !== null && (showArchivedProjects.value || !p.archived))
)

// Unnamed projects organized as a directory tree
const treeRoots = computed(() => {
    const unnamed = store.getProjects.filter(p => p.name === null && (showArchivedProjects.value || !p.archived))
    return buildProjectTree(unnamed)
})

const emit = defineEmits(['select'])

// Ref for the edit dialog component
const editDialogRef = ref(null)
// Currently selected project for editing
const editingProject = ref(null)

function handleSelect(project) {
    emit('select', project)
}

function handleMenuSelect(event, project) {
    const item = event.detail?.item
    if (!item) return
    if (item.value === 'edit') {
        editingProject.value = project
        editDialogRef.value?.open()
    } else if (item.value === 'archive') {
        store.setProjectArchived(project.id, true)
    } else if (item.value === 'unarchive') {
        store.setProjectArchived(project.id, false)
    }
}

function handleTreeMenuSelect(event, project) {
    // Same logic but event comes from tree node, no stopPropagation needed
    handleMenuSelect(event, project)
}

function handleToggleShowArchived(event) {
    settingsStore.setShowArchivedProjects(event.target.checked)
}
</script>

<template>
    <div class="project-list">
        <wa-switch
            size="small"
            class="show-archived-toggle"
            :checked="showArchivedProjects"
            @change="handleToggleShowArchived"
        >
            Show archived projects
        </wa-switch>

        <!-- Section 1: Named projects (flat, by mtime) -->
        <template v-if="namedProjects.length">
            <div class="section-header">Named projects</div>
            <ProjectCard
                v-for="project in namedProjects"
                :key="project.id"
                :project="project"
                @select="handleSelect"
                @menu-select="handleMenuSelect"
            />
        </template>

        <!-- Section 2: Unnamed projects (tree) -->
        <template v-if="treeRoots.length">
            <div v-if="namedProjects.length" class="section-header">Other projects</div>
            <ProjectTreeNode
                v-for="root in treeRoots"
                :key="root.project ? root.project.id : root.segment"
                :node="root"
                @select="handleSelect"
                @menu-select="handleTreeMenuSelect"
            />
        </template>

        <div v-if="namedProjects.length === 0 && treeRoots.length === 0" class="empty-state">
            No projects found
        </div>
    </div>

    <ProjectEditDialog ref="editDialogRef" :project="editingProject" />
</template>

<style scoped>
.project-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.show-archived-toggle {
    align-self: flex-end;
    font-size: var(--wa-font-size-s);
}

.section-header {
    font-size: var(--wa-font-size-s);
    font-weight: 600;
    color: var(--wa-color-text-quiet);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: var(--wa-space-xs) 0;
}

.empty-state {
    text-align: center;
    padding: var(--wa-space-xl);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}
</style>
