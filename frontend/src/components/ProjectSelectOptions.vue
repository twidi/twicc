<script setup>
/**
 * ProjectSelectOptions — Renders wa-option elements for a list of projects,
 * split into named projects (with ProjectBadge) and unnamed projects
 * (as a flattened directory tree with folder separators).
 *
 * Designed to be placed inside a <wa-select> parent. Each consumer provides
 * its own wa-select wrapper and any extra options (e.g. "All Projects").
 *
 * A wa-divider is rendered between the named and unnamed sections when both
 * are present. The consumer is responsible for any leading divider.
 */

import { computed } from 'vue'
import { useCodeCommentsStore } from '../stores/codeComments'
import { useDataStore } from '../stores/data'
import { splitProjectsByPriority } from '../utils/projectSort'
import { buildProjectTree, flattenProjectTree } from '../utils/projectTree'
import ProjectBadge from './ProjectBadge.vue'
import AggregatedProcessIndicator from './AggregatedProcessIndicator.vue'

const props = defineProps({
    /** Array of project objects to display as options */
    projects: {
        type: Array,
        required: true,
    },
    /** Whether to show the process indicator (running/waiting) next to each project */
    showProcessIndicator: {
        type: Boolean,
        default: false,
    },
    /** Ordered array of project IDs to display first (e.g. workspace projects). When provided, these appear before all other projects with a divider. */
    priorityProjectIds: {
        type: Array,
        default: null,
    },
    /** Label for the priority section header (e.g. "Sprint 14 projects"). Shown as a disabled option before priority projects. */
    priorityLabel: {
        type: String,
        default: null,
    },
    /** Color for the workspace icon shown in the priority section header. */
    priorityColor: {
        type: String,
        default: null,
    },
})

const store = useDataStore()
const codeCommentsStore = useCodeCommentsStore()

// When priorityProjectIds is provided, split projects into prioritized (workspace) and others
const hasPriority = computed(() => props.priorityProjectIds?.length > 0)

const prioritySplit = computed(() => {
    if (!hasPriority.value) return null
    return splitProjectsByPriority(props.projects, props.priorityProjectIds)
})

// Named/unnamed split applies to "others" when priority is active, or all projects otherwise
const projectsForDefaultSplit = computed(() =>
    hasPriority.value ? prioritySplit.value.others : props.projects
)

const namedProjects = computed(() =>
    projectsForDefaultSplit.value.filter(p => p.name !== null)
)

const flatTree = computed(() => {
    const unnamed = projectsForDefaultSplit.value.filter(p => p.name === null)
    const roots = buildProjectTree(unnamed)
    return flattenProjectTree(roots)
})
</script>

<template>
    <!-- Priority projects (workspace) — shown first when priorityProjectIds is provided -->
    <template v-if="hasPriority">
        <wa-option v-if="priorityLabel && prioritySplit.prioritized.length" disabled class="section-header-option">
            <wa-icon name="layer-group" auto-width class="ws-header-icon" :style="priorityColor ? { color: priorityColor } : null"></wa-icon>
            {{ priorityLabel }}
        </wa-option>
        <wa-option
            v-for="p in prioritySplit.prioritized"
            :key="p.id"
            :value="p.id"
            :label="store.getProjectDisplayName(p.id)"
        >
            <span class="project-option">
                <ProjectBadge :project-id="p.id" />
                <span class="project-option-indicators">
                    <wa-icon v-if="codeCommentsStore.countByProject(p.id) > 0" name="comment" variant="regular" class="code-comments-indicator"></wa-icon>
                    <AggregatedProcessIndicator v-if="showProcessIndicator" :project-ids="[p.id]" size="small" />
                </span>
            </span>
        </wa-option>

        <!-- Divider + "Other projects" header between priority and remaining projects -->
        <template v-if="prioritySplit.prioritized.length && (namedProjects.length || flatTree.length)">
            <wa-divider></wa-divider>
            <wa-option v-if="priorityLabel" disabled class="section-header-option">Other projects</wa-option>
        </template>
    </template>

    <!-- Named projects -->
    <wa-option
        v-for="p in namedProjects"
        :key="p.id"
        :value="p.id"
        :label="store.getProjectDisplayName(p.id)"
    >
        <span class="project-option">
            <ProjectBadge :project-id="p.id" />
            <span class="project-option-indicators">
                <wa-icon v-if="codeCommentsStore.countByProject(p.id) > 0" name="comment" variant="regular" class="code-comments-indicator"></wa-icon>
                <AggregatedProcessIndicator v-if="showProcessIndicator" :project-ids="[p.id]" size="small" />
            </span>
        </span>
    </wa-option>

    <!-- Divider between named and unnamed sections (only when both exist) -->
    <wa-divider v-if="namedProjects.length && flatTree.length"></wa-divider>

    <!-- Unnamed projects (flattened directory tree) -->
    <template v-for="item in flatTree" :key="item.key">
        <wa-option
            v-if="item.isFolder"
            disabled
            class="tree-folder-option"
        >
            <span class="tree-folder-label" :style="{ paddingLeft: `${item.depth * 12}px` }">
                {{ item.segment }}
            </span>
        </wa-option>
        <wa-option
            v-else
            :value="item.project.id"
            :label="store.getProjectDisplayName(item.project.id)"
        >
            <span class="project-option" :style="{ paddingLeft: `${item.depth * 12}px` }">
                <ProjectBadge :project-id="item.project.id" />
                <span class="project-option-indicators">
                    <wa-icon v-if="codeCommentsStore.countByProject(item.project.id) > 0" name="comment" variant="regular" class="code-comments-indicator"></wa-icon>
                    <AggregatedProcessIndicator v-if="showProcessIndicator" :project-ids="[item.project.id]" size="small" />
                </span>
            </span>
        </wa-option>
    </template>
</template>

<style scoped>
.project-option {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    width: 100%;
    justify-content: space-between;
}
.project-option-indicators {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}
.code-comments-indicator {
    color: var(--wa-color-brand);
    font-size: var(--wa-font-size-s);
}
wa-divider {
    --width: 4px;
    --spacing: 4px;
}

.tree-folder-option {
}

.tree-folder-label {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
}

.section-header-option {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--wa-color-text-quiet);
}

.ws-header-icon {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    margin-inline: 0.2em;
}
</style>
