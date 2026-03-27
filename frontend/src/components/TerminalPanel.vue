<script setup>
import { computed, ref, watch } from 'vue'
import { useTerminal } from '../composables/useTerminal'
import { useSettingsStore } from '../stores/settings'
import { useDataStore } from '../stores/data'
import { useTerminalConfigStore } from '../stores/terminalConfig'
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
    containerRef, isConnected, started, start, reconnect,
    activeModifiers, lockedModifiers,
    handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
    handleComboPress, handleSnippetPress,
} = useTerminal(props.sessionId)

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
