<script setup>
// ProjectDetailNavList.vue - Horizontal navigation list of workspaces and/or projects.
//
// Quick navigation displayed in header panels:
// - "All Projects" mode: workspaces (getSelectableWorkspaces order) + all projects (named first, then unnamed, mtime desc)
// - Workspace mode: ↑ All Projects + projects in the workspace's custom order
// - Single project mode: ↑ All Projects + ↑ workspaces the project belongs to + ↑ the main repository this project is a worktree of (if any) + this project's own worktrees (if any, shown like workspace members), store order, respecting archived setting
//
// Each item shows an icon (layer-group for workspaces, colored dot for projects),
// the name, and aggregated indicators (CodeCommentsIndicator + AggregatedProcessIndicator).

import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useDataStore, ALL_PROJECTS_ID } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../utils/workspaceIds'
import ProjectBadge from './ProjectBadge.vue'
import AggregatedProcessIndicator from '../ui/AggregatedProcessIndicator.vue'
import CodeCommentsIndicator from '../ui/CodeCommentsIndicator.vue'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
})

const dataStore = useDataStore()
const settingsStore = useSettingsStore()
const workspacesStore = useWorkspacesStore()

const isAllProjectsMode = computed(() => props.projectId === ALL_PROJECTS_ID)
const isWorkspaceMode = computed(() => isWorkspaceProjectId(props.projectId))
const workspaceId = computed(() => isWorkspaceMode.value ? extractWorkspaceId(props.projectId) : null)

const items = computed(() => {
    const result = []

    if (isAllProjectsMode.value) {
        // 1. Workspaces in getSelectableWorkspaces order
        for (const ws of workspacesStore.getSelectableWorkspaces) {
            result.push({
                type: 'workspace',
                id: ws.id,
                name: ws.name,
                color: ws.color,
                projectIds: workspacesStore.getVisibleProjectIds(ws.id),
                to: { name: 'projects-all', query: { workspace: ws.id } },
            })
        }

        // Divider between workspaces and projects (only if there are workspaces)
        if (result.length > 0) {
            result.push({ type: 'divider' })
        }

        // 2. All visible projects: named first, then unnamed (both in mtime desc from store).
        //    Worktrees are excluded from the project links (getListableProjects).
        const showArchived = settingsStore.isShowArchivedProjects
        const projects = dataStore.getListableProjects.filter(p => showArchived || !p.archived)

        const namedProjects = projects.filter(p => p.name !== null)
        const unnamedProjects = projects.filter(p => p.name === null && p.directory)

        for (const p of namedProjects) {
            result.push({
                type: 'project',
                id: p.id,
                projectIds: [p.id],
                to: { name: 'project', params: { projectId: p.id } },
            })
        }
        if (namedProjects.length > 0 && unnamedProjects.length > 0) {
            result.push({ type: 'divider' })
        }
        for (const p of unnamedProjects) {
            result.push({
                type: 'project',
                id: p.id,
                projectIds: [p.id],
                to: { name: 'project', params: { projectId: p.id } },
            })
        }
    } else if (isWorkspaceMode.value) {
        // Go-up item: All Projects
        result.push({
            type: 'up',
            id: 'all-projects',
            label: 'All Projects',
            to: { name: 'projects-all' },
        })

        // Projects in workspace's custom order (worktree links excluded)
        const projectIds = workspacesStore.getVisibleProjectIds(workspaceId.value)
            .filter(pid => !dataStore.getProject(pid)?.worktree_of)
        if (projectIds.length) {
            result.push({ type: 'divider' })
        }
        for (const pid of projectIds) {
            const p = dataStore.getProject(pid)
            if (!p) continue
            result.push({
                type: 'project',
                id: p.id,
                projectIds: [p.id],
                to: { name: 'project', params: { projectId: p.id } },
            })
        }
    } else {
        // Go-up item: All Projects
        result.push({
            type: 'up',
            id: 'all-projects',
            label: 'All Projects',
            to: { name: 'projects-all' },
        })

        // "Go up" group: the workspaces this project belongs to, then — if this
        // project is a worktree — the main repository it was checked out from.
        const upItems = []

        // Workspaces containing this project (go up one level)
        const showArchivedWs = settingsStore.isShowArchivedWorkspaces
        for (const ws of workspacesStore.workspaces) {
            if (!showArchivedWs && ws.archived) continue
            if (!ws.projectIds.includes(props.projectId)) continue
            upItems.push({
                type: 'workspace',
                isUp: true,
                id: ws.id,
                name: ws.name,
                color: ws.color,
                projectIds: workspacesStore.getVisibleProjectIds(ws.id),
                to: { name: 'projects-all', query: { workspace: ws.id } },
            })
        }

        // Worktree → main repository link (go up to the repo this worktree was
        // checked out from). Comes after the workspaces.
        const parentId = dataStore.getProject(props.projectId)?.worktree_of
        if (parentId && dataStore.getProject(parentId)) {
            upItems.push({
                type: 'project',
                isUp: true,
                id: parentId,
                projectIds: [parentId],
                to: { name: 'project', params: { projectId: parentId } },
            })
        }

        if (upItems.length) {
            result.push({ type: 'divider' })
            result.push(...upItems)
        }

        // This project's own worktrees (when it is a main repository), shown on
        // their own line, prefixed by a "Worktrees" label (code-branch icon).
        const showArchivedProjects = settingsStore.isShowArchivedProjects
        const worktrees = dataStore.getWorktreesOf(props.projectId)
            .filter(p => showArchivedProjects || !p.archived)
        if (worktrees.length) {
            // Full-width break forces the worktrees onto a fresh line.
            result.push({ type: 'break' })
            result.push({ type: 'worktrees-label' })
            for (const wt of worktrees) {
                result.push({
                    type: 'project',
                    id: wt.id,
                    // Listed under their main repository: just their own folder.
                    hideParent: true,
                    projectIds: [wt.id],
                    to: { name: 'project', params: { projectId: wt.id } },
                })
            }
        }
    }

    return result
})

</script>

<template>
    <div v-if="items.length" class="project-nav-list">
        <template v-for="(item, index) in items" :key="item.id ? `${item.type}-${item.id}` : `${item.type}-${index}`">
            <span v-if="item.type === 'divider'" class="nav-divider"></span>
            <span v-else-if="item.type === 'break'" class="nav-break"></span>
            <span v-else-if="item.type === 'worktrees-label'" class="nav-section-label">
                <wa-icon name="code-branch" auto-width class="nav-item-icon"></wa-icon>
                <span class="nav-item-name">Worktrees</span>
            </span>
            <RouterLink
                v-else
                :to="item.to"
                class="nav-item"
            >
            <template v-if="item.type === 'up'">
                <wa-icon name="arrow-up" auto-width class="nav-up-icon"></wa-icon>
                <span class="nav-item-name">{{ item.label }}</span>
            </template>
            <template v-else-if="item.type === 'workspace'">
                <wa-icon v-if="item.isUp" name="arrow-up" auto-width class="nav-up-icon"></wa-icon>
                <wa-icon
                    name="layer-group"
                    auto-width
                    class="nav-item-icon"
                    :style="item.color ? { color: item.color } : null"
                ></wa-icon>
                <span class="nav-item-name">{{ item.name }}</span>
            </template>
            <template v-else>
                <wa-icon v-if="item.isUp" name="arrow-up" auto-width class="nav-up-icon"></wa-icon>
                <ProjectBadge :project-id="item.id" :hide-parent="!!item.hideParent" use-directory-for-unnamed gap="var(--wa-space-2xs)" />
            </template>

            <template v-if="item.projectIds">
                <CodeCommentsIndicator :project-ids="item.projectIds" :show-tooltip="false" />
                <AggregatedProcessIndicator :project-ids="item.projectIds" size="small" />
            </template>
            </RouterLink>
        </template>
    </div>
</template>

<style scoped>
.project-nav-list {
    display: flex;
    flex-wrap: wrap;
    column-gap: var(--wa-space-l);
    row-gap: var(--wa-space-2xs);
}

.nav-item {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    text-decoration: none;
    transition: background-color 0.15s, color 0.15s;
}

.nav-item:hover {
    background: var(--wa-color-surface-alt);
    color: var(--wa-color-text-normal);
}

.nav-divider {
    width: 1px;
    align-self: stretch;
    background: var(--wa-color-surface-border);
}

/* Zero-height, full-width item: forces the following items onto a new line. */
.nav-break {
    flex-basis: 100%;
    height: 0;
}

.nav-section-label {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}

.nav-up-icon {
    font-size: var(--wa-font-size-xs);
}

.nav-item-icon {
    font-size: var(--wa-font-size-s);
}

.nav-item-name {
    white-space: nowrap;
}
</style>
