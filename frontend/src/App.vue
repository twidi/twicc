<script setup>
import { onMounted } from 'vue'
import { useWebSocket } from './composables/useWebSocket'
import { useDataStore } from './stores/data'
import ConnectionIndicator from './components/ConnectionIndicator.vue'

// Initialize WebSocket connection for real-time updates
const { wsStatus } = useWebSocket()

// Load initial data
const store = useDataStore()
onMounted(async () => {
    await store.loadProjects({ isInitialLoading: true })
})
</script>

<template>
    <ConnectionIndicator :status="wsStatus" />
    <div class="app-container">
        <router-view />
    </div>
</template>

<style>
body {
    margin: 0;
    padding: 0;
}

.app-container {
    min-height: 100vh;
    background: var(--wa-color-surface-base);
    color: var(--wa-color-text);
}
</style>
