<script setup>
import { onMounted, watch, computed } from 'vue'
import { useWebSocket } from './composables/useWebSocket'
import { useDataStore } from './stores/data'
import { useSettingsStore } from './stores/settings'
import ConnectionIndicator from './components/ConnectionIndicator.vue'

// Initialize WebSocket connection for real-time updates
const { wsStatus } = useWebSocket()

// Load initial data
const dataStore = useDataStore()
onMounted(async () => {
    await dataStore.loadProjects({ isInitialLoading: true })
})

// Sync display mode to body data attribute
const settingsStore = useSettingsStore()
const displayMode = computed(() => settingsStore.getDisplayMode)

// Set initial value and watch for changes
document.body.dataset.displayMode = displayMode.value
watch(displayMode, (newMode) => {
    document.body.dataset.displayMode = newMode
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
    background: var(--wa-color-surface-default);
    color: var(--wa-color-text-normal);
}

:root {
    --user-card-base-color: var(--wa-color-indigo-95);
    --assistant-card-base-color: var(--wa-color-gray-95);

    --wa-font-size-3xs: round(calc(var(--wa-font-size-2xs) / 1.125), 1px);
}

.wa-dark {
    --wa-color-surface-default: #1b2733;

    --wa-color-neutral-fill-quiet: #141d26;
    --wa-color-surface-raised: #141d26;

    --wa-color-brand-border-loud: var(--wa-color-brand-50);

    --user-card-base-color: #323b45;
    --assistant-card-base-color: #252e38;
}

</style>
