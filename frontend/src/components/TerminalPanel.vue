<script setup>
import { watch, computed } from 'vue'
import { useTerminal } from '../composables/useTerminal'
import { useSettingsStore } from '../stores/settings'
import TmuxNavigator from './TmuxNavigator.vue'

const settingsStore = useSettingsStore()

const props = defineProps({
    sessionId: {
        type: String,
        default: null,
    },
    active: {
        type: Boolean,
        default: false,
    },
})

const {
    containerRef, isConnected, started, start, reconnect, sendInput, focusTerminal, copyMode,
    windows, presets, showNavigator, listWindows, createWindow, selectWindow, toggleNavigator,
} = useTerminal(props.sessionId)

const activeWindowName = computed(() => windows.value.find(w => w.active)?.name ?? '')

// Lazy init: start the terminal only when the tab becomes active for the first time
watch(
    () => props.active,
    (active) => {
        if (active && !started.value) {
            start()
        }
    },
    { immediate: true },
)

function handleNavigatorSelect(name) {
    selectWindow(name)
}

function handleNavigatorCreate(nameOrPreset) {
    createWindow(nameOrPreset)
    // Backend responds with updated windows list automatically
}

// Fetch window list when navigator is shown
watch(showNavigator, (show) => {
    if (show) {
        listWindows()
    }
})

// Expose toggleNavigator for parent component (SessionView tab re-click)
defineExpose({ toggleNavigator })
</script>

<template>
    <div class="terminal-panel">
        <!-- Mobile toolbar — dropdown for window switching + control buttons -->
        <div v-if="settingsStore.isTouchDevice && started && !showNavigator" class="mobile-toolbar">
            <wa-select
                v-if="windows.length > 1"
                :value.prop="activeWindowName"
                size="small"
                class="window-select"
                @change="selectWindow($event.target.value); focusTerminal()"
            >
                <wa-option
                    v-for="win in windows"
                    :key="win.name"
                    :value="win.name"
                >{{ win.name }}</wa-option>
            </wa-select>
            <span class="toolbar-spacer"></span>
            <wa-button
                size="small"
                :appearance="copyMode ? 'filled' : 'plain'"
                :variant="copyMode ? 'brand' : 'neutral'"
                @click="copyMode = !copyMode"
            >
                <wa-icon slot="start" name="copy"></wa-icon>
                Copy
            </wa-button>
            <wa-button size="small" appearance="plain" variant="neutral" @click="sendInput('\x03')">
                Ctrl+C
            </wa-button>
            <wa-button size="small" appearance="plain" variant="neutral" @click="sendInput('\x1a')">
                Ctrl+Z
            </wa-button>
        </div>

        <!-- Desktop window tab bar — shown when multiple windows exist -->
        <div v-else-if="windows.length > 1 && started && !showNavigator" class="window-tabs">
            <button
                v-for="win in windows"
                :key="win.name"
                class="window-tab"
                :class="{ active: win.active }"
                @click="selectWindow(win.name); focusTerminal()"
            >
                {{ win.name }}
            </button>
        </div>

        <!-- Terminal xterm.js container — hidden when navigator is shown -->
        <div ref="containerRef" class="terminal-container" :class="{ hidden: showNavigator }"></div>

        <!-- Tmux Navigator overlay -->
        <TmuxNavigator
            v-if="showNavigator"
            :windows="windows"
            :presets="presets"
            @select="handleNavigatorSelect"
            @create="handleNavigatorCreate"
        />

        <!-- Disconnect overlay -->
        <div v-if="started && !isConnected && !showNavigator" class="disconnect-overlay">
            <wa-callout variant="warning" appearance="outlined">
                <wa-icon slot="icon" name="plug-circle-xmark"></wa-icon>
                <div class="disconnect-content">
                    <div>Terminal disconnected</div>
                    <wa-button
                        variant="warning"
                        appearance="outlined"
                        size="small"
                        @click="reconnect"
                    >
                        <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                        Reconnect
                    </wa-button>
                </div>
            </wa-callout>
        </div>
    </div>
</template>

<style scoped>
.terminal-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
    position: relative;
}

.window-tabs {
    display: flex;
    align-items: stretch;
    gap: var(--wa-space-3xs);
    flex-shrink: 0;
    overflow-x: auto;
    padding: 0 var(--wa-space-xs);
    border-bottom: 1px solid var(--wa-color-border-default);
    background: var(--wa-color-surface-alt);
}

.window-tab {
    appearance: none;
    padding: var(--wa-space-xs) var(--wa-space-m);
    margin: 0;
    background: transparent;
    border: none;
    border-radius: 0;
    border-bottom: 2px solid transparent;
    color: var(--wa-color-text-subtle);
    font-size: var(--wa-font-size-s);
    font-family: inherit;
    cursor: pointer;
    white-space: nowrap;
    transition: color 0.15s, border-color 0.15s;
}

.window-tab:hover {
    color: var(--wa-color-text-default);
}

.window-tab.active {
    color: var(--wa-color-brand-600);
    border-bottom-color: var(--wa-color-brand-600);
}

.mobile-toolbar {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    flex-shrink: 0;
    border-bottom: 1px solid var(--wa-color-border-default);
}

.window-select {
    min-width: 0;
    max-width: 10rem;
}

.toolbar-spacer {
    flex: 1;
}

.terminal-container {
    flex: 1;
    min-height: 0;
    width: 100%;
    padding: var(--wa-space-2xs);
}

.terminal-container.hidden {
    display: none;
}

/* Ensure xterm fills its container */
.terminal-container :deep(.xterm) {
    height: 100%;
}

.terminal-container :deep(.xterm-viewport) {
    overflow-y: auto !important;
}

.disconnect-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.4);
    z-index: 10;
}

.disconnect-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    text-align: center;
}
</style>
