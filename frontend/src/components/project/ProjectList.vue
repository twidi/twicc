<script setup>
import { ref, computed } from 'vue'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { buildProjectTree } from '../../utils/projectTree'
import ProjectEditDialog from './ProjectEditDialog.vue'
import ProjectCard from './ProjectCard.vue'
import ProjectTreeNode from './ProjectTreeNode.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import HelpIconButton from '../help/HelpIconButton.vue'

const store = useDataStore()
const settingsStore = useSettingsStore()

// Show archived projects setting
const showArchivedProjects = computed(() => settingsStore.isShowArchivedProjects)
const hasArchivedProjects = computed(() => store.getListableProjects.some(p => p.archived))

// Named projects (have a user-assigned name), sorted by effective activity (from store)
const namedProjects = computed(() =>
    store.getListableProjects.filter(p => p.name !== null && (showArchivedProjects.value || !p.archived))
)

// Unnamed projects organized as a directory tree
const treeRoots = computed(() => {
    const unnamed = store.getListableProjects.filter(p => p.name === null && (showArchivedProjects.value || !p.archived))
    return buildProjectTree(unnamed)
})

const emit = defineEmits(['select', 'create'])

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
    <div>
        <div class="section-header">
            <span class="section-label">Projects <HelpIconButton help-key="projects" label="What's a project?" /></span>
            <div class="header-actions">
                <wa-switch
                    v-if="hasArchivedProjects"
                    size="small"
                    class="show-archived-toggle"
                    :checked="showArchivedProjects"
                    @change="handleToggleShowArchived"
                >
                    Show archived
                </wa-switch>
                <wa-button
                    id="project-list-create-btn"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    @click="emit('create')"
                >
                    <wa-icon name="plus"></wa-icon>
                </wa-button>
                <AppTooltip for="project-list-create-btn">New project</AppTooltip>
            </div>
        </div>

        <!-- Section 1: Named projects (flat, by effective activity) -->
        <div class="section-subheader">Named projects</div>
        <div v-if="namedProjects.length" class="project-cards">
            <ProjectCard
                v-for="project in namedProjects"
                :key="project.id"
                :project="project"
                @select="handleSelect"
                @menu-select="handleMenuSelect"
            />
        </div>
        <wa-callout v-else variant="brand" class="naming-hint">
            <wa-icon slot="icon" name="lightbulb"></wa-icon>
            Name your projects to keep them at the top of the list. Named projects are always displayed first, making your most important projects easier to find. To name a project, click the <wa-icon name="ellipsis-v" auto-width></wa-icon> menu on any project below.
        </wa-callout>

        <!-- Section 2: Unnamed projects (tree) -->
        <template v-if="treeRoots.length">
            <div class="section-subheader">Other projects</div>
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
.section-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    column-gap: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    font-weight: 600;
    color: var(--wa-color-text-quiet);
    padding: var(--wa-space-xs) 0;
}

.section-label {
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-text-normal);
}

.show-archived-toggle {
    font-size: var(--wa-font-size-s);
    text-transform: none;
    letter-spacing: normal;
    font-weight: normal;
    margin-right: var(--wa-space-s);
}

.header-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    margin-left: auto;
}

.project-cards {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.section-subheader {
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    color: var(--wa-color-text-quiet);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: var(--wa-space-l) 0 var(--wa-space-s);
}

.section-header + .section-subheader {
    padding-top: 0;
}

.empty-state {
    text-align: center;
    padding: var(--wa-space-xl);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}
</style>
