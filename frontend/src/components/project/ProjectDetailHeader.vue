<script setup>
// ProjectDetailHeader.vue - Header section of the project detail panel.
// Shows project/workspace name, edit button, then three zones: identity (directory path and
// live indicators chip), a stats container (sessions count, cost, last activity over the
// activity sparkline drawn as a backdrop) and the navigation list; manages the edit/manage dialogs.
//
// On small viewports (compact height, utils/compactHeight.js), collapses to a single compact row
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
import { compactHeight } from '../../utils/compactHeight'
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
        <div class="detail-collapsible-rows" :class="{ 'glass-surface': compactHeight }">
            <!-- Identity: directory (single project only; cut from the left so the last folder stays
                 visible) and the live indicators chip -->
            <div v-if="!isAllProjectsMode" class="detail-identity">
                <div v-if="isSingleProjectMode && directory" class="detail-directory">
                    <wa-icon name="folder" class="detail-icon"></wa-icon>
                    <ProjectDirectoryPath :project-id="projectId" emphasize-last class="detail-directory-path" />
                    <!-- flex-basis: 100% puts it on its own line without disturbing the
                         icon/path alignment of the single-line case. -->
                    <ProjectMissingDirectoryNote :project-id="projectId" class="detail-directory-note" />
                </div>

                <!-- Full-size indicators (hidden in compact collapsed mode, which shows its own copies in
                     the title row). The chip hides itself when both indicators render nothing. -->
                <span class="detail-indicators">
                    <CodeCommentsIndicator :project-ids="indicatorProjectIds" class="full-indicator" />
                    <AggregatedProcessIndicator :project-ids="indicatorProjectIds" size="small" class="full-indicator" />
                </span>
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

/* Collapsible rows: transparent wrapper on large viewports */
.detail-collapsible-rows {
    display: contents;
}

/* Zone 1 — identity: directory + indicators chip. Hidden when it would hold neither. */
.detail-identity {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-3xs) var(--wa-space-m);
}

.detail-identity:not(:has(> .detail-directory)):not(:has(> .detail-indicators > *)) {
    display: none;
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

.detail-indicators {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-s);
    margin-inline-start: auto;
    padding: 0.125rem var(--wa-space-s);
    border-radius: 999px;
    border: var(--divider-size) solid var(--wa-color-surface-border);
    background: color-mix(in oklab, var(--wa-color-brand-60) 8%, transparent);
}

.detail-indicators:not(:has(> *)) {
    display: none;
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
/* Show chevron */
:where(html.compact-height) .compact-toggle-chevron {
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

:where(html.compact-height) .compact-toggle-zone:hover .compact-toggle-chevron {
    opacity: 1;
}

/* In compact collapsed mode: show compact indicators, hide full indicators */
:where(html.compact-height) .detail-header.compact-collapsed .compact-indicator {
    display: inline-flex;
}

:where(html.compact-height) .detail-header.compact-collapsed {
    border-bottom: solid var(--wa-color-surface-border) var(--divider-size);
    gap: 0;
    padding-block: 0;
    padding-inline: var(--wa-space-xs);
}

/* In compact collapsed mode: hide the action buttons (archive/edit), like
   the session header — they reappear when the header is expanded. */
:where(html.compact-height) .detail-header.compact-collapsed .detail-title-actions {
    display: none;
}

/* Hide full indicators in compact mode (they live inside the collapsible rows) */
:where(html.compact-height) .detail-header.compact-collapsed .full-indicator {
    display: none;
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

/* When expanded: hide compact indicators, show full indicators */
:where(html.compact-height) .detail-header.compact-expanded .compact-indicator {
    display: none;
}

:where(html.compact-height) .detail-header.compact-expanded .full-indicator {
    display: inline-flex;
}

:where(html.compact-height) .detail-title-row {
    padding-block: var(--wa-space-xs);
}

:where(html.compact-height) .detail-stats,
:where(html.compact-height) .detail-nav-list {
    padding-bottom: 0 !important;
    margin-bottom: 0 !important;
}

/* The bubble has no side padding of its own: each zone brings its own inset. */
:where(html.compact-height) .detail-identity,
:where(html.compact-height) .detail-nav-list {
    padding-inline: var(--wa-space-m);
}

:where(html.compact-height) .detail-stats {
    margin-inline: var(--wa-space-m);
}
</style>
