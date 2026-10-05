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
import { computed, ref } from 'vue'
import { useHomeCardEntrance } from '../../composables/useHomeCardCascade'
import { useProjectMark } from '../../composables/useProjectMark'
import { useDataStore } from '../../stores/data'
import { useSettingsStore } from '../../stores/settings'
import { formatDate } from '../../utils/date'
import { SESSION_TIME_FORMAT } from '../../constants'
import ProjectBadge from './ProjectBadge.vue'
import ProjectDirectoryPath from './ProjectDirectoryPath.vue'
import ProjectMissingDirectoryNote from './ProjectMissingDirectoryNote.vue'
import AggregatedProcessIndicator from '../ui/AggregatedProcessIndicator.vue'
import CodeCommentsIndicator from '../ui/CodeCommentsIndicator.vue'
import ActivitySparkline from '../activity/ActivitySparkline.vue'
import CostDisplay from '../ui/CostDisplay.vue'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    project: {
        type: Object,
        required: true,
    },
})

const emit = defineEmits(['select', 'menu-select'])

const store = useDataStore()
const settingsStore = useSettingsStore()

const lastActivity = computed(() => store.getProjectActivity(props.project.id))

// Home card cascade (step 7b): the card enters when the home shows it.
const cardRef = ref(null)
useHomeCardEntrance(cardRef)

// Hover glow colour: the project's dot colour (worktree → main-repo fallback).
const { dotColor } = useProjectMark(computed(() => props.project.id))

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
        ref="cardRef"
        class="project-card"
        :style="{ '--card-glow-color': dotColor || null }"
        appearance="outlined"
        @click="handleSelect"
    >
        <div class="project-info">
            <div class="project-title-row">
                <slot name="title-prefix"></slot>
                <ProjectBadge :project-id="project.id" class="project-title" />
                <CodeCommentsIndicator :project-ids="[project.id]" />
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
                            <wa-icon name="ellipsis-v" label="Project menu"></wa-icon>
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
            <div v-if="project.directory" class="project-directory">
                <ProjectDirectoryPath :project-id="project.id" />
            </div>
            <ProjectMissingDirectoryNote :project-id="project.id" />
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
                        <wa-relative-time v-if="useRelativeTime" :date.prop="timestampToDate(lastActivity)" :format="relativeTimeFormat" numeric="always" sync></wa-relative-time>
                        <span v-else>{{ formatDate(lastActivity) }}</span>
                    </span>
                    <AppTooltip :for="`project-mtime-${project.id}`">{{ useRelativeTime ? `Last activity: ${formatDate(lastActivity)}` : 'Last activity' }}</AppTooltip>
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
    transition:
        translate var(--motion-dur-2) var(--motion-ease-spring),
        box-shadow var(--motion-dur-2) var(--motion-ease),
        border-color var(--motion-dur-2) var(--motion-ease);
    &::part(body) {
        position: relative;
    }
}

/* While the card enters (home cascade, step 7b), its animation owns translate: a hover
   transition on it would make the card snap. */
.project-card.home-card-entering {
    transition:
        box-shadow var(--motion-dur-2) var(--motion-ease),
        border-color var(--motion-dur-2) var(--motion-ease);
}

/* Lift and glow in the project's dot colour (accent when none). */
@media (hover: hover) {
    .project-card:hover {
        translate: 0 calc(-0.125rem * var(--motion-amount));
        border-color: color-mix(in oklab, var(--card-glow-color, var(--wa-color-brand-60)) 55%, transparent);
        box-shadow: var(--depth-2), 0 0.5rem 1.75rem -0.75rem color-mix(in oklab, var(--card-glow-color, var(--wa-color-brand-60)) 70%, transparent);
    }
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
