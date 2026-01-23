<script setup>
import { useRouter } from 'vue-router'
import ProjectList from '../components/ProjectList.vue'

const router = useRouter()

function handleProjectSelect(project) {
    router.push({ name: 'project', params: { projectId: project.id } })
}
</script>

<template>
    <div class="home-view">
        <header class="home-header">
            <h1>Claude Code Projects</h1>
        </header>
        <main class="home-content">
            <ProjectList @select="handleProjectSelect" />
        </main>
    </div>
</template>

<style scoped>
.home-view {
    padding: var(--wa-space-l);
    max-width: 900px;
    margin: 0 auto;
}

.home-header {
    margin-bottom: var(--wa-space-xl);
}

.home-header h1 {
    margin: 0;
    font-size: var(--wa-font-size-2xl);
    font-weight: 700;
    color: var(--wa-color-text);
}

.home-content {
    /* Content fills available space */
}
</style>
