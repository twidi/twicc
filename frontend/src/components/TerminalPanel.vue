<script setup>
import { ref, computed, watch } from 'vue'
import { useTerminal } from '../composables/useTerminal'
import { useSettingsStore } from '../stores/settings'
import ShortcutConfigDialog from './ShortcutConfigDialog.vue'

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
    containerRef, isConnected, started, start, reconnect,
    sendInput, focusTerminal, copyMode, showConfig, toggleConfig,
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

defineExpose({ toggleConfig })

// ── Shortcut buttons ─────────────────────────────────────────────────

/** Visible shortcut buttons for the toolbar (non-empty, respecting device type) */
const visibleShortcuts = computed(() => {
    return settingsStore.getTerminalShortcuts
        .map((s, i) => ({ ...s, index: i }))
        .filter(s => s.label && s.sequence && (settingsStore.isTouchDevice || s.showOnDesktop))
})

function executeShortcut(shortcut) {
    sendInput(shortcut.sequence)
    focusTerminal()
}

// ── Shortcut config dialog ───────────────────────────────────────────

const configDialogRef = ref(null)
const editingSlotIndex = ref(0)
const editingShortcut = computed(() => settingsStore.getTerminalShortcuts[editingSlotIndex.value] || { label: '', sequence: '', showOnDesktop: false })

function openConfigDialog(index) {
    editingSlotIndex.value = index
    configDialogRef.value?.open()
}

function onConfigSave(shortcut) {
    settingsStore.setTerminalShortcut(editingSlotIndex.value, shortcut)
    // Close the dialog via the exposed ref
    const dialog = configDialogRef.value?.$refs?.dialogRef
    if (dialog) dialog.open = false
}

function onConfigClose() {
    // Dialog closed — nothing to do
}
</script>

<template>
    <div class="terminal-panel">
        <!-- Shortcut buttons toolbar (desktop if showOnDesktop, mobile always) -->
        <div v-if="started && !showConfig && (settingsStore.isTouchDevice || visibleShortcuts.length > 0)" class="shortcut-toolbar">
            <wa-button
                v-for="shortcut in visibleShortcuts"
                :key="shortcut.index"
                size="small"
                appearance="outlined"
                variant="neutral"
                @click="executeShortcut(shortcut)"
            >{{ shortcut.label }}</wa-button>
            <span class="toolbar-spacer"></span>
            <wa-button
                v-if="settingsStore.isTouchDevice"
                size="small"
                :appearance="copyMode ? 'filled' : 'plain'"
                :variant="copyMode ? 'brand' : 'neutral'"
                @click="copyMode = !copyMode"
            >
                <wa-icon slot="start" name="copy"></wa-icon>
                Copy
            </wa-button>
        </div>

        <!-- Config panel (toggled by re-clicking Terminal tab) -->
        <div v-if="showConfig && started" class="config-panel">
            <div class="config-header">
                <span class="config-title">Shortcut buttons</span>
            </div>
            <div class="config-slots">
                <button
                    v-for="(shortcut, index) in settingsStore.getTerminalShortcuts"
                    :key="index"
                    class="config-slot"
                    :class="{ empty: !shortcut.label }"
                    @click="openConfigDialog(index)"
                >
                    <template v-if="shortcut.label">
                        <span class="slot-label">{{ shortcut.label }}</span>
                        <wa-icon
                            v-if="shortcut.showOnDesktop"
                            name="display"
                            class="slot-desktop-icon"
                        ></wa-icon>
                    </template>
                    <template v-else>
                        <wa-icon name="circle-plus" class="slot-add-icon"></wa-icon>
                    </template>
                </button>
            </div>
        </div>

        <div ref="containerRef" class="terminal-container" :class="{ hidden: showConfig }"></div>

        <!-- Disconnect overlay -->
        <div v-if="started && !isConnected && !showConfig" class="disconnect-overlay">
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

        <!-- Config dialog -->
        <ShortcutConfigDialog
            ref="configDialogRef"
            :shortcut="editingShortcut"
            @save="onConfigSave"
            @close="onConfigClose"
        />
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

.shortcut-toolbar {
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

/* ── Config panel ──────────────────────────────────────────────────── */

.config-panel {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    padding: var(--wa-space-m);
    gap: var(--wa-space-m);
    overflow-y: auto;
}

.config-header {
    display: flex;
    align-items: center;
}

.config-title {
    font-size: var(--wa-font-size-m);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-default);
}

.config-slots {
    display: flex;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.config-slot {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-2xs);
    min-width: 5rem;
    padding: var(--wa-space-s) var(--wa-space-m);
    border: 2px solid var(--wa-color-border-default);
    border-radius: var(--wa-border-radius-m);
    background: var(--wa-color-surface-default);
    color: var(--wa-color-text-default);
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
}

.config-slot:hover {
    border-color: var(--wa-color-border-hover);
    background: var(--wa-color-surface-alt);
}

.config-slot.empty {
    border-style: dashed;
    color: var(--wa-color-text-subtle);
    font-weight: normal;
}

.slot-label {
    white-space: nowrap;
}

.slot-desktop-icon {
    font-size: 0.85em;
    opacity: 0.5;
}

.slot-add-icon {
    font-size: 1.2em;
    opacity: 0.5;
}

/* ── Disconnect overlay ──────────────────────────────────────────── */

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
