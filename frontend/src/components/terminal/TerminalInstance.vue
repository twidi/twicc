<script setup>
import { inject, nextTick, onUnmounted, ref, watch } from 'vue'
import { useTerminal } from '../../composables/useTerminal'
import { useTerminalPoolStore } from '../../stores/terminalPool'

const emit = defineEmits(['disconnected'])

const props = defineProps({
    contextKey: {
        type: String,
        required: true,
    },
    sessionId: {
        type: String,
        default: null,
    },
    projectId: {
        type: String,
        default: null,
    },
    cwd: {
        type: String,
        default: null,
    },
    terminalIndex: {
        type: Number,
        default: 0,
    },
    // Pool key (`${contextKey}#${index}`) under which this instance registers its
    // API in the terminal pool store, so the displaying panel's toolbar can reach
    // it across the <Teleport> boundary (provide/inject can't). When null, the
    // instance is rendered inline (e.g. the hybrid CLI block) and falls back to
    // the legacy provide/inject API registry of its parent.
    poolKey: {
        type: String,
        default: null,
    },
    active: {
        type: Boolean,
        default: false,
    },
    // How this instance decides to connect when it becomes active:
    //   'auto'    → connect (index > 0, or the Main when a tmux session already exists → attach);
    //   'manual'  → don't connect; show a Start callout (the Main with nothing to attach to, so merely
    //               displaying the terminal never auto-creates tmux);
    //   'pending' → the existence check hasn't resolved yet — do nothing until it does.
    startMode: {
        type: String,
        default: 'auto',
    },
})

const {
    containerRef, isConnected, isReady, started, ptyExited, start, reconnect, disconnect, focus,
    touchMode, hasSelection, copySelection, getSelectionText,
    paneAlternate,
    canScrollUp, canScrollDown,
    scrollToEdge, scrollingToEdge, cancelScrollToEdge,
    activeModifiers, lockedModifiers,
    handleExtraKeyInput, handleExtraKeyModifierToggle, handleExtraKeyPaste,
    handleComboPress, handleSnippetPress,
} = useTerminal(props.contextKey, props.terminalIndex, {
    sessionId: props.sessionId,
    projectId: props.projectId,
    cwd: props.cwd,
})

// Overlay action buttons — only one is ever rendered at a time (v-if/v-else-if), so the presence of a
// ref tells focusContent() which target is current without re-deriving the overlay conditions.
const startBtnRef = ref(null)
const reconnectBtnRef = ref(null)

function elHoldsFocus(el) {
    // wa-button retargets focus into its shadow DOM, so the host stays document.activeElement.
    return !!el && (document.activeElement === el || el.shadowRoot?.activeElement != null)
}

/**
 * Focus this instance's primary content for a tab / sub-tab activation: the visible overlay's action
 * button (Reconnect when disconnected, Start when not started) so it can be triggered from the keyboard,
 * otherwise the live terminal so the user types straight away. Returns whether focus now holds — drives
 * the parent's focus-retry pump (re-evaluated each frame, so it auto-corrects as the state transitions,
 * e.g. the overlay clearing once connected).
 */
function focusContent() {
    const overlayBtn = reconnectBtnRef.value || startBtnRef.value
    if (overlayBtn) {
        if (!elHoldsFocus(overlayBtn)) {
            try {
                overlayBtn.focus()
            } catch {
                // wa-button.focus() can throw if its shadow DOM isn't upgraded yet. Benign — the pump retries.
            }
        }
        return elHoldsFocus(overlayBtn)
    }
    focus()
    return !!containerRef.value && containerRef.value.contains(document.activeElement)
}

// Register terminal API so the displaying panel's toolbar + ExtraKeysBar can
// route to it. Pooled instances (poolKey set) are teleported out of the panel's
// component tree, so they publish to the app-level pool store; inline instances
// (no poolKey, e.g. the hybrid block) use the legacy provide/inject of a parent.
const poolStore = useTerminalPoolStore()
const registerTerminal = inject('registerTerminal', null)
const unregisterTerminal = inject('unregisterTerminal', null)

const terminalApi = {
    // ExtraKeysBar handlers
    activeModifiers,
    lockedModifiers,
    handleExtraKeyInput,
    handleExtraKeyModifierToggle,
    handleExtraKeyPaste,
    handleComboPress,
    handleSnippetPress,
    // Toolbar state (refs)
    isConnected,
    isReady,
    started,
    ptyExited,
    canScrollUp,
    canScrollDown,
    paneAlternate,
    scrollingToEdge,
    hasSelection,
    touchMode,
    // Toolbar actions (functions)
    scrollToEdge,
    cancelScrollToEdge,
    copySelection,
    getSelectionText,
    disconnect,
    reconnect,
    focus,
    focusContent,
}
if (props.poolKey) {
    poolStore.registerApi(props.poolKey, terminalApi)
} else {
    registerTerminal?.(props.terminalIndex, terminalApi)
}
onUnmounted(() => {
    if (props.poolKey) poolStore.unregisterApi(props.poolKey)
    else unregisterTerminal?.(props.terminalIndex)
})

// Notify parent when the terminal's PTY exits (Ctrl+D, `exit`, shell crash, etc.)
// Only emits when the backend explicitly signals pty_exited — NOT on network disconnects
// (where the user should see a reconnect overlay instead).
// Vue stops watchers during unmount, so this does NOT fire when the component is
// destroyed by removeTerminalTab — only when the WS closes while the component is alive.
watch(isConnected, (connected, wasConnected) => {
    if (wasConnected && !connected && ptyExited.value) {
        emit('disconnected')
    }
})

// Lazy init: connect the first time the tab is active with startMode 'auto' (a regular sub-tab, or the
// Main when a tmux session already exists → attach). A 'manual' Main waits for the explicit Start button
// below; 'pending' waits for the existence check to resolve. Re-runs when startMode flips (pending→auto
// attaches as soon as discovery confirms a session; pending→manual reveals the Start callout).
watch(
    [() => props.active, () => props.startMode],
    ([active, mode]) => {
        if (active && !started.value && mode === 'auto') {
            start()
        }
    },
    { immediate: true },
)

// When the user clicks Start / Reconnect, move focus into the terminal so they
// can type as soon as the shell is ready. The overlay button holds focus on
// click; without this it would fall back to <body> when the overlay is removed.
// A single focus() is enough: once xterm's hidden textarea has focus, nothing
// steals it back (the "Connected." writeln and the overlay removal don't blur),
// so it persists through connect → ready.
function handleStartClick() {
    start()
    nextTick(() => focus())
}

function handleReconnectClick() {
    reconnect()
    nextTick(() => focus())
}
</script>

<template>
    <div class="terminal-area">
        <div ref="containerRef" class="terminal-container"></div>

        <!-- Disconnect overlay (only covers terminal area, not ExtraKeysBar) -->
        <div v-if="started && !isConnected" class="terminal-overlay">
            <wa-callout variant="warning">
                <wa-icon slot="icon" name="plug-circle-xmark"></wa-icon>
                <div class="terminal-overlay-content">
                    <div>Terminal disconnected</div>
                    <wa-button
                        ref="reconnectBtnRef"
                        variant="warning"
                        appearance="outlined"
                        size="small"
                        @click="handleReconnectClick"
                    >
                        <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                        Reconnect
                    </wa-button>
                </div>
            </wa-callout>
        </div>

        <!-- Start overlay: the Main terminal with no tmux session to attach to waits for an explicit
             start, so merely viewing (e.g. a docked-by-default terminal) never spawns tmux. -->
        <div v-else-if="!started && active && startMode === 'manual'" class="terminal-overlay">
            <wa-callout variant="neutral">
                <wa-icon slot="icon" name="terminal"></wa-icon>
                <div class="terminal-overlay-content">
                    <div>Terminal not started</div>
                    <wa-button
                        ref="startBtnRef"
                        variant="brand"
                        appearance="outlined"
                        size="small"
                        @click="handleStartClick"
                    >
                        <wa-icon slot="start" name="play"></wa-icon>
                        Start terminal
                    </wa-button>
                </div>
            </wa-callout>
        </div>
    </div>
</template>

<style scoped>
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

.terminal-overlay {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.4);
    z-index: 10;
}

/* filled-outlined keeps the callout opaque over the terminal content; restore the
   stronger border the plain outlined appearance gave it. */
.terminal-overlay-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    text-align: center;
}
</style>
