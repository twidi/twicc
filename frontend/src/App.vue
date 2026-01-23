<script setup>
import { onMounted } from 'vue'
import { useWebSocket } from './composables/useWebSocket'
import { useDataStore } from './stores/data'

// Initialize WebSocket connection for real-time updates
const { wsStatus } = useWebSocket()

// Load initial data
const store = useDataStore()
onMounted(async () => {
    await store.loadProjects()
})
</script>

<template>
    <div class="app-container">
        <router-view />
    </div>
</template>

<style>
.app-container {
    min-height: 100vh;
    background: var(--wa-color-surface-base);
    color: var(--wa-color-text);
}
</style>
