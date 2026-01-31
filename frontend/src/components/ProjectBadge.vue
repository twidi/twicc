<script setup>
// ProjectBadge.vue - Displays a project color dot and display name
import { computed } from 'vue'
import { useDataStore } from '../stores/data'

const props = defineProps({
    projectId: {
        type: String,
        required: true,
    },
})

const store = useDataStore()

const project = computed(() => store.getProject(props.projectId))
const displayName = computed(() => store.getProjectDisplayName(props.projectId))
const color = computed(() => project.value?.color || null)
</script>

<template>
    <span class="project-badge">
        <span
            class="project-badge-dot"
            :style="color ? { '--dot-color': color } : null"
        ></span>
        <span class="project-badge-name">{{ displayName }}</span>
    </span>
</template>

<style scoped>
.project-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4em;
    min-width: 0;
}

.project-badge-dot {
    width: 0.75em;
    height: 0.75em;
    border-radius: 50%;
    flex-shrink: 0;
    border: 1px solid;
    box-sizing: border-box;
    background-color: var(--dot-color, transparent);
    border-color: var(--dot-color, var(--wa-color-border-quiet));
}

.project-badge-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
</style>
