<script setup>
import { onMounted, watch, computed } from 'vue'
import { Notivue, Notification, lightTheme, slateTheme } from 'notivue'
import { useWebSocket } from './composables/useWebSocket'
import { useDataStore } from './stores/data'
import { useSettingsStore } from './stores/settings'
import { THEME_MODE } from './constants'
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

// Notivue theme - inverted for contrast (dark theme when app is light, and vice-versa)
const toastTheme = computed(() => {
    const isDark = settingsStore.getEffectiveTheme === THEME_MODE.DARK
    // Invert: use light toast theme when app is dark, and vice-versa
    return isDark ? lightTheme : slateTheme
})
</script>

<template>
    <ConnectionIndicator :status="wsStatus" />
    <div class="app-container">
        <router-view />
    </div>

    <!-- Toast notification system (theme inverted for contrast) -->
    <Notivue v-slot="item">
        <Notification :item="item" :theme="toastTheme" />
    </Notivue>
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
    overflow: hidden;

    --user-card-base-color: var(--wa-color-indigo-95);
    --assistant-card-base-color: var(--wa-color-gray-95);

    --wa-font-size-3xs: round(calc(var(--wa-font-size-2xs) / 1.125), 1px);

    /* --main-header-footer-bg-color: var(--wa-color-surface-raised); */
    --main-header-footer-bg-color: transparent;

}

.wa-dark {
    --wa-color-surface-default: #1b2733;

    --wa-color-neutral-fill-quiet: #141d26;
    --wa-color-surface-raised: #141d26;

    --wa-color-brand-border-loud: var(--wa-color-brand-50);

    --user-card-base-color: #323b45;
    --assistant-card-base-color: #252e38;
}

/* Reset Web Awesome button styles inside Notivue notifications */
.Notivue__close {
    all: unset;
    cursor: pointer;
    padding: calc(var(--nv-spacing) / 2);
    margin: var(--nv-spacing) var(--nv-spacing) var(--nv-spacing) 0;
    font-weight: 700;
    line-height: 1;
    font-size: var(--nv-message-size);
    color: var(--nv-fg, var(--nv-global-fg));
    -webkit-tap-highlight-color: transparent;
    position: relative;
}

/* Add separator line below title in notifications */
body .Notivue__content-title {
    border-bottom: 1px solid var(--nv-accent, var(--nv-global-accent));
    padding-bottom: var(--nv-spacing);
    margin-bottom: var(--nv-spacing);
}

</style>
