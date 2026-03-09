<script setup>
import { ref, computed, watch } from 'vue'
import { useTerminal } from '../composables/useTerminal'
import { useSettingsStore } from '../stores/settings'
import TmuxNavigator from './TmuxNavigator.vue'
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
    containerRef, isConnected, started, start, reconnect, sendInput, focusTerminal,
    copyMode, windows, presets, paneAlternate, showNavigator,
    listWindows, createWindow, selectWindow, toggleNavigator,
    showConfig, toggleConfig,
} = useTerminal(props.sessionId)

const activeWindowName = computed(() => windows.value.find(w => w.active)?.name ?? '')
/** Flatten all preset names across all sources for tab bar icons + mobile dropdown. */
const presetNames = computed(() => {
    const names = new Set()
    for (const source of presets.value) {
        for (const p of source.presets || []) {
            names.add(p.name)
        }
    }
    return names
})

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

defineExpose({ toggleConfig, toggleNavigator, showConfig, showNavigator })

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

// ── Navigator ────────────────────────────────────────────────────────

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

// ── Shortcut config dialog ───────────────────────────────────────────

const configDialogRef = ref(null)
const editingSlotIndex = ref(0)
const editingShortcut = computed(() => settingsStore.getTerminalShortcuts[editingSlotIndex.value] || { label: '', sequence: '', showOnDesktop: false })

function openConfigDialog(index) {
    editingSlotIndex.value = index
    const shortcut = settingsStore.getTerminalShortcuts[index] || { label: '', sequence: '', showOnDesktop: false }
    configDialogRef.value?.open(shortcut)
}

function onConfigSave(shortcut) {
    settingsStore.setTerminalShortcut(editingSlotIndex.value, shortcut)
}

function onConfigClose() {
    // Dialog closed — nothing to do
}
</script>

<template>
    <div class="terminal-panel">
        <!-- Mobile toolbar — dropdown for window switching + shortcut buttons -->
        <div v-if="settingsStore.isTouchDevice && started && !showNavigator && !showConfig" class="mobile-toolbar">
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
                ><wa-icon v-if="presetNames.has(win.name)" name="circle-play" class="option-preset-icon"></wa-icon>{{ win.name }}</wa-option>
            </wa-select>
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

        <!-- Desktop: shortcut buttons toolbar + window tab bar -->
        <template v-else-if="!settingsStore.isTouchDevice && started && !showNavigator && !showConfig">
            <!-- Shortcut buttons toolbar (desktop only, when showOnDesktop) -->
            <div v-if="visibleShortcuts.length > 0" class="shortcut-toolbar">
                <wa-button
                    v-for="shortcut in visibleShortcuts"
                    :key="shortcut.index"
                    size="small"
                    appearance="outlined"
                    variant="neutral"
                    @click="executeShortcut(shortcut)"
                >{{ shortcut.label }}</wa-button>
            </div>

            <!-- Window tab bar — shown when multiple windows exist -->
            <div v-if="windows.length > 1" class="window-tabs">
                <button
                    v-for="win in windows"
                    :key="win.name"
                    class="window-tab"
                    :class="{ active: win.active }"
                    @click="selectWindow(win.name); focusTerminal()"
                >
                    <wa-icon v-if="presetNames.has(win.name)" name="circle-play" class="tab-preset-icon"></wa-icon>
                    {{ win.name }}
                </button>
            </div>
        </template>

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

        <!-- Terminal xterm.js container — hidden when navigator or config is shown -->
        <div ref="containerRef" class="terminal-container" :class="{ hidden: showNavigator || showConfig }"></div>

        <!-- Tmux Navigator overlay -->
        <TmuxNavigator
            v-if="showNavigator"
            :windows="windows"
            :preset-sources="presets"
            @select="handleNavigatorSelect"
            @create="handleNavigatorCreate"
        />

        <!-- Disconnect overlay -->
        <div v-if="started && !isConnected && !showNavigator && !showConfig" class="disconnect-overlay">
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

/* ── Window tab bar (desktop) ─────────────────────────────────────── */

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

.tab-preset-icon {
    font-size: 0.75em;
    margin-right: 0.25em;
}

.option-preset-icon {
    font-size: 0.85em;
    margin-right: 0.25em;
    vertical-align: -0.1em;
}

/* ── Toolbars ─────────────────────────────────────────────────────── */

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
