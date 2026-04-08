<script setup>
/**
 * WorkspaceCard - Card component for displaying a workspace on the Home page.
 *
 * Renders a wa-card with: workspace name, project badges of visible projects,
 * aggregate session count, archived tag, and a context menu dropdown.
 *
 * When the workspace has no visible projects (not activable), the card is
 * shown in a disabled state with reduced opacity and no interaction.
 */
import { computed } from 'vue'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { useWorkspacesStore } from '../stores/workspaces'
import { formatDate } from '../utils/date'
import { SESSION_TIME_FORMAT } from '../constants'
import { aggregateWeeklyActivity } from '../utils/activityAggregation'
import ProjectBadge from './ProjectBadge.vue'
import AggregatedProcessIndicator from './AggregatedProcessIndicator.vue'
import CostDisplay from './CostDisplay.vue'
import ActivitySparkline from './ActivitySparkline.vue'
import AppTooltip from './AppTooltip.vue'

const props = defineProps({
    workspace: {
        type: Object,
        required: true,
    },
})

const emit = defineEmits(['select', 'menu-select'])

const dataStore = useDataStore()
const settingsStore = useSettingsStore()
const workspacesStore = useWorkspacesStore()

const visibleProjectIds = computed(() =>
    workspacesStore.getVisibleProjectIds(props.workspace.id)
)

const isActivable = computed(() =>
    workspacesStore.isActivable(props.workspace.id)
)

/** Visible project objects (cached for aggregation). */
const visibleProjects = computed(() =>
    visibleProjectIds.value.map(pid => dataStore.getProject(pid)).filter(Boolean)
)

const totalSessionsCount = computed(() =>
    visibleProjects.value.reduce((sum, p) => sum + (p.sessions_count || 0), 0)
)

const totalCost = computed(() =>
    visibleProjects.value.reduce((sum, p) => sum + (p.total_cost || 0), 0)
)

const showCosts = computed(() => settingsStore.areCostsShown)

const workspaceWeeklyActivity = computed(() =>
    aggregateWeeklyActivity(visibleProjectIds.value, dataStore.weeklyActivity)
)

/** Most recent mtime across visible projects (Unix seconds), or null. */
const lastMtime = computed(() => {
    let max = 0
    for (const p of visibleProjects.value) {
        if (p.mtime > max) max = p.mtime
    }
    return max || null
})

const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)

function timestampToDate(timestamp) {
    return new Date(timestamp * 1000)
}

function handleSelect() {
    if (!isActivable.value) return
    emit('select', props.workspace)
}

function handleMenuSelect(event) {
    emit('menu-select', event, props.workspace)
}
</script>

<template>
    <wa-card
        class="workspace-card"
        :class="{ disabled: !isActivable }"
        appearance="outlined"
        @click="handleSelect"
    >
        <div class="workspace-info">
            <div class="workspace-title-row">
                <span class="workspace-name"><wa-icon name="layer-group" auto-width :style="workspace.color ? { color: workspace.color } : null"></wa-icon> {{ workspace.name }}</span>
                <AggregatedProcessIndicator :project-ids="visibleProjectIds" size="small" />
                <wa-tag v-if="workspace.archived" variant="neutral" size="small" class="archived-tag">Archived</wa-tag>
                <div class="workspace-menu" @click.stop>
                    <wa-dropdown
                        placement="bottom-end"
                        @wa-select="handleMenuSelect"
                    >
                        <wa-button
                            :id="`workspace-menu-trigger-${workspace.id}`"
                            slot="trigger"
                            variant="neutral"
                            appearance="plain"
                            size="small"
                        >
                            <wa-icon name="ellipsis" label="Workspace menu"></wa-icon>
                        </wa-button>
                        <wa-dropdown-item value="manage">
                            <wa-icon slot="icon" name="gear"></wa-icon>
                            Manage
                        </wa-dropdown-item>
                        <wa-dropdown-item v-if="!workspace.archived" value="archive">
                            <wa-icon slot="icon" name="box-archive"></wa-icon>
                            Archive
                        </wa-dropdown-item>
                        <wa-dropdown-item v-if="workspace.archived" value="unarchive">
                            <wa-icon slot="icon" name="box-open"></wa-icon>
                            Unarchive
                        </wa-dropdown-item>
                    </wa-dropdown>
                    <AppTooltip :for="`workspace-menu-trigger-${workspace.id}`">Workspace actions</AppTooltip>
                </div>
            </div>
            <div v-if="visibleProjectIds.length" class="workspace-projects">
                <ProjectBadge
                    v-for="pid in visibleProjectIds"
                    :key="pid"
                    :project-id="pid"
                    class="workspace-project-badge"
                />
            </div>
            <div v-else class="workspace-empty">
                No visible projects
            </div>
            <div class="workspace-meta-wrapper">
                <div class="workspace-meta">
                    <span :id="`workspace-sessions-${workspace.id}`" class="sessions-count">
                        <wa-icon auto-width name="folder-open" variant="regular"></wa-icon>
                        <span>{{ totalSessionsCount }} session{{ totalSessionsCount !== 1 ? 's' : '' }}</span>
                    </span>
                    <AppTooltip :for="`workspace-sessions-${workspace.id}`">Total sessions across workspace projects</AppTooltip>
                    <template v-if="showCosts">
                        <CostDisplay :id="`workspace-cost-${workspace.id}`" :cost="totalCost" class="workspace-cost" />
                        <AppTooltip :for="`workspace-cost-${workspace.id}`">Total workspace cost</AppTooltip>
                    </template>
                    <template v-if="lastMtime">
                        <span :id="`workspace-mtime-${workspace.id}`" class="workspace-mtime">
                            <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                            <wa-relative-time v-if="useRelativeTime" :date.prop="timestampToDate(lastMtime)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                            <span v-else>{{ formatDate(lastMtime) }}</span>
                        </span>
                        <AppTooltip :for="`workspace-mtime-${workspace.id}`">{{ useRelativeTime ? `Last activity: ${formatDate(lastMtime)}` : 'Last activity' }}</AppTooltip>
                    </template>
                </div>
                <div :id="`workspace-sparkline-${workspace.id}`" class="workspace-graph">
                    <ActivitySparkline :id-suffix="`ws-${workspace.id}`" :data="workspaceWeeklyActivity" />
                </div>
                <AppTooltip :for="`workspace-sparkline-${workspace.id}`">Workspace activity (message turns per week)</AppTooltip>
            </div>
        </div>
    </wa-card>
</template>

<style scoped>
.workspace-card {
    cursor: pointer;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
    &::part(body) {
        position: relative;
    }
}

.workspace-card:hover {
    transform: translateY(-2px);
    box-shadow: var(--wa-shadow-m);
}

.workspace-card.disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.workspace-card.disabled:hover {
    transform: none;
    box-shadow: none;
}

.workspace-info {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.workspace-title-row {
    display: flex;
    align-items: center;
    column-gap: var(--wa-space-s);
}

@media (width <= 50rem) {
    .workspace-title-row {
        flex-wrap: wrap;
    }
}

.workspace-name {
    font-weight: 600;
    font-size: var(--wa-font-size-m);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    > wa-icon {
        margin-right: var(--wa-space-2xs);
    }
}

.workspace-menu {
    margin-left: auto;
    translate: var(--wa-space-m) 0;
}

.workspace-projects {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs) var(--wa-space-m);
    font-size: var(--wa-font-size-s);
}

.workspace-project-badge {
    min-width: 0;
}

.workspace-empty {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.workspace-meta-wrapper {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.workspace-meta {
    display: flex;
    flex-wrap: wrap;
    justify-content: start;
    column-gap: var(--wa-space-m);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);

    & > span {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }
}
</style>
