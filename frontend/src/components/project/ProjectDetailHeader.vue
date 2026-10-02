<script setup>
// ProjectDetailHeader.vue - Header section of the project detail panel.
// Shows the project/workspace name with its status icons (code comments, live process), then the
// zones below: identity (directory path), the action buttons (archive, edit/manage), a stats container
// (sessions count, cost, last activity over the activity sparkline drawn as a backdrop) and the
// navigation list; manages the edit/manage dialogs.
//
// On small viewports (compact height, utils/compactHeight.js), collapses to a single compact row
// with a tools button to expand the zones below as an overlay — same pattern as SessionHeader.vue.

import { ref, computed } from 'vue'
import { onClickOutside } from '@vueuse/core'
import { useDataStore, ALL_PROJECTS_ID } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { useWorkspacesStore } from '../../stores/workspaces'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../utils/workspaceIds'
import { aggregateWeeklyActivity } from '../../utils/activityAggregation'
import { formatDate } from '../../utils/date'
import { compactHeight, rootFontSizePx } from '../../utils/compactHeight'
import { useActionsRowLabels } from '../../composables/useActionsRowLabels'
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
// workspace) is currently archived — drives the archived marker and the Archive / Unarchive button.
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
// The compact closed header hides the zone where the missing-directory warning lives, so the warning is
// re-hung next to the name — the wrapper needs the same condition as the icon itself, otherwise an
// empty indicator would still claim its slot in the row's gap.
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

// The action buttons spell out their names when their row fits on one line (see useActionsRowLabels).
const actionsRef = ref(null)
const actionsLabels = useActionsRowLabels(headerRef, actionsRef, () => [
    props.projectId, isArchived.value, compactHeight.value, rootFontSizePx.value,
])

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

// Click on the Unarchive button → unarchive the project / workspace in place.
function handleUnarchive() {
    if (isSingleProjectMode.value && project.value?.archived) {
        store.setProjectArchived(props.projectId, false)
    } else if (isWorkspaceMode.value && workspace.value?.archived) {
        workspacesStore.updateWorkspace(workspaceId.value, { archived: false })
    }
}
</script>

<template>
    <header ref="headerRef" class="detail-header" :class="{ 'compact-expanded': isCompactExpanded, 'compact-collapsed': !isCompactExpanded, 'actions-labels': actionsLabels }">
        <!-- Title row -->
        <div class="detail-title-row">
            <!-- Clickable zone for compact toggle -->
            <div class="compact-toggle-zone" @click="isCompactExpanded = !isCompactExpanded">
                <!-- Archived marker: the session header's archive icon, in its yellow. The Unarchive button,
                     with the other actions, is the way out. -->
                <wa-icon
                    v-if="isArchived"
                    id="project-detail-archived-icon"
                    name="box-archive"
                    :label="isWorkspaceMode ? 'Archived workspace' : 'Archived project'"
                    class="session-state-icon session-state-icon--archived"
                ></wa-icon>
                <AppTooltip v-if="isArchived" for="project-detail-archived-icon">{{ isWorkspaceMode ? 'Archived workspace' : 'Archived project' }}</AppTooltip>

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

                <!-- Missing directory (compact closed header only: the warning is in the opened panel and in
                     the full header, with the path). It comes first: the directory row is collapsed away, so
                     the warning would otherwise disappear exactly where the name still shows. -->
                <span v-if="directoryMissing" class="compact-missing-directory">
                    <ProjectMissingDirectoryIcon :project-id="projectId" />
                </span>

                <!-- Status: unsent code comments, then the live process indicator, right after the name in
                     every mode. The wrappers have no box of their own, so an indicator that renders nothing
                     claims no gap. -->
                <span v-if="!isAllProjectsMode" class="title-indicator">
                    <CodeCommentsIndicator :project-ids="indicatorProjectIds" />
                </span>
                <span v-if="!isAllProjectsMode" class="title-indicator">
                    <AggregatedProcessIndicator :project-ids="indicatorProjectIds" size="small" />
                </span>

                <!-- Panel toggle (compact height only): the same one-segment pill as the session header's. -->
                <div class="compact-pill">
                    <wa-button
                        id="detail-compact-toggle"
                        variant="brand"
                        appearance="plain"
                        size="small"
                        :class="['compact-tool-button', 'reduced-height', { 'compact-tool-button--active': isCompactExpanded }]"
                        @click.stop="isCompactExpanded = !isCompactExpanded"
                    >
                        <wa-icon name="screwdriver-wrench" label="Toggle details"></wa-icon>
                        <wa-icon class="compact-tool-chevron" :name="isCompactExpanded ? 'chevron-up' : 'chevron-down'"></wa-icon>
                    </wa-button>
                </div>
            </div>
        </div>

        <!-- Collapsible rows: sparkline, directory, meta (overlay on small viewports) -->
        <div class="detail-collapsible-rows" :class="{ 'glass-surface': compactHeight }">
            <!-- Identity: directory (single project only; cut from the left so the last folder stays
                 visible) -->
            <div v-if="isSingleProjectMode && directory" class="detail-identity">
                <div class="detail-directory">
                    <wa-icon name="folder" class="detail-icon"></wa-icon>
                    <ProjectDirectoryPath :project-id="projectId" emphasize-last class="detail-directory-path" />
                    <!-- flex-basis: 100% puts it on its own line without disturbing the
                         icon/path alignment of the single-line case. -->
                    <ProjectMissingDirectoryNote :project-id="projectId" class="detail-directory-note" />
                </div>
            </div>

            <!-- Action buttons (a project or a workspace): part of the compact panel, always visible at full
                 height. -->
            <div v-if="!isAllProjectsMode" ref="actionsRef" class="detail-actions">
                <wa-button
                    v-if="!isArchived"
                    id="detail-archive-button"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="archive-button reduced-height"
                    @click="handleArchive"
                >
                    <wa-icon auto-width name="box-archive" :label="isWorkspaceMode ? 'Archive workspace' : 'Archive project'"></wa-icon>
                    <span class="action-label">Archive</span>
                </wa-button>
                <AppTooltip v-if="!isArchived" for="detail-archive-button">{{ isWorkspaceMode ? 'Archive workspace' : 'Archive project' }}</AppTooltip>

                <wa-button
                    v-else
                    id="detail-unarchive-button"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="archive-button archive-button--archived reduced-height"
                    @click="handleUnarchive"
                >
                    <wa-icon auto-width name="box-archive" :label="isWorkspaceMode ? 'Unarchive workspace' : 'Unarchive project'"></wa-icon>
                    <span class="action-label">Unarchive</span>
                </wa-button>
                <AppTooltip v-if="isArchived" for="detail-unarchive-button">{{ isWorkspaceMode ? 'Unarchive workspace' : 'Unarchive project' }}</AppTooltip>

                <wa-button
                    id="detail-edit-button"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="edit-button reduced-height"
                    @click="handleEditClick"
                >
                    <wa-icon auto-width :name="isWorkspaceMode ? 'gear' : 'pencil'" :label="isWorkspaceMode ? 'Manage workspace' : 'Edit project'"></wa-icon>
                    <span class="action-label">{{ isWorkspaceMode ? 'Manage' : 'Edit' }}</span>
                </wa-button>
                <AppTooltip v-if="isWorkspaceMode" for="detail-edit-button">Manage workspace</AppTooltip>
                <AppTooltip v-else-if="isSingleProjectMode" for="detail-edit-button">Edit project (name and color)</AppTooltip>
            </div>

            <!-- Stats container: labelled cells over the activity sparkline, drawn as a soft backdrop. -->
            <div class="detail-stats">
                <span :id="`detail-sparkline-${projectId}`" class="detail-sparkline-backdrop">
                    <ActivitySparkline
                        reveal
                        stretch
                        :id-suffix="`${projectId}-detail`"
                        :data="weeklyActivity"
                    />
                </span>
                <AppTooltip :for="`detail-sparkline-${projectId}`">{{ isWorkspaceMode ? 'Workspace' : isAllProjectsMode ? 'Overall' : 'Project' }} activity (message turns per week)</AppTooltip>

                <div class="detail-cells">
                    <div class="detail-cell">
                        <span class="detail-cell-label">Sessions</span>
                        <div id="detail-sessions-count" class="detail-cell-value">
                            <wa-icon name="folder-open" class="detail-icon" variant="regular"></wa-icon>
                            <span>{{ sessionsCount }}</span>
                        </div>
                        <AppTooltip for="detail-sessions-count">Number of sessions</AppTooltip>
                    </div>

                    <div v-if="showCosts" class="detail-cell">
                        <span class="detail-cell-label">Cost</span>
                        <CostDisplay id="detail-cost" :cost="totalCost" class="detail-cell-value" />
                        <AppTooltip for="detail-cost">Total cost</AppTooltip>
                    </div>

                    <div v-if="mtime" class="detail-cell">
                        <span class="detail-cell-label">Last activity</span>
                        <div id="detail-mtime" class="detail-cell-value">
                            <wa-icon name="clock" class="detail-icon" variant="regular"></wa-icon>
                            <span>
                                <wa-relative-time v-if="useRelativeTime" :date.prop="timestampToDate(mtime)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                                <template v-else>{{ formatDate(mtime) }}</template>
                            </span>
                        </div>
                        <AppTooltip for="detail-mtime">{{ useRelativeTime ? `Last activity: ${formatDate(mtime)}` : 'Last activity' }}</AppTooltip>
                    </div>
                </div>
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
    /* The leading icon of the title row, the directory row and the action row is centred in a box of this
       width, starting at the rows' inline padding: one vertical axis, like the session header's. */
    --header-icon-col: 1.5rem;
    /* Geometry of the compact tools pill (see SessionHeader.vue): one button-wide share plus the chevron. */
    --compact-pill-button-width: 2.75rem;
    --compact-pill-chevron-width: 1rem;
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
    font-weight: 650;
    letter-spacing: -0.015em;
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

/* The status indicators after the name: no box of their own (an empty one claims no gap). */
.title-indicator {
    display: contents;
}

/* Compact toggle zone: transparent on large viewports */
.compact-toggle-zone {
    display: contents;
}

/* The missing-directory mark: only in the compact closed header, on the name's centre line. */
.compact-missing-directory {
    display: none;
    align-items: center;
}

/* Leading icon of the title row: the archived marker, the project's mark, or the workspace's icon. */
.compact-toggle-zone > wa-icon:first-child,
.all-projects-title > wa-icon:first-child {
    display: inline-flex;
    justify-content: center;
    width: var(--header-icon-col);
}
:deep(.detail-title > :first-child) {
    display: inline-flex;
    justify-content: center;
    width: var(--header-icon-col);
}

/* The compact tools pill: hidden by default, one outlined brand segment like the session header's. */
.compact-pill {
    display: none;
    flex-shrink: 0;
    align-items: stretch;
    overflow: hidden;
    margin-inline-start: auto;
    margin-block: calc(-3 * var(--wa-space-2xs));
    border-radius: var(--wa-form-control-border-radius, var(--wa-border-radius-m));
    border: 1px solid var(--wa-color-brand-border-loud);
    box-shadow: var(--depth-button), var(--depth-highlight);
}
.compact-tool-button {
    display: inline-flex;
    width: calc(var(--compact-pill-button-width) + var(--compact-pill-chevron-width));
    opacity: 0.6;
    transition: opacity 0.15s;
    &::part(base) {
        flex: 1;
        min-width: 0;
        padding-inline: 0;
        border-radius: 0;
    }
}
.compact-tool-button:hover,
.compact-tool-button.compact-tool-button--active {
    opacity: 1;
}
.compact-tool-chevron {
    display: inline-flex;
    justify-content: center;
    width: var(--compact-pill-chevron-width);
    font-size: var(--wa-font-size-2xs);
}

/* Collapsible rows: transparent wrapper on large viewports */
.detail-collapsible-rows {
    display: contents;
}

/* Zone 1 — identity: the directory. */
.detail-identity {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-3xs) var(--wa-space-m);
}

.detail-directory {
    flex: 1 1 14rem;
    min-width: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-3xs) var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.detail-directory-path {
    flex: 1 1 0;
    min-width: 0;
}

.detail-directory-note {
    flex-basis: 100%;
}

.detail-icon {
    flex-shrink: 0;
    color: var(--wa-color-brand-60);
    font-size: var(--wa-font-size-s);
}

/* The directory icon is chrome, not data: keep it quiet like the path. */
.detail-directory > .detail-icon {
    color: var(--wa-color-text-quiet);
}
/* The directory icon sits on the header's icon axis, in a box of the column's width. */
.detail-directory > .detail-icon:first-child {
    display: inline-flex;
    justify-content: center;
    width: var(--header-icon-col);
}

/* Action buttons: a row of their own between the identity and the stats, in the full header and in the
   compact panel. Same treatment as the session header's (see SessionHeader.vue): the 1.3 scale of
   .reduced-height is a real 1.3em, so a long name cannot run into the next icon; the pull-in that trims the
   buttons' tall boxes is on each button, so a wrapped line stays close; the first icon is on the axis. */
.detail-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-3xs) var(--wa-space-xs);
    margin-block: 0 calc(0.5 * var(--wa-space-2xs));
}
.detail-actions > wa-button {
    margin-block: calc(-1.5 * var(--wa-space-2xs));
}
.detail-actions wa-button {
    font-size: var(--wa-font-size-2xs);
}
.detail-actions wa-button::part(base) {
    padding-inline: var(--wa-space-xs);
}
.detail-actions wa-button::part(label) {
    scale: 1;
}
.detail-actions wa-button wa-icon,
.detail-actions .action-label {
    font-size: 1.3em;
}
.detail-actions > wa-button:first-child::part(base) {
    padding-inline-start: 0;
}
.detail-actions > wa-button:first-child wa-icon {
    width: var(--header-icon-col);
}
.edit-button,
.archive-button {
    flex-shrink: 0;
}
.archive-button.archive-button--archived::part(base) {
    color: var(--wa-color-yellow-80);
}
/* The button names show only when the row fits on one line (see useActionsRowLabels). */
.action-label {
    display: none;
    margin-inline-start: calc(0.75 * var(--wa-space-xs));
    white-space: nowrap;
}
.detail-header.actions-labels .action-label {
    display: inline;
}

/* Zone 2 — stats container: labelled cells over the sparkline backdrop. */
.detail-stats {
    position: relative;
    overflow: hidden;
    border: var(--divider-size) solid var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-l);
    background: color-mix(in oklab, var(--wa-color-brand-60) 8%, transparent);
    font-size: var(--wa-font-size-s);
}

/* The sparkline fills the container, drawn softly so the cells stay readable. The mask is a gentle
   fade (never to zero: a full fade washes out the peaks); the cell values add a halo of their own. */
.detail-sparkline-backdrop {
    position: absolute;
    inset: 0;
    opacity: 0.5;
    -webkit-mask-image: linear-gradient(to top, #000, rgb(0 0 0 / 0.5));
    mask-image: linear-gradient(to top, #000, rgb(0 0 0 / 0.5));
}

/* The dark-mode graph palette starts near the container colour; shift it up for the backdrop. */
:where(.wa-dark) .detail-sparkline-backdrop {
    opacity: 0.75;
    --sparkline-project-gradient-color-1: #196c2e;
    --sparkline-project-gradient-color-2: #2ea043;
    --sparkline-project-gradient-color-3: #56d364;
    --sparkline-project-gradient-color-4: #7ee787;
    --sparkline-project-stroke-color: #b6f0b9;
}

.detail-cells {
    position: relative;
    display: flex;
    flex-wrap: wrap;
    /* Clicks and hovers go through to the backdrop (its tooltip); only the values catch them. */
    pointer-events: none;
}

.detail-cell {
    flex: 1 1 auto;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: 2px;
    padding: var(--wa-space-s) var(--wa-space-m);
}

.detail-cell-label {
    font-size: var(--wa-font-size-3xs);
    letter-spacing: 0.07em;
    text-transform: uppercase;
    color: var(--wa-color-text-quiet);
}

.detail-cell-value {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    align-self: flex-start;
    pointer-events: auto;
    font-weight: 600;
    white-space: nowrap;
    /* Halo in the container colour, so a curve passing behind a value does not cut through it. */
    text-shadow:
        0 0 0.35em color-mix(in oklab, var(--wa-color-surface-raised) 85%, transparent),
        0 0 0.7em color-mix(in oklab, var(--wa-color-surface-raised) 60%, transparent);
}

.detail-stats:not(:has(+ .detail-nav-list)) {
    margin-bottom: var(--wa-space-s);
}
.detail-nav-list {
    padding-bottom: var(--wa-space-s);
}

/* compact height: see utils/compactHeight.js */
/* Show the missing-directory mark while the panel that holds the path is closed */
:where(html.compact-height) .detail-header.compact-collapsed .compact-missing-directory {
    display: inline-flex;
}

/* Show the tools pill */
:where(html.compact-height) .compact-pill {
    display: inline-flex;
}

/* Make toggle zone a clickable flex row */
:where(html.compact-height) .compact-toggle-zone {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-width: 0;
    cursor: pointer;
    flex: 1;
}

:where(html.compact-height) .detail-header.compact-collapsed {
    border-bottom: solid var(--wa-color-surface-border) var(--divider-size);
    gap: 0;
    padding-block: 0;
    padding-inline: var(--wa-space-xs);
}

/* Collapsible rows become a glass panel hanging under the title row, the same as the session header's
   (the glass-surface class is set only at compact height, see the template). It reveals through
   --twicc-reveal (the glass layer, its content and its shadow fade) and a small move; an opacity on the
   panel itself would stop the blur. */
:where(html.compact-height) .detail-collapsible-rows {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    position: absolute;
    top: 100%;
    left: var(--wa-space-xs);
    right: var(--wa-space-xs);
    z-index: 20;
    padding: var(--wa-space-xs) 0;
    border-radius: var(--wa-border-radius-l);

    /* Hidden by default */
    visibility: hidden;
    pointer-events: none;
    --twicc-reveal: 0;
    --twicc-reveal-filter: opacity(var(--twicc-reveal));
    translate: 0 calc(-0.5rem * var(--motion-amount));
    transition:
        --twicc-reveal var(--motion-dur-2) ease-in-out,
        translate var(--motion-dur-2) var(--motion-ease-out),
        visibility var(--motion-dur-2);
}

/* When expanded: reveal the panel */
:where(html.compact-height) .detail-header.compact-expanded .detail-collapsible-rows {
    visibility: visible;
    pointer-events: auto;
    --twicc-reveal: 1;
    translate: 0 0;
}

:where(html.compact-height) .detail-title-row {
    padding-block: var(--wa-space-xs);
}

:where(html.compact-height) .detail-stats,
:where(html.compact-height) .detail-nav-list {
    padding-bottom: 0 !important;
    margin-bottom: 0 !important;
}

/* The bubble has no side padding of its own: each zone brings its own inset. The title row is at the
   header's padding (m) and the bubble starts xs inside it, so the zones' inset is the difference: their
   icons line up with the title's. */
:where(html.compact-height) .detail-identity,
:where(html.compact-height) .detail-actions,
:where(html.compact-height) .detail-nav-list {
    padding-inline: var(--wa-space-xs);
}

:where(html.compact-height) .detail-stats {
    margin-inline: var(--wa-space-xs);
}
</style>
