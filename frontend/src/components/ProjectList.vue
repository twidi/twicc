<script setup>
import { useDataStore } from '../stores/data'
import { formatDate } from '../utils/date'

const store = useDataStore()

const emit = defineEmits(['select'])

function handleSelect(project) {
    emit('select', project)
}
</script>

<template>
    <div class="project-list">
        <wa-card
            v-for="project in store.getProjects"
            :key="project.id"
            class="project-card"
            appearance="outlined"
            @click="handleSelect(project)"
        >
            <div class="project-info">
                <div class="project-id">{{ project.id }}</div>
                <div v-if="project.directory" class="project-directory">{{ project.directory }}</div>
                <div class="project-meta">
                    <span class="sessions-count">
                        {{ project.sessions_count }} session{{ project.sessions_count !== 1 ? 's' : '' }}
                    </span>
                    <span class="project-mtime">{{ formatDate(project.mtime) }}</span>
                </div>
            </div>
        </wa-card>
        <div v-if="store.getProjects.length === 0" class="empty-state">
            No projects found
        </div>
    </div>
</template>

<style scoped>
.project-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.project-card {
    cursor: pointer;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
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

.project-id {
    font-weight: 600;
    font-size: var(--wa-font-size-m);
    word-break: break-all;
}

.project-directory {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    word-break: break-all;
}

.project-meta {
    display: flex;
    justify-content: space-between;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.empty-state {
    text-align: center;
    padding: var(--wa-space-xl);
    color: var(--wa-color-text-quiet);
}
</style>
