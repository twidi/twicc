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
import { useDataStore } from '../stores/data'
import { buildProjectTree, flattenProjectTree } from '../utils/projectTree'
import ProjectBadge from './ProjectBadge.vue'
import ProjectProcessIndicator from './ProjectProcessIndicator.vue'

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
})

const store = useDataStore()

const namedProjects = computed(() =>
    props.projects.filter(p => p.name !== null)
)

const flatTree = computed(() => {
    const unnamed = props.projects.filter(p => p.name === null)
    const roots = buildProjectTree(unnamed)
    return flattenProjectTree(roots)
})
</script>

<template>
    <!-- Named projects -->
    <wa-option
        v-for="p in namedProjects"
        :key="p.id"
        :value="p.id"
        :label="store.getProjectDisplayName(p.id)"
    >
        <span class="project-option">
            <ProjectBadge :project-id="p.id" />
            <ProjectProcessIndicator v-if="showProcessIndicator" :project-id="p.id" size="small" />
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
                <ProjectProcessIndicator v-if="showProcessIndicator" :project-id="item.project.id" size="small" />
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
</style>
