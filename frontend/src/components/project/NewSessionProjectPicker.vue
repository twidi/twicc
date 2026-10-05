<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import { splitProjectsByPriority } from '../../utils/projectSort'
import { buildProjectTree, flattenProjectTree } from '../../utils/projectTree'
import ProjectBadge from './ProjectBadge.vue'
import WorktreePickerRows from './WorktreePickerRows.vue'
import WorktreeButton from './WorktreeButton.vue'
import WorktreeDialog from './WorktreeDialog.vue'
import ProjectEditDialog from './ProjectEditDialog.vue'

defineOptions({ inheritAttrs: false })
const emit = defineEmits(['select-project'])
const store = useDataStore()
const settingsStore = useSettingsStore()
const workspacesStore = useWorkspacesStore()
const route = useRoute()
const dropdownRef = ref(null)
const createProjectDialogRef = ref(null)
const worktreeDialogRef = ref(null)
const showArchivedProjects = computed(() => settingsStore.isShowArchivedProjects)
const activeWorkspaceId = computed(() => route.query.workspace || null)
const activeWorkspace = computed(() =>
    activeWorkspaceId.value ? workspacesStore.getWorkspaceById(activeWorkspaceId.value) : null
)
const workspaceVisibleProjectIds = computed(() =>
    activeWorkspaceId.value ? workspacesStore.getVisibleProjectIds(activeWorkspaceId.value) : []
)
const activeWsLabel = computed(() =>
    activeWorkspace.value ? `${activeWorkspace.value.name} projects` : null
)
const nonStaleProjects = computed(() =>
    store.getListableProjects.filter(p => (showArchivedProjects.value || !p.archived) && !p.stale)
)
const nonStaleNamedProjects = computed(() => nonStaleProjects.value.filter(p => p.name !== null))
const wsVisibleSet = computed(() =>
    activeWorkspaceId.value ? new Set(workspaceVisibleProjectIds.value) : null
)
const wsPriorityIds = computed(() => activeWorkspace.value ? activeWorkspace.value.projectIds : null)
const splitNamedProjects = computed(() =>
    splitProjectsByPriority(nonStaleNamedProjects.value, wsPriorityIds.value, wsVisibleSet.value)
)
const splitFlatTree = computed(() => {
    const unnamed = nonStaleProjects.value.filter(p => p.name === null)
    if (!wsPriorityIds.value) {
        return { prioritized: [], others: flattenProjectTree(buildProjectTree(unnamed)) }
    }
    const { prioritized, others } = splitProjectsByPriority(unnamed, wsPriorityIds.value, wsVisibleSet.value)
    return {
        prioritized: flattenProjectTree(buildProjectTree(prioritized)),
        others: flattenProjectTree(buildProjectTree(others)),
    }
})

// Each selector keeps its own ephemeral expansion state.
const expandedWorktrees = ref(new Set())
function isNewSessionWorktreesExpanded(id) {
    return expandedWorktrees.value.has(id)
}
function pickableWorktreesOf(id) {
    return store.getWorktreesOf(id).filter(p => (showArchivedProjects.value || !p.archived) && !p.stale)
}
function handleNewSessionSelect(event) {
    const value = event.detail?.item?.value
    if (!value) return
    if (value.startsWith('worktrees-toggle:')) {
        const id = value.slice('worktrees-toggle:'.length)
        if (expandedWorktrees.value.has(id)) expandedWorktrees.value.delete(id)
        else expandedWorktrees.value.add(id)
        event.preventDefault()
        return
    }
    if (value === '__new_project__') createProjectDialogRef.value?.open()
    else emit('select-project', value)
}
function handleProjectResolved(project) {
    emit('select-project', project.id)
}
function openWorktreeDialog(project) {
    if (dropdownRef.value) dropdownRef.value.open = false
    worktreeDialogRef.value?.open(project)
}
</script>

<template>
    <wa-dropdown v-bind="$attrs" ref="dropdownRef" @wa-select="handleNewSessionSelect">
        <slot name="trigger" />
        <wa-dropdown-item value="__new_project__">
            <wa-icon slot="icon" name="plus"></wa-icon>
            New project
        </wa-dropdown-item>

        <!-- Workspace projects first (when workspace active) -->
        <template v-if="splitNamedProjects.prioritized.length || splitFlatTree.prioritized.length">
            <wa-divider></wa-divider>
            <wa-dropdown-item v-if="activeWsLabel" disabled class="section-header-item"><wa-icon name="layer-group" auto-width :style="activeWorkspace?.color ? { color: activeWorkspace.color } : null"></wa-icon> {{ activeWsLabel }}</wa-dropdown-item>
        </template>
        <template v-for="p in splitNamedProjects.prioritized" :key="p.id">
            <wa-dropdown-item :value="p.id" class="project-picker-row">
                <ProjectBadge :project-id="p.id" />
                <WorktreeButton v-if="!p.worktree_of" :project-id="p.id" slot="details" @create="openWorktreeDialog(p)" />
            </wa-dropdown-item>
            <WorktreePickerRows
                :parent-id="p.id"
                :worktrees="pickableWorktreesOf(p.id)"
                :expanded="isNewSessionWorktreesExpanded(p.id)"
                :base-depth="0"
            />
        </template>
        <template v-for="item in splitFlatTree.prioritized" :key="'wsp-' + item.key">
            <wa-dropdown-item
                v-if="item.isFolder"
                disabled
                class="tree-folder-dropdown-item"
            >
                <span class="tree-folder-label" :title="item.path" :style="{ paddingLeft: `${item.depth * 12}px` }">
                    {{ item.segment }}
                </span>
            </wa-dropdown-item>
            <template v-else>
                <wa-dropdown-item :value="item.project.id" class="project-picker-row">
                    <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                        <ProjectBadge :project-id="item.project.id" />
                    </span>
                    <WorktreeButton v-if="!item.project.worktree_of" :project-id="item.project.id" slot="details" @create="openWorktreeDialog(item.project)" />
                </wa-dropdown-item>
                <WorktreePickerRows
                    :parent-id="item.project.id"
                    :worktrees="pickableWorktreesOf(item.project.id)"
                    :expanded="isNewSessionWorktreesExpanded(item.project.id)"
                    :base-depth="item.depth"
                />
            </template>
        </template>

        <!-- Other projects -->
        <template v-if="activeWsLabel && (splitNamedProjects.others.length || splitFlatTree.others.length)">
            <wa-divider></wa-divider>
            <wa-dropdown-item disabled class="section-header-item">Other projects</wa-dropdown-item>
        </template>
        <wa-divider v-else-if="splitNamedProjects.others.length"></wa-divider>
        <template v-for="p in splitNamedProjects.others" :key="p.id">
            <wa-dropdown-item :value="p.id" class="project-picker-row">
                <ProjectBadge :project-id="p.id" />
                <WorktreeButton v-if="!p.worktree_of" :project-id="p.id" slot="details" @create="openWorktreeDialog(p)" />
            </wa-dropdown-item>
            <WorktreePickerRows
                :parent-id="p.id"
                :worktrees="pickableWorktreesOf(p.id)"
                :expanded="isNewSessionWorktreesExpanded(p.id)"
                :base-depth="0"
            />
        </template>

        <!-- Other unnamed projects (flattened tree) -->
        <wa-divider v-if="splitFlatTree.others.length"></wa-divider>
        <template v-for="item in splitFlatTree.others" :key="item.key">
            <wa-dropdown-item
                v-if="item.isFolder"
                disabled
                class="tree-folder-dropdown-item"
            >
                <span class="tree-folder-label" :title="item.path" :style="{ paddingLeft: `${item.depth * 12}px` }">
                    {{ item.segment }}
                </span>
            </wa-dropdown-item>
            <template v-else>
                <wa-dropdown-item :value="item.project.id" class="project-picker-row">
                    <span :style="{ paddingLeft: `${item.depth * 12}px` }">
                        <ProjectBadge :project-id="item.project.id" />
                    </span>
                    <WorktreeButton v-if="!item.project.worktree_of" :project-id="item.project.id" slot="details" @create="openWorktreeDialog(item.project)" />
                </wa-dropdown-item>
                <WorktreePickerRows
                    :parent-id="item.project.id"
                    :worktrees="pickableWorktreesOf(item.project.id)"
                    :expanded="isNewSessionWorktreesExpanded(item.project.id)"
                    :base-depth="item.depth"
                />
            </template>
        </template>
    </wa-dropdown>
    <!-- Keep dialog controls outside a hosting wa-button-group's default slot. -->
    <Teleport to="body">
        <ProjectEditDialog ref="createProjectDialogRef" @saved="handleProjectResolved" />
        <WorktreeDialog ref="worktreeDialogRef" @resolved="handleProjectResolved" />
    </Teleport>
</template>

<style scoped>
/* Reserve space for the worktree control on every project row. */
.project-picker-row {
    padding-inline-end: calc(2.25rem + 0.5em + var(--wa-space-2xs));
}
wa-dropdown::part(menu) {
    --auto-size-available-height: 50dvh;
}
.tree-folder-dropdown-item {
    opacity: 1;
    cursor: default;
}

.section-header-item {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    opacity: 1;
    cursor: default;
    color: var(--wa-color-text-quiet);
    wa-icon {
        font-size: var(--wa-font-size-s);
        color: var(--wa-color-text-normal);
        margin-inline: 0.2em;
    }
}

.tree-folder-label {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
    /* Intermediate directory nodes are not projects — italicize them so they
       read as grouping folders, distinct from the unnamed-project leaves below. */
    font-style: italic;
    /* Folder rows live inside a disabled <wa-dropdown-item>, whose host sets
       `pointer-events: none` — which would suppress the native `title` tooltip
       showing the full path. Re-enable pointer events on the label itself so the
       tooltip works on hover (the row stays non-selectable: it carries no value). */
    pointer-events: auto;
}
</style>
