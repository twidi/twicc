<script setup>
/**
 * ProjectCard - Reusable card component for displaying a single project.
 *
 * Renders a wa-card with: project badge, process indicator, archived tag,
 * dropdown menu (edit/archive/unarchive), directory path, session count,
 * cost, last activity time, and activity sparkline.
 *
 * Provides a `title-prefix` slot for injecting extra content before the
 * project badge (used by ProjectTreeNode for the tree chevron).
 */
import { computed } from 'vue'
import { useCodeCommentsStore } from '../stores/codeComments'
import { useDataStore } from '../stores/data'
import { useSettingsStore } from '../stores/settings'
import { formatDate } from '../utils/date'
import { SESSION_TIME_FORMAT } from '../constants'
import ProjectBadge from './ProjectBadge.vue'
import AggregatedProcessIndicator from './AggregatedProcessIndicator.vue'
import ActivitySparkline from './ActivitySparkline.vue'
import CostDisplay from './CostDisplay.vue'
import AppTooltip from './AppTooltip.vue'

const props = defineProps({
    project: {
        type: Object,
        required: true,
    },
})

const emit = defineEmits(['select', 'menu-select'])

const store = useDataStore()
const settingsStore = useSettingsStore()
const codeCommentsStore = useCodeCommentsStore()

const codeCommentsCount = computed(() => codeCommentsStore.countByProject(props.project.id))
const codeCommentsTooltip = computed(() => {
    const n = codeCommentsCount.value
    if (n === 0) return ''
    return n === 1
        ? '1 code comment not yet sent to Claude'
        : `${n} code comments not yet sent to Claude`
})

// Settings
const showCosts = computed(() => settingsStore.areCostsShown)
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
 * @param {number} timestamp - Unix timestamp in seconds
 * @returns {Date}
 */
function timestampToDate(timestamp) {
    return new Date(timestamp * 1000)
}

function handleSelect() {
    emit('select', props.project)
}

function handleMenuSelect(event) {
    emit('menu-select', event, props.project)
}
</script>

<template>
    <wa-card
        class="project-card"
        appearance="outlined"
        @click="handleSelect"
    >
        <div class="project-info">
            <div class="project-title-row">
                <slot name="title-prefix"></slot>
                <ProjectBadge :project-id="project.id" class="project-title" />
                <wa-icon v-if="codeCommentsCount > 0" :id="`project-comments-${project.id}`" name="comment" variant="regular" class="code-comments-indicator"></wa-icon>
                <AppTooltip v-if="codeCommentsCount > 0" :for="`project-comments-${project.id}`">{{ codeCommentsTooltip }}</AppTooltip>
                <AggregatedProcessIndicator :project-ids="[project.id]" size="small" />
                <wa-tag v-if="project.archived" variant="neutral" size="small" class="archived-tag">Archived</wa-tag>
                <div class="project-menu" @click.stop>
                    <wa-dropdown
                        placement="bottom-end"
                        @wa-select="handleMenuSelect"
                    >
                        <wa-button
                            :id="`project-menu-trigger-${project.id}`"
                            slot="trigger"
                            variant="neutral"
                            appearance="plain"
                            size="small"
                        >
                            <wa-icon name="ellipsis" label="Project menu"></wa-icon>
                        </wa-button>
                        <wa-dropdown-item value="edit">
                            <wa-icon slot="icon" name="pencil"></wa-icon>
                            Edit
                        </wa-dropdown-item>
                        <wa-dropdown-item v-if="!project.archived" value="archive">
                            <wa-icon slot="icon" name="box-archive"></wa-icon>
                            Archive
                        </wa-dropdown-item>
                        <wa-dropdown-item v-if="project.archived" value="unarchive">
                            <wa-icon slot="icon" name="box-open"></wa-icon>
                            Unarchive
                        </wa-dropdown-item>
                    </wa-dropdown>
                    <AppTooltip :for="`project-menu-trigger-${project.id}`">Project actions</AppTooltip>
                </div>
            </div>
            <div v-if="project.directory" class="project-directory">{{ project.directory }}</div>
            <div class="project-meta-wrapper">
                <div class="project-meta">
                    <span :id="`sessions-count-${project.id}`" class="sessions-count">
                        <wa-icon auto-width name="folder-open" variant="regular"></wa-icon>
                        <span>{{ project.sessions_count }} session{{ project.sessions_count !== 1 ? 's' : '' }}</span>
                    </span>
                    <AppTooltip :for="`sessions-count-${project.id}`">Number of sessions</AppTooltip>
                    <template v-if="showCosts">
                        <CostDisplay :id="`project-cost-${project.id}`" :cost="project.total_cost" class="project-cost" />
                        <AppTooltip :for="`project-cost-${project.id}`">Total project cost</AppTooltip>
                    </template>
                    <span :id="`project-mtime-${project.id}`" class="project-mtime">
                        <wa-icon auto-width name="clock" variant="regular"></wa-icon>
                        <wa-relative-time v-if="useRelativeTime" :date.prop="timestampToDate(project.mtime)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                        <span v-else>{{ formatDate(project.mtime) }}</span>
                    </span>
                    <AppTooltip :for="`project-mtime-${project.id}`">{{ useRelativeTime ? `Last activity: ${formatDate(project.mtime)}` : 'Last activity' }}</AppTooltip>
                </div>
                <div :id="`project-sparkline-${project.id}`" class="project-graph">
                    <ActivitySparkline :id-suffix="project.id" :data="store.weeklyActivity[project.id] || []" />
                </div>
                <AppTooltip :for="`project-sparkline-${project.id}`">Project activity (message turns per week)</AppTooltip>
            </div>
        </div>
    </wa-card>
</template>

<style scoped>
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
    column-gap: var(--wa-space-s);
}

@media (width <= 50rem) {
    .project-title-row {
        flex-wrap: wrap;
    }
}

.code-comments-indicator {
    color: var(--wa-color-brand);
    font-size: var(--wa-font-size-s);
}

.project-title {
    font-weight: 600;
    font-size: var(--wa-font-size-m);
    min-width: 0;
}

.project-menu {
    margin-left: auto;
    translate: var(--wa-space-m) 0;
}

.project-directory {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    word-break: break-all;
}

.project-meta-wrapper {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.project-meta {
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
