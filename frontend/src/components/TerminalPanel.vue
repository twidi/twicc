<script setup>
import { watch } from 'vue'
import { useTerminal } from '../composables/useTerminal'
import { useSettingsStore } from '../stores/settings'

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

const { containerRef, isConnected, started, start, reconnect, copyMode } = useTerminal(props.sessionId)

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
</script>

<template>
    <div class="terminal-panel">
        <!-- Mobile toolbar — copy mode toggle -->
        <div v-if="settingsStore.isTouchDevice && started" class="mobile-toolbar">
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
        </div>

        <div ref="containerRef" class="terminal-container"></div>

        <!-- Disconnect overlay -->
        <div v-if="started && !isConnected" class="disconnect-overlay">
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

.terminal-container {
    flex: 1;
    min-height: 0;
    width: 100%;
    padding: var(--wa-space-2xs);
}

/* Ensure xterm fills its container */
.terminal-container :deep(.xterm) {
    height: 100%;
}

.terminal-container :deep(.xterm-viewport) {
    overflow-y: auto !important;
}

.mobile-toolbar {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    flex-shrink: 0;
    border-bottom: 1px solid var(--wa-color-border-default);
}

.toolbar-spacer {
    flex: 1;
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
