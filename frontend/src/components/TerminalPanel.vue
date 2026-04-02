<script setup>
import { computed, ref, watch } from 'vue'
import { useTerminal } from '../composables/useTerminal'
import { useSettingsStore } from '../stores/settings'
import { useDataStore } from '../stores/data'
import { useTerminalConfigStore } from '../stores/terminalConfig'
import AppTooltip from './AppTooltip.vue'
import ExtraKeysBar from './ExtraKeysBar.vue'
import ManageCombosDialog from './ManageCombosDialog.vue'
import ManageSnippetsDialog from './ManageSnippetsDialog.vue'

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

const settingsStore = useSettingsStore()
const dataStore = useDataStore()
const terminalConfigStore = useTerminalConfigStore()

const {
    containerRef, isConnected, started, start, reconnect, disconnect,
    touchMode, hasSelection, copySelection,
    paneAlternate,
    canScrollUp, canScrollDown,
    scrollToEdge, scrollingToEdge, cancelScrollToEdge,
    activeModifiers, lockedModifiers,
    handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
    handleComboPress, handleSnippetPress,
} = useTerminal(props.sessionId)

function handleTouchModeChange(event) {
    touchMode.value = event.target.checked ? 'select' : 'scroll'
}

// Resolve projectId from sessionId
const session = computed(() => props.sessionId ? dataStore.getSession(props.sessionId) : null)
const projectId = computed(() => session.value?.project_id)

// Snippets for the current project (global + project-specific, merged)
const snippetsForProject = computed(() =>
    projectId.value ? terminalConfigStore.getSnippetsForProject(projectId.value) : []
)

// Dialog refs
const manageCombosDialogRef = ref(null)
const manageSnippetsDialogRef = ref(null)

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
        <div class="terminal-actions-bar">
            <span class="push-right"></span>

            <template v-if="isConnected">
                <!-- Scroll to edge buttons (shown only when scrolled away from edge,
                     or always in alternate screen where position is unknown) -->
                <wa-button
                    v-if="canScrollUp || paneAlternate"
                    id="terminal-scroll-top-button"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="scroll-edge-button"
                    :loading="scrollingToEdge"
                    @click="scrollingToEdge ? cancelScrollToEdge() : scrollToEdge('top')"
                >
                    <wa-icon name="angles-up"></wa-icon>
                </wa-button>
                <AppTooltip v-if="canScrollUp || paneAlternate" for="terminal-scroll-top-button">Scroll to top</AppTooltip>

                <wa-button
                    v-if="canScrollDown || paneAlternate"
                    id="terminal-scroll-bottom-button"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="scroll-edge-button"
                    :loading="scrollingToEdge"
                    @click="scrollingToEdge ? cancelScrollToEdge() : scrollToEdge('bottom')"
                >
                    <wa-icon name="angles-down"></wa-icon>
                </wa-button>
                <AppTooltip v-if="canScrollDown || paneAlternate" for="terminal-scroll-bottom-button">Scroll to bottom</AppTooltip>

                <!-- Mobile-only controls -->
                <template v-if="settingsStore.isTouchDevice">
                    <div class="touch-mode-group">
                        <span
                            class="touch-mode-label"
                            @click="touchMode = touchMode === 'scroll' ? 'select' : 'scroll'"
                        >Scroll</span>
                        <wa-switch
                            size="small"
                            class="touch-mode-switch"
                            :checked="touchMode === 'select'"
                            @change="handleTouchModeChange"
                        >Select</wa-switch>
                    </div>

                    <Transition name="copy-fade">
                        <wa-button
                            v-if="hasSelection"
                            variant="primary"
                            appearance="filled"
                            size="small"
                            class="copy-button"
                            @click="copySelection"
                        >
                            <wa-icon slot="start" name="copy" variant="regular"></wa-icon>
                            Copy
                        </wa-button>
                    </Transition>
                </template>

                <wa-button
                    id="terminal-disconnect-button"
                    variant="danger"
                    appearance="filled"
                    size="small"
                    class="disconnect-button"
                    @click="disconnect"
                >
                    <wa-icon name="ban" label="Disconnect"></wa-icon>
                </wa-button>
                <AppTooltip for="terminal-disconnect-button">Disconnect terminal session</AppTooltip>
            </template>
        </div>
        <div class="terminal-area">
            <div ref="containerRef" class="terminal-container"></div>

            <!-- Disconnect overlay (only covers terminal area, not ExtraKeysBar) -->
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

        <ExtraKeysBar
            :active-modifiers="activeModifiers"
            :locked-modifiers="lockedModifiers"
            :is-touch-device="settingsStore.isTouchDevice"
            :combos="terminalConfigStore.combos"
            :snippets="snippetsForProject"
            @key-input="handleExtraKeyInput"
            @modifier-toggle="handleExtraKeyModifierToggle"
            @paste="handleExtraKeyPaste"
            @combo-press="handleComboPress"
            @snippet-press="handleSnippetPress"
            @manage-combos="manageCombosDialogRef?.open()"
            @manage-snippets="manageSnippetsDialogRef?.open()"
        />

        <ManageCombosDialog ref="manageCombosDialogRef" />
        <ManageSnippetsDialog
            ref="manageSnippetsDialogRef"
            :current-project-id="projectId"
        />
    </div>
</template>

<style scoped>
.terminal-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
}

.terminal-actions-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: start;
    gap: var(--wa-space-m);
    padding: var(--wa-space-2xs) var(--wa-space-s);
    border-bottom: 1px solid var(--wa-color-surface-border);
    flex-shrink: 0;
    min-height: 2rem;
}

.scroll-edge-button {
    opacity: 0.5;
    transition: opacity 0.15s;
    flex-shrink: 0;
    font-size: var(--wa-font-size-3xs);
    &::part(label) {
        scale: 1.5;
    }
}

.scroll-edge-button:hover {
    opacity: 1;
}

.push-right {
    margin-inline-start: auto;
}

.disconnect-button {
    opacity: 0.6;
    transition: opacity 0.15s;
    flex-shrink: 0;
    font-size: var(--wa-font-size-3xs);
    &::part(label) {
        scale: 1.5;
    }
}

.disconnect-button:hover:not([disabled]) {
    opacity: 1;
}

.touch-mode-group {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

.touch-mode-label {
    cursor: pointer;
    font-size: var(--wa-font-size-s);
    user-select: none;
}

.copy-button {
    flex-shrink: 0;
}

.copy-button::part(base) {
    --wa-form-control-padding-block: .5em;
    /* Had to force it here, but the same as default one: */
    --wa-form-control-height: round( calc(2 * var(--wa-form-control-padding-block) + 1em * var(--wa-form-control-value-line-height)), 1px );
}

/* Copy button enter/leave transition */
.copy-fade-enter-active,
.copy-fade-leave-active {
    transition: opacity 0.15s ease, transform 0.15s ease;
}

.copy-fade-enter-from,
.copy-fade-leave-to {
    opacity: 0;
    transform: scale(0.9);
}

.terminal-area {
    flex: 1;
    min-height: 0;
    position: relative;
}

.terminal-container {
    height: 100%;
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
