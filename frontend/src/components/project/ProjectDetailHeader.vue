<script setup>
// ProjectDetailHeader.vue - Header section of the project detail panel.
// Shows project/workspace name, sparkline, process indicator, edit button,
// directory path, meta info (sessions count, cost, last activity),
// and manages the edit/manage dialogs.
//
// On small viewports (max-height: 900px), collapses to a single compact row
// with a chevron to expand the full details as an overlay — same pattern as
// SessionHeader.vue.

import { ref, computed } from 'vue'
import { onClickOutside } from '@vueuse/core'
import { useDataStore, ALL_PROJECTS_ID } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../utils/workspaceIds'
import { aggregateWeeklyActivity } from '../../utils/activityAggregation'
import { formatDate } from '../../utils/date'
import { SESSION_TIME_FORMAT } from '../../constants'
import ProjectBadge from './ProjectBadge.vue'
import ProjectDirectoryPath from './ProjectDirectoryPath.vue'
import ProjectMissingDirectoryIcon from './ProjectMissingDirectoryIcon.vue'
import ProjectMissingDirectoryNote from './ProjectMissingDirectoryNote.vue'
import AggregatedProcessIndicator from '../ui/AggregatedProcessIndicator.vue'
import CodeCommentsIndicator from '../ui/CodeCommentsIndicator.vue'
import ProjectEditDialog from './ProjectEditDialog.vue'
import WorkspaceManageDialog from '../workspace/WorkspaceManageDialog.vue'
import ActivitySparkline from '../activity/ActivitySparkline.vue'
import CostDisplay from '../ui/CostDisplay.vue'
import AppTooltip from '../ui/AppTooltip.vue'
import ProjectDetailNavList from './ProjectDetailNavList.vue'

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
// Stats (sparkline) include archived projects so the workspace history
// reflects the full activity, not just what is currently visible.
const workspaceStatsProjectIds = computed(() =>
    workspaceId.value ? workspacesStore.getAllProjectIds(workspaceId.value) : []
)
// Single project data
const project = computed(() => isSingleProjectMode.value ? store.getProject(props.projectId) : null)

// Whether the thing shown in the header (the single project or the active
// workspace) is currently archived — drives the clickable "Archived" badge.
const isArchived = computed(() => {
    if (isSingleProjectMode.value) return !!project.value?.archived
    if (isWorkspaceMode.value) return !!workspace.value?.archived
    return false
})

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
// Compact mode collapses the directory row away, so the warning is re-hung next
// to the badge — the wrapper needs the same condition as the icon itself,
// otherwise an empty indicator would still claim its slot in the row's gap.
const directoryMissing = computed(() => !!project.value?.stale)

// Project IDs aggregated for this page's stats (counter, cost, last activity,
// sparkline). No archived filter — these reflect the whole, including archived
// projects and every worktree: a single project aggregates itself plus its
// worktrees, a workspace all its members plus their worktrees.
const statsProjectIds = computed(() => {
    if (isAllProjectsMode.value) return null
    if (isWorkspaceMode.value) return workspaceStatsProjectIds.value
    return store.getProjectScopeIds(props.projectId)
})
const statsProjects = computed(() => {
    if (isAllProjectsMode.value) return allProjects.value
    return (statsProjectIds.value || []).map(pid => store.getProject(pid)).filter(Boolean)
})

// Sessions count
const sessionsCount = computed(() =>
    statsProjects.value.reduce((sum, p) => sum + (p.sessions_count || 0), 0)
)

// Total cost
const totalCost = computed(() => {
    const sum = statsProjects.value.reduce((s, p) => s + (p.total_cost || 0), 0)
    return sum > 0 ? sum : null
})

// Last activity (mtime)
const mtime = computed(() => {
    if (statsProjects.value.length === 0) return null
    return Math.max(...statsProjects.value.map(p => p.mtime || 0))
})

// Weekly activity data — aggregated across the page's stats scope.
const weeklyActivity = computed(() => {
    if (isAllProjectsMode.value) return store.weeklyActivity._global || []
    return aggregateWeeklyActivity(statsProjectIds.value || [], store.weeklyActivity)
})

// Project IDs for the live process / unread indicators. Unlike the stats above,
// these track the *visible* scope (matching the session list): a workspace's
// visible members + their worktrees, or a single project (whose own visible
// worktrees AggregatedProcessIndicator expands in, archived-aware).
const indicatorProjectIds = computed(() => {
    if (isAllProjectsMode.value) return []
    if (isWorkspaceMode.value) return workspaceProjectIds.value
    return [props.projectId]
})

// Compact mode state
const isCompactExpanded = ref(false)
const headerRef = ref(null)

// The expanded overlay behaves like a popup: a click anywhere else closes it.
// The overlay is a child of the header, so one target is enough.
onClickOutside(headerRef, () => {
    isCompactExpanded.value = false
})

defineExpose({ isCompactExpanded })

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

// Archive the project / workspace from the header action button.
function handleArchive() {
    if (isSingleProjectMode.value && project.value && !project.value.archived) {
        store.setProjectArchived(props.projectId, true)
    } else if (isWorkspaceMode.value && workspace.value && !workspace.value.archived) {
        workspacesStore.updateWorkspace(workspaceId.value, { archived: true })
    }
}

// Click on the "Archived" badge → unarchive the project / workspace in place
// (mirrors the clickable archived badge on a session header).
function handleUnarchive() {
    if (isSingleProjectMode.value && project.value?.archived) {
        store.setProjectArchived(props.projectId, false)
    } else if (isWorkspaceMode.value && workspace.value?.archived) {
        workspacesStore.updateWorkspace(workspaceId.value, { archived: false })
    }
}
</script>

<template>
    <header ref="headerRef" class="detail-header" :class="{ 'compact-expanded': isCompactExpanded, 'compact-collapsed': !isCompactExpanded }">
        <!-- Title row -->
        <div class="detail-title-row">
            <!-- Clickable zone for compact toggle -->
            <div class="compact-toggle-zone" @click="isCompactExpanded = !isCompactExpanded">
                <!-- Action group before the title (mirrors the session header):
                     the clickable "Archived" badge (or the Archive button when
                     not archived) followed by the Edit/Manage button. -->
                <div v-if="!isAllProjectsMode" class="detail-title-actions">
                    <wa-tag
                        v-if="isArchived"
                        id="project-detail-archived-tag"
                        size="small"
                        variant="neutral"
                        class="archived-tag"
                        @click.stop="handleUnarchive"
                    >Archived</wa-tag>
                    <AppTooltip v-if="isArchived" for="project-detail-archived-tag">Click to unarchive</AppTooltip>

                    <wa-button
                        v-else
                        id="detail-archive-button"
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="archive-button reduced-height"
                        @click.stop="handleArchive"
                    >
                        <wa-icon name="box-archive" :label="isWorkspaceMode ? 'Archive workspace' : 'Archive project'"></wa-icon>
                    </wa-button>
                    <AppTooltip v-if="!isArchived" for="detail-archive-button">{{ isWorkspaceMode ? 'Archive workspace' : 'Archive project' }}</AppTooltip>

                    <wa-button
                        id="detail-edit-button"
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="edit-button reduced-height"
                        @click.stop="handleEditClick"
                    >
                        <wa-icon :name="isWorkspaceMode ? 'gear' : 'pencil'"></wa-icon>
                    </wa-button>
                    <AppTooltip v-if="isWorkspaceMode" for="detail-edit-button">Manage workspace</AppTooltip>
                    <AppTooltip v-else-if="isSingleProjectMode" for="detail-edit-button">Edit project (name and color)</AppTooltip>
                </div>

                <!-- Single project mode -->
                <template v-if="isSingleProjectMode">
                    <ProjectBadge :project-id="projectId" parent-link class="detail-title" />
                </template>
                <!-- Workspace or All Projects mode -->
                <template v-else>
                    <h2 class="detail-title all-projects-title">
                        <wa-icon v-if="isWorkspaceMode" name="layer-group" auto-width :style="workspace?.color ? { color: workspace.color } : null"></wa-icon>
                        {{ displayName }}
                    </h2>
                </template>

                <!-- Compact-only indicators (visible only in compact collapsed mode).
                     The missing-directory mark comes first: in compact mode the
                     directory row is collapsed away, so the warning would
                     otherwise disappear exactly where the badge still shows. -->
                <span v-if="directoryMissing" class="compact-indicator compact-missing-directory">
                    <ProjectMissingDirectoryIcon :project-id="projectId" />
                </span>
                <span v-if="!isAllProjectsMode" class="compact-indicator">
                    <CodeCommentsIndicator :project-ids="indicatorProjectIds" />
                </span>
                <span v-if="!isAllProjectsMode" class="compact-indicator">
                    <AggregatedProcessIndicator :project-ids="indicatorProjectIds" size="small" />
                </span>

                <!-- Compact chevron -->
                <wa-icon
                    class="compact-toggle-chevron"
                    :name="isCompactExpanded ? 'chevron-up' : 'chevron-down'"
                    label="Toggle details"
                ></wa-icon>
            </div>
        </div>

        <!-- Collapsible rows: sparkline, directory, meta (overlay on small viewports) -->
        <div class="detail-collapsible-rows">
            <!-- Sparkline -->
            <div class="detail-sparkline-row">
                <span :id="`detail-sparkline-${projectId}`" class="detail-sparkline">
                    <ActivitySparkline
                        :id-suffix="`${projectId}-detail`"
                        :data="weeklyActivity"
                    />
                </span>
                <AppTooltip :for="`detail-sparkline-${projectId}`">{{ isWorkspaceMode ? 'Workspace' : isAllProjectsMode ? 'Overall' : 'Project' }} activity (message turns per week)</AppTooltip>

                <!-- Full-size indicators (hidden in compact mode, shown on large viewports) -->
                <CodeCommentsIndicator v-if="!isAllProjectsMode" :project-ids="indicatorProjectIds" class="full-indicator" />
                <AggregatedProcessIndicator v-if="!isAllProjectsMode" :project-ids="indicatorProjectIds" size="small" class="full-indicator" />
            </div>

            <!-- Directory (single project only) -->
            <div v-if="isSingleProjectMode && directory" class="detail-directory">
                <wa-icon name="folder" class="detail-icon"></wa-icon>
                <ProjectDirectoryPath :project-id="projectId" />
                <!-- flex-basis: 100% puts it on its own line without disturbing the
                     icon/path alignment of the single-line case. -->
                <ProjectMissingDirectoryNote :project-id="projectId" class="detail-directory-note" />
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

            <!-- Navigation list of workspaces/projects (aggregate modes only) -->
            <ProjectDetailNavList :project-id="projectId" class="detail-nav-list" />
        </div>

        <!-- Edit dialog (single project only) -->
        <ProjectEditDialog v-if="isSingleProjectMode" ref="editDialogRef" :project="project" />
        <!-- Workspace manage dialog -->
        <WorkspaceManageDialog v-if="isWorkspaceMode" ref="manageDialogRef" />
    </header>
</template>

<style scoped>
.detail-header {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    padding-inline: var(--wa-space-m);
    position: relative;
}

.detail-title-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    min-width: 0;
}

.detail-title {
    font-weight: 600;
    min-width: 0;
}

.all-projects-title {
    margin: 0;
    color: var(--wa-color-text-normal);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: var(--wa-font-size-l);
}

/* Action group placed before the title (mirrors the session header's
   .session-title-actions): archived badge / archive button + edit button. */
.detail-title-actions {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    flex-shrink: 0;
}
.edit-button,
.archive-button {
    flex-shrink: 0;
}

/* Compact toggle zone: transparent on large viewports */
.compact-toggle-zone {
    display: contents;
}

/* Clickable "Archived" badge shown before the title when the project /
   workspace is archived (mirrors the session header's archived tag). */
.archived-tag {
    flex-shrink: 0;
    cursor: pointer;
}

/* Compact chevron: hidden by default */
.compact-toggle-chevron {
    display: none;
    flex-shrink: 0;
    opacity: 0.6;
    transition: opacity 0.15s;
    font-size: var(--wa-font-size-xs);
    align-self: center;
}

/* Compact-only indicators: hidden by default (shown only in compact collapsed mode) */
.compact-indicator {
    display: none;
}

/* Keeps the missing-directory mark on the badge's centre line. */
.compact-missing-directory {
    align-items: center;
}

.detail-sparkline-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
}

/* Collapsible rows: transparent wrapper on large viewports */
.detail-collapsible-rows {
    display: contents;
}

.detail-directory {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-3xs) var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    word-break: break-all;
}

.detail-directory-note {
    flex-basis: 100%;
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
}

.detail-meta:not(:has(+ .detail-nav-list)) {
    padding-bottom: var(--wa-space-s);
}
.detail-nav-list {
    padding-bottom: var(--wa-space-s);
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

@media (max-height: 900px) {
    /* Show chevron */
    .compact-toggle-chevron {
        display: inline-flex;
    }

    /* Make toggle zone a clickable flex row */
    .compact-toggle-zone {
        display: flex;
        align-items: center;
        gap: var(--wa-space-s);
        min-width: 0;
        cursor: pointer;
        flex: 1;
    }

    .compact-toggle-zone:hover .compact-toggle-chevron {
        opacity: 1;
    }

    /* In compact collapsed mode: show compact indicators, hide full indicators */
    .detail-header.compact-collapsed .compact-indicator {
        display: inline-flex;
    }

    .detail-header.compact-collapsed {
        border-bottom: solid var(--wa-color-surface-border) var(--divider-size);
        gap: 0;
        padding-block: 0;
        padding-inline: var(--wa-space-xs);
    }

    /* In compact collapsed mode: hide the action buttons (archive/edit), like
       the session header — they reappear when the header is expanded. */
    .detail-header.compact-collapsed .detail-title-actions {
        display: none;
    }

    /* Hide full indicators in compact mode (they live inside the collapsible rows) */
    .detail-header.compact-collapsed .full-indicator {
        display: none;
    }

    /* Collapsible rows become an overlay panel */
    .detail-collapsible-rows {
        display: flex;
        flex-direction: column;
        gap: var(--wa-space-m);
        position: absolute;
        top: 100%;
        left: 0;
        right: 0;
        z-index: 20;
        background: var(--wa-color-surface-default);
        box-shadow: var(--wa-shadow-s);
        border-bottom: solid var(--wa-color-surface-border) var(--divider-size);

        /* Hidden by default */
        opacity: 0;
        visibility: hidden;
        transform: translateY(-8px);
        transition: opacity 0.2s ease, transform 0.2s ease, visibility 0.2s;
    }

    /* When expanded: reveal the overlay */
    .detail-header.compact-expanded .detail-collapsible-rows {
        opacity: 1;
        visibility: visible;
        transform: translateY(0);
    }

    /* When expanded: hide compact indicators, show full indicators */
    .detail-header.compact-expanded .compact-indicator {
        display: none;
    }

    .detail-header.compact-expanded .full-indicator {
        display: inline-flex;
    }

    .detail-title-row {
        padding-block: var(--wa-space-xs);
    }

    .detail-meta, .detail-nav-list {
        padding-bottom: 0 !important;
    }
    .detail-sparkline-row {
        padding-top: var(--wa-space-s);
    }

    .detail-meta, .detail-sparkline-row, .detail-directory, .detail-nav-list {
        padding-inline: var(--wa-space-m);
    }
}
</style>
