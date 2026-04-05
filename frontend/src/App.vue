<script setup>
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Notivue, Notification, lightTheme, slateTheme } from 'notivue'
import { useWebSocket, versionMismatchDetected } from './composables/useWebSocket'
import { useDataStore } from './stores/data'
import { useSettingsStore } from './stores/settings'
import { useAuthStore } from './stores/auth'
import { THEME_MODE } from './constants'
import { useFavicon } from './composables/useFavicon'
import ConnectionIndicator from './components/ConnectionIndicator.vue'
import CustomNotification from './components/CustomNotification.vue'
import CommandPalette from './components/CommandPalette.vue'
import SearchOverlay from './components/SearchOverlay.vue'
import { initStaticCommands } from './commands/staticCommands'

const route = useRoute()
const router = useRouter()
initStaticCommands(router)
const authStore = useAuthStore()

const isAuthenticated = computed(() => !authStore.needsLogin)
const isLoginPage = computed(() => route.name === 'login')
const isConnecting = computed(() => authStore.isConnecting)

// Initialize WebSocket connection for real-time updates.
// Connection is deferred until authenticated (see useWebSocket).
const { wsStatus, openWs, closeWs } = useWebSocket()

// Dynamic favicon: overlays a status badge based on global process state
useFavicon()

// Load initial data and connect WebSocket when authenticated
const dataStore = useDataStore()

// React to authentication state changes (initial check + after login)
watch(isAuthenticated, async (authenticated) => {
    if (authenticated) {
        await dataStore.loadHomeData()
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

// Auto-reload when backend version changes
watch(versionMismatchDetected, (mismatch) => {
    if (mismatch) {
        setTimeout(() => window.location.reload(), 3000)
    }
})

// ─── Command Palette (Ctrl+K / Cmd+K) & Search (Ctrl+Shift+F / Ctrl+F) ──
const commandPaletteRef = ref(null)
const searchOverlayRef = ref(null)

// Route names where Ctrl+F opens in-session search (main chat tab only)
const SESSION_CHAT_ROUTES = new Set(['session', 'projects-session'])

// All session route names (for tab keyboard shortcuts: Alt+Shift+{1-4, ←, →, ↑})
const SESSION_ROUTES = new Set([
    'session', 'session-subagent', 'session-files', 'session-git', 'session-terminal',
    'projects-session', 'projects-session-subagent', 'projects-session-files', 'projects-session-git', 'projects-session-terminal',
])

// Terminal route names (for terminal tab shortcuts: Alt+Ctrl+Shift+{1-9, ←, →, ↑})
const TERMINAL_ROUTES = new Set([
    'session-terminal', 'projects-session-terminal',
])

function handleGlobalKeydown(e) {
    const modKey = settingsStore.isMac ? e.metaKey : e.ctrlKey
    if (modKey && e.key === 'k') {
        e.preventDefault()
        e.stopPropagation()
        commandPaletteRef.value?.open()
    }
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'F') {
        e.preventDefault()
        e.stopPropagation()
        searchOverlayRef.value?.open()
    }
    // Ctrl+F (without Shift): in-session search when on a session's chat tab.
    // First press opens the custom search bar (and blocks native browser Find).
    // Second press (bar already open) closes it and lets the native Find through.
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === 'f') {
        if (SESSION_CHAT_ROUTES.has(route.name)) {
            const detail = { handled: false }
            window.dispatchEvent(new CustomEvent('twicc:toggle-session-search', { detail }))
            if (detail.handled) {
                e.preventDefault()
                e.stopPropagation()
            }
        }
    }
    // Alt+Ctrl+Shift+{1-9, ←, →, ↑, ↓}: terminal tab navigation within the terminal panel.
    // Dispatches a custom event handled by the active TerminalPanel instance.
    if (e.altKey && e.shiftKey && e.ctrlKey && !e.metaKey && TERMINAL_ROUTES.has(route.name)) {
        let tabAction = null
        const digitMatch = e.code.match(/^(?:Digit|Numpad)([1-9])$/)
        if (digitMatch) {
            tabAction = { type: 'direct', index: parseInt(digitMatch[1]) }
        } else if (e.key === 'ArrowLeft') {
            tabAction = { type: 'prev' }
        } else if (e.key === 'ArrowRight') {
            tabAction = { type: 'next' }
        } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            tabAction = { type: 'last-visited' }
        }
        if (tabAction) {
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:terminal-tab-shortcut', { detail: tabAction }))
        }
    }
    // Alt+Shift+{1-4, ←, →, ↑, ↓}: tab navigation within a session.
    // Dispatches a custom event handled by the active SessionView instance.
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && SESSION_ROUTES.has(route.name)) {
        let tabAction = null
        // Use e.code (physical key) for digits — e.key depends on keyboard layout
        // and modifiers (e.g. French AZERTY: Alt+Shift+number row produces unexpected e.key values).
        const digitMatch = e.code.match(/^(?:Digit|Numpad)([1-4])$/)
        if (digitMatch) {
            tabAction = { type: 'direct', index: parseInt(digitMatch[1]) }
        } else if (e.key === 'ArrowLeft') {
            tabAction = { type: 'prev' }
        } else if (e.key === 'ArrowRight') {
            tabAction = { type: 'next' }
        } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            tabAction = { type: 'last-visited' }
        }
        if (tabAction) {
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:tab-shortcut', { detail: tabAction }))
        }
    }
}

onMounted(() => {
    document.addEventListener('keydown', handleGlobalKeydown, { capture: true })
})
onBeforeUnmount(() => {
    document.removeEventListener('keydown', handleGlobalKeydown, { capture: true })
})

// Notivue theme - inverted for contrast (dark theme when app is light, and vice-versa)
const toastTheme = computed(() => {
    const isDark = settingsStore.getEffectiveTheme === THEME_MODE.DARK
    // Invert: use light toast theme when app is dark, and vice-versa
    return {
        ...(isDark ? lightTheme : slateTheme),
        '--nv-width': '100%',
        '--nv-min-width': '30rem',
    }
})
</script>

<template>
    <!-- Version mismatch: non-dismissible reload dialog -->
    <wa-dialog :open="versionMismatchDetected || undefined" without-header @wa-hide.prevent>
        <div class="version-reload-content">
            <wa-spinner></wa-spinner>
            <p class="version-reload-text">TwiCC has been updated, reloading…</p>
        </div>
    </wa-dialog>

    <!-- Connecting overlay: shown while waiting for backend during auth check retry -->
    <div v-if="isConnecting" class="connecting-backdrop">
        <div class="connecting-content">
            <wa-spinner></wa-spinner>
            <p class="connecting-text">Connecting to server...</p>
        </div>
    </div>

    <ConnectionIndicator v-if="!isLoginPage && !isConnecting" :status="wsStatus" />
    <CommandPalette ref="commandPaletteRef" />
    <SearchOverlay ref="searchOverlayRef" />
    <!-- Prevent browser default drop behavior (e.g. navigating to a dropped image).
         Our specific drop handlers in SessionItemsList call preventDefault themselves;
         this catches any drops that miss those zones. -->
    <div class="app-container" @dragover.prevent @drop.prevent>
        <router-view />
    </div>

    <!-- Toast notification system (theme inverted for contrast) -->
    <Notivue v-slot="item">
        <CustomNotification v-if="item.props?.custom" :item="item" :theme="toastTheme" />
        <Notification v-else :item="item" :theme="toastTheme" />
    </Notivue>
</template>

<style>
.version-reload-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    padding: var(--wa-space-l);
    text-align: center;
}

.version-reload-text {
    font-size: var(--wa-font-size-l);
    color: var(--wa-color-text-normal);
    margin: 0;
}

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

/*
 * Override WA 3.3's "box-sizing: inherit" from @layer wa-native.
 * Native <details> elements break "inherit" propagation (both Chrome & Firefox):
 * children inside <details> fall back to content-box even when the parent is border-box.
 * This affects all slotted content inside wa-details. Using explicit border-box fixes it.
 * Our unlayered CSS has higher cascade priority than @layer wa-native.
 */
*, *::before, *::after {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 0;
}

.app-container {
    min-height: 100dvh;
    background: var(--wa-color-surface-default);
    color: var(--wa-color-text-normal);
}

:root {
    overflow-y: auto;

    --user-card-base-color: var(--wa-color-indigo-95);
    --assistant-card-base-color: var(--wa-color-gray-95);

    --wa-font-size-3xs: round(calc(var(--wa-font-size-2xs) / 1.125), 1px);

    /* --main-header-footer-bg-color: var(--wa-color-surface-raised); */
    --main-header-footer-bg-color: transparent;

    /* Sparkline / heatmap graph colors (light mode) — green (default) */
    --sparkline-project-gradient-color-0: #ebedf0;
    --sparkline-project-gradient-color-1: #aceebb;
    --sparkline-project-gradient-color-2: #4ac26b;
    --sparkline-project-gradient-color-3: #2da44e;
    --sparkline-project-gradient-color-4: #116329;
    --sparkline-project-stroke-color: #8cc665;

    /* Sparkline graph colors — blue (sessions) */
    --sparkline-blue-gradient-color-1: #a8d4ff;
    --sparkline-blue-gradient-color-2: #4da6ff;
    --sparkline-blue-gradient-color-3: #1a7fdb;
    --sparkline-blue-gradient-color-4: #0a4f8a;
    --sparkline-blue-stroke-color: #6ab8f7;

    /* Sparkline graph colors — red (cost) */
    --sparkline-red-gradient-color-1: #ffb3b3;
    --sparkline-red-gradient-color-2: #ff5c5c;
    --sparkline-red-gradient-color-3: #d63333;
    --sparkline-red-gradient-color-4: #8b1a1a;
    --sparkline-red-stroke-color: #f77070;

    /* Sparkline graph colors — green (temporal) */
    --sparkline-green-gradient-color-1: #aceebb;
    --sparkline-green-gradient-color-2: #4ac26b;
    --sparkline-green-gradient-color-3: #2da44e;
    --sparkline-green-gradient-color-4: #116329;
    --sparkline-green-stroke-color: #8cc665;

    /* Sparkline graph colors — orange (smoothed burn rate) */
    --sparkline-orange-gradient-color-1: #ffe6b3;
    --sparkline-orange-gradient-color-2: #f5a623;
    --sparkline-orange-gradient-color-3: #d4760a;
    --sparkline-orange-gradient-color-4: #8a4d0f;
    --sparkline-orange-stroke-color: #e89d3f;

    /* Sparkline graph colors — purple (recent rate long) */
    --sparkline-purple-gradient-color-1: #e8d5f5;
    --sparkline-purple-gradient-color-2: #a855f7;
    --sparkline-purple-gradient-color-3: #7c3aed;
    --sparkline-purple-gradient-color-4: #4c1d95;
    --sparkline-purple-stroke-color: #b07ce8;

    /* Sparkline graph colors — pink (recent rate short) */
    --sparkline-pink-gradient-color-1: #fce4ec;
    --sparkline-pink-gradient-color-2: #f06292;
    --sparkline-pink-gradient-color-3: #d81b60;
    --sparkline-pink-gradient-color-4: #880e4f;
    --sparkline-pink-stroke-color: #e57399;

    /* Diff editor colors (light mode) */
    --diff-removedLineBackground: #FEF1F1;
    --diff-removedTextBackground: #FFC4C3;
    --diff-insertedLineBackground: #C0FFD8;
    --diff-insertedTextBackground: #A7E9B8;
    --diff-selectionBackground: #BBDFFF99;
}

.wa-dark {
    --wa-color-surface-default: #1b2733;

    --wa-color-neutral-fill-quiet: #141d26;
    --wa-color-surface-raised: #141d26;

    --wa-color-brand-border-loud: var(--wa-color-brand-50);

    --user-card-base-color: #323b45;
    --assistant-card-base-color: #252e38;

    /* Sparkline / heatmap graph colors (dark mode) — green (default) */
    --sparkline-project-gradient-color-0: #151b23;
    --sparkline-project-gradient-color-1: #033a16;
    --sparkline-project-gradient-color-2: #196c2e;
    --sparkline-project-gradient-color-3: #2ea043;
    --sparkline-project-gradient-color-4: #56d364;
    --sparkline-project-stroke-color: #8cc665;

    /* Sparkline graph colors — blue (sessions, dark mode) */
    --sparkline-blue-gradient-color-1: #0a2d4f;
    --sparkline-blue-gradient-color-2: #1a5a8a;
    --sparkline-blue-gradient-color-3: #3a8fd4;
    --sparkline-blue-gradient-color-4: #6abef7;
    --sparkline-blue-stroke-color: #6ab8f7;

    /* Sparkline graph colors — red (cost, dark mode) */
    --sparkline-red-gradient-color-1: #4f0a0a;
    --sparkline-red-gradient-color-2: #8a1a1a;
    --sparkline-red-gradient-color-3: #d44040;
    --sparkline-red-gradient-color-4: #f77070;
    --sparkline-red-stroke-color: #f77070;

    /* Sparkline graph colors — green (temporal, dark mode) */
    --sparkline-green-gradient-color-1: #033a16;
    --sparkline-green-gradient-color-2: #196c2e;
    --sparkline-green-gradient-color-3: #2ea043;
    --sparkline-green-gradient-color-4: #56d364;
    --sparkline-green-stroke-color: #8cc665;

    /* Sparkline graph colors — orange (smoothed burn rate, dark mode) */
    --sparkline-orange-gradient-color-1: #3d1e00;
    --sparkline-orange-gradient-color-2: #7a3c00;
    --sparkline-orange-gradient-color-3: #d4760a;
    --sparkline-orange-gradient-color-4: #f5a623;
    --sparkline-orange-stroke-color: #e89d3f;

    /* Sparkline graph colors — purple (recent rate long, dark mode) */
    --sparkline-purple-gradient-color-1: #2e1065;
    --sparkline-purple-gradient-color-2: #5b21b6;
    --sparkline-purple-gradient-color-3: #8b5cf6;
    --sparkline-purple-gradient-color-4: #c084fc;
    --sparkline-purple-stroke-color: #b07ce8;

    /* Sparkline graph colors — pink (recent rate short, dark mode) */
    --sparkline-pink-gradient-color-1: #4a0e2a;
    --sparkline-pink-gradient-color-2: #9d174d;
    --sparkline-pink-gradient-color-3: #ec4899;
    --sparkline-pink-gradient-color-4: #f9a8d4;
    --sparkline-pink-stroke-color: #e57399;

    /* Diff editor colors (dark mode) */
    --diff-removedLineBackground: #451B1B;
    --diff-removedTextBackground: #5E1B1B;
    --diff-insertedLineBackground: #1B452B;
    --diff-insertedTextBackground: #2A573B;
    --diff-selectionBackground: #003d7399;
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
    align-self: flex-start;
}

/* Add separator line below title in notifications */
body .Notivue__content-title {
    border-bottom: 1px solid var(--nv-accent, var(--nv-global-accent));
    padding-bottom: var(--nv-spacing);
    margin-bottom: var(--nv-spacing);
}

/* Floating drag-hover indicator (spring-loaded folder pattern) */
.drag-hover-indicator {
    position: fixed;
    z-index: 10000;
    pointer-events: none;
    left: calc(var(--x) * 1px + 16px);
    top: calc(var(--y) * 1px - 16px);
    --size: 20px;
    --track-width: 2.5px;
    --indicator-color: var(--wa-color-success);
    --indicator-transition-duration: 0s;
}

/* Style for low-height buttons */

.reduced-height {
    font-size: var(--wa-font-size-3xs);
    &::part(label) {
        scale: 1.3;
    }
}

/* Keep blockquote normal size */
blockquote {
    font-family: inherit !important;
    font-size: inherit !important;
}

</style>
