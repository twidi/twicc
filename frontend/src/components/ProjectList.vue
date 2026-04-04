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
const hasArchivedProjects = computed(() => store.getProjects.some(p => p.archived))

// Naming hint: dismissed state persisted in localStorage
const NAMING_HINT_KEY = 'twicc-naming-hint-dismissed'
const namingHintDismissed = ref(localStorage.getItem(NAMING_HINT_KEY) === '1')

const showNamingHint = computed(() =>
    !namingHintDismissed.value && namedProjects.value.length === 0 && treeRoots.value.length > 0
)

function dismissNamingHint() {
    namingHintDismissed.value = true
    localStorage.setItem(NAMING_HINT_KEY, '1')
}

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
            v-if="hasArchivedProjects"
            size="small"
            class="show-archived-toggle"
            :checked="showArchivedProjects"
            @change="handleToggleShowArchived"
        >
            Show archived projects
        </wa-switch>

        <!-- Naming hint callout -->
        <wa-callout v-if="showNamingHint" variant="brand" class="naming-hint">
            <wa-icon slot="icon" name="lightbulb"></wa-icon>
            <div class="naming-hint-content">
                <span><strong>Tip:</strong> Name your projects to keep them at the top of the list. Named projects are always displayed first, making your most important projects easier to find. To name a project, click the <wa-icon name="ellipsis"></wa-icon> menu on any project below.</span>
                <wa-button class="naming-hint-close" appearance="plain" variant="brand" size="small" @click="dismissNamingHint" aria-label="Dismiss">
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
            </div>
        </wa-callout>

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

.naming-hint-content {
    display: flex;
    align-items: flex-start;
    gap: var(--wa-space-s);
}

.naming-hint-content span {
    flex: 1;
}

.naming-hint-close {
    flex-shrink: 0;
}
</style>
