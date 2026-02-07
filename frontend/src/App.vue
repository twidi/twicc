<script setup>
import { onMounted, watch, computed } from 'vue'
import { useRoute } from 'vue-router'
import { Notivue, Notification, lightTheme, slateTheme } from 'notivue'
import { useWebSocket } from './composables/useWebSocket'
import { useDataStore } from './stores/data'
import { useSettingsStore } from './stores/settings'
import { useAuthStore } from './stores/auth'
import { THEME_MODE } from './constants'
import ConnectionIndicator from './components/ConnectionIndicator.vue'

const route = useRoute()
const authStore = useAuthStore()

const isAuthenticated = computed(() => !authStore.needsLogin)
const isLoginPage = computed(() => route.name === 'login')
const isConnecting = computed(() => authStore.isConnecting)

// Initialize WebSocket connection for real-time updates.
// Connection is deferred until authenticated (see useWebSocket).
const { wsStatus, openWs, closeWs } = useWebSocket()

// Load initial data and connect WebSocket when authenticated
const dataStore = useDataStore()

// React to authentication state changes (initial check + after login)
watch(isAuthenticated, async (authenticated) => {
    if (authenticated) {
        await dataStore.loadProjects({ isInitialLoading: true })
        openWs()
    } else {
        closeWs()
    }
}, { immediate: true })

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
    <!-- Connecting overlay: shown while waiting for backend during auth check retry -->
    <div v-if="isConnecting" class="connecting-backdrop">
        <div class="connecting-content">
            <wa-spinner></wa-spinner>
            <p class="connecting-text">Connecting to server...</p>
        </div>
    </div>

    <ConnectionIndicator v-if="!isLoginPage && !isConnecting" :status="wsStatus" />
    <div class="app-container">
        <router-view />
    </div>

    <!-- Toast notification system (theme inverted for contrast) -->
    <Notivue v-slot="item">
        <Notification :item="item" :theme="toastTheme" />
    </Notivue>
</template>

<style>
.connecting-backdrop {
    position: fixed;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--wa-color-surface-default);
    z-index: 10000;
}

.connecting-content {
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
}

.connecting-text {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    margin: 0;
}

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
