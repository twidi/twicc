<script setup>
import { watch } from 'vue'
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
    containerRef, isConnected, started, start, reconnect, sendInput,
    windows, showNavigator, listWindows, createWindow, selectWindow, toggleNavigator,
} = useTerminal(props.sessionId)

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

function handleNavigatorCreate(name) {
    createWindow(name)
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
        <!-- Mobile control toolbar -->
        <div v-if="settingsStore.isTouchDevice && started && !showNavigator" class="control-toolbar">
            <wa-button size="small" appearance="plain" variant="neutral" @click="sendInput('\x03')">
                Ctrl+C
            </wa-button>
            <wa-button size="small" appearance="plain" variant="neutral" @click="sendInput('\x1a')">
                Ctrl+Z
            </wa-button>
        </div>

        <!-- Terminal xterm.js container — hidden when navigator is shown -->
        <div ref="containerRef" class="terminal-container" :class="{ hidden: showNavigator }"></div>

        <!-- Tmux Navigator overlay -->
        <TmuxNavigator
            v-if="showNavigator"
            :windows="windows"
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

.control-toolbar {
    display: flex;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    flex-shrink: 0;
    border-bottom: 1px solid var(--wa-color-border-default);
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
