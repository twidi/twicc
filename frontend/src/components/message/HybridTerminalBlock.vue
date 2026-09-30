<script setup>
// HybridTerminalBlock.vue - Embedded terminal for hybrid CLI sessions.
//
// Sits in the session footer, above the message input. Shows the live claude
// TUI of the session's dedicated tmux session (attach-only 'h:' terminal
// context). CLOSED by default — nothing visible, but the xterm stays mounted
// so its buffer survives. Opened on demand: the composer's hybrid icon
// (committed sessions) or the attention callout below. Two open sizes: normal
// (capped height) and maximized (fills the session area).
//
// When closed AND the TUI needs the user directly — a prompt only it can show
// ('hybrid_terminal', GUI channel expired) or a blocked composer — a warning
// callout takes the block's place, carrying the reason and opening the terminal
// on click. Answerable requests never trigger it: they render the
// PendingRequestForm above the composer (first responder wins).
import { ref, computed, watch, watchEffect, useId, provide, onBeforeUnmount, markRaw, nextTick } from 'vue'
import { useDataStore } from '../../stores/data'
import { useFooterBlockMotion } from '../../composables/useFooterMotion.js'
import AppTooltip from '../ui/AppTooltip.vue'
import TerminalInstance from '../terminal/TerminalInstance.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    }
})

const emit = defineEmits(['request-open', 'request-collapse', 'show-pending-form', 'state-change'])

const store = useDataStore()

// ── Open/closed state (single enum) ─────────────────────────────────────────
// Closed by default: the composer keeps the room and the CLI is steered from
// it without the terminal on screen. Safe for sizing — while closed the CLI's
// 80-column width comes from the backend tmux session's default size (created
// detached at 80x24), not the embedded xterm. Opening fits it to the panel: a
// wide panel widens the view, a narrow one shrinks the font first and only then
// narrows the columns, down to the HYBRID_MIN_COLS floor.
const viewState = ref('closed') // 'closed' | 'normal' | 'maximized'
const isClosed = computed(() => viewState.value === 'closed')
const isMaximized = computed(() => viewState.value === 'maximized')
const closeBtnId = useId()
const maximizeToggleId = useId()
const refreshBtnId = useId()

// Imperative open/close, driven by the footer accordion (SessionItemsList). No
// emit, so the accordion's sync never loops back into a request.
function close() {
    viewState.value = 'closed'
}
function open() {
    if (viewState.value !== 'closed') return
    viewState.value = 'normal'
}
function toggleMaximized() {
    viewState.value = isMaximized.value ? 'normal' : 'maximized'
}
// Driven from the composer's hybrid icon (open) and internally (close);
// requestFocus moves keyboard focus into the xterm once the block is open.
defineExpose({ open, close, requestFocus })

// ── Manual terminal refresh ──────────────────────────────────────────────────
// A purely front-side xterm.js freeze (renderer stuck, or a zombie WebSocket
// that still reads as connected so reconnect() would no-op) leaves the CLI
// running fine in the backend tmux but frozen/blank in the UI. Remounting the
// TerminalInstance through this key is the reliable cure: Vue disposes the old
// xterm and closes its socket, then a fresh instance mounts, refits and
// re-attaches to the same tmux session — which repaints the whole TUI.
const terminalRefreshKey = ref(0)
function refresh() {
    terminalRefreshKey.value++
}

// ── Process / terminal lifecycle ─────────────────────────────────────────────
// The CLI is launched lazily at the first send: before that there is no tmux
// session to attach to, so show a placeholder. Once a process exists, keep
// the terminal mounted while it lives — and drop back to the placeholder
// when the session is STOPPED (see the watchEffect below): the tmux session
// is killed along with the CLI, so the "Terminal disconnected / Reconnect"
// overlay would offer a reconnect to nothing.
const processState = computed(() => store.getProcessState(props.sessionId))
const hasProcess = computed(() => !!processState.value)
const everHadProcess = ref(false)
watch(hasProcess, (v) => { if (v) everHadProcess.value = true }, { immediate: true })

// Capture the embedded TerminalInstance's API (it registers itself through
// the same provide/inject contract as the Terminal panel) so we can drive
// reconnects when the CLI is relaunched.
// markRaw is REQUIRED: storing the api object in a ref would wrap it in a
// reactive proxy that auto-unwraps its nested refs — ``api.started.value``
// would then read ``.value`` on a plain boolean and silently yield
// undefined, breaking both watchers below. Reading the kept-intact refs
// inside watch/watchEffect still tracks them.
const termApi = ref(null)
provide('registerTerminal', (_index, api) => { termApi.value = markRaw(api) })
provide('unregisterTerminal', () => { termApi.value = null })

// Auto-reconnect: when a process (re)appears while the terminal sits
// disconnected, retry attaching a few times — the tmux session is created
// just AFTER the STARTING broadcast, so the first attempt can race it.
let reconnectTimers = []
function clearReconnectTimers() {
    reconnectTimers.forEach(clearTimeout)
    reconnectTimers = []
}
watch(processState, (state, oldState) => {
    if (!state || oldState) return
    clearReconnectTimers()
    for (const delay of [800, 2500, 6000]) {
        reconnectTimers.push(setTimeout(() => {
            const api = termApi.value
            if (!api) return
            if (!hasProcess.value) return
            if (api.started.value && !api.isConnected.value) api.reconnect()
        }, delay))
    }
})
onBeforeUnmount(clearReconnectTimers)

// Session stopped → back to the placeholder. Requires BOTH signals: the
// process is gone AND the attach explicitly exited (pty_exited is never set
// on network cuts, where the tmux may well be alive and the reconnect
// overlay is the right UI). A watchEffect covers both event orders —
// pty_exited can land before or after the process_state dead broadcast.
watchEffect(() => {
    if (!everHadProcess.value || hasProcess.value) return
    const api = termApi.value
    if (api && api.started.value && !api.isConnected.value && api.ptyExited.value) {
        everHadProcess.value = false
    }
})

// ── Focus the embedded terminal on (accordion-driven) open ───────────────────
// Order-independent, like the composer's requestFocus: if the block is still
// closed when asked, defer until the open watch flips it; otherwise focus now.
// The first open lazily starts the xterm (active:false → true), so termApi may
// not be ready for a tick — poll briefly. No-op while in the placeholder state
// (no process yet, nothing to focus).
let wantsFocus = false
function focusTerminalNow() {
    if (!everHadProcess.value) return
    let tries = 12
    const attempt = () => {
        const api = termApi.value
        if (api?.started?.value) {
            api.focus?.()
            // Cross-tab: the chat panel may still be hidden on the first try, so
            // focus() is a no-op until the xterm is visible — retry until focus
            // actually lands inside it.
            if (document.activeElement?.closest?.('.xterm')) return
        }
        if (tries-- > 0) setTimeout(attempt, 40)
    }
    nextTick(attempt)
}
function requestFocus() {
    if (isClosed.value) wantsFocus = true
    else focusTerminalNow()
}
watch(isClosed, (closed) => {
    if (!closed && wantsFocus) {
        wantsFocus = false
        focusTerminalNow()
    }
})

// ── Attention: the terminal is the ONLY answer surface ───────────────────────
// A pending request degraded to badge-only ('hybrid_terminal', GUI channel
// expired) or a blocked composer (process ``extra.terminal_blocked``: TwiCC
// tried to paste and a TUI dialog was open or text was typed in). Answerable
// pendings are excluded — they render the PendingRequestForm above the composer.
const hybridPending = computed(() => (store.getPendingRequests(props.sessionId) || [])[0] || null)
const pendingIsAnswerable = computed(() =>
    !!hybridPending.value && hybridPending.value.request_type !== 'hybrid_terminal'
)
const pendingTerminalOnly = computed(() => !!hybridPending.value && !pendingIsAnswerable.value)
const terminalBlocked = computed(() => !!processState.value?.extra?.terminal_blocked)
const attentionTerminalOnly = computed(() => pendingTerminalOnly.value || terminalBlocked.value)
// Same wording the minimized bar used to carry, promoted to the callout.
const attentionLabel = computed(() =>
    pendingTerminalOnly.value ? 'Answer in the terminal' : 'Terminal blocked — click here to open'
)

// Open-header badge: while the terminal IS open, keep the same context — steer
// an answerable request to the UI form, or note a block. Quiet (no pulse): the
// terminal is already on screen.
const headerBadgeLabel = computed(() => {
    if (hybridPending.value) return pendingIsAnswerable.value ? 'Prefer answering in the UI' : 'Answer in the terminal'
    if (terminalBlocked.value) return 'Terminal blocked'
    return ''
})
// Clicking the answerable badge brings the form back if the user minimized it,
// and closes the terminal: the user chose the form as their surface.
function onHeaderBadgeClick() {
    if (!pendingIsAnswerable.value) return
    emit('show-pending-form')
    close()
}

// ── Report visibility + attention to the parent ──────────────────────────────
// calloutShown: the closed-state warning callout is up → drives the composer's
// hybrid-icon tint. isVisible: the block occupies space (open, or the callout)
// → drives the composer's top separator.
const calloutShown = computed(() => isClosed.value && attentionTerminalOnly.value)
const isVisible = computed(() => !isClosed.value || attentionTerminalOnly.value)
watch([isVisible, calloutShown], () => {
    emit('state-change', { visible: isVisible.value, attention: calloutShown.value })
}, { immediate: true })

// ── Footer motion (visual refresh step 7d) ───────────────────────────────────
// The single root animates its height on open / close and when the callout shows or
// goes (closed with no callout is a zero height), the block its inset on maximize /
// restore. The xterm refits on its own (useTerminal's resize debounce).
const wrapperRef = ref(null)
const blockRef = ref(null)
const footerShape = computed(() => isClosed.value ? (calloutShown.value ? 'callout' : 'closed') : 'open')
useFooterBlockMotion({
    wrapperRef,
    blockRef,
    shape: footerShape,
    maximized: isMaximized,
})
</script>

<template>
    <div ref="wrapperRef" class="footer-block">
    <wa-divider v-if="isVisible"></wa-divider>
    <div ref="blockRef" class="hybrid-terminal-block" :class="{ maximized: isMaximized, closed: isClosed, attention: calloutShown }">
        <!-- Closed + attention: a warning callout takes the block's place, opens
             the terminal on click. -->
        <wa-callout
            v-if="calloutShown"
            variant="warning"
            size="small"
            class="hybrid-attention-callout"
            role="button"
            tabindex="0"
            @click="$emit('request-open')"
            @keydown.enter="$emit('request-open')"
        >
            <wa-icon slot="icon" name="triangle-exclamation" variant="classic"></wa-icon>
            {{ attentionLabel }}
        </wa-callout>
        <!-- Open: header with window controls -->
        <div v-else-if="!isClosed" class="hybrid-terminal-header">
            <wa-icon name="terminal" class="hybrid-terminal-icon"></wa-icon>
            <span class="hybrid-terminal-title">Claude CLI terminal</span>
            <span
                v-if="headerBadgeLabel"
                class="hybrid-pending-badge"
                :class="{ clickable: pendingIsAnswerable }"
                role="status"
                @click.stop="onHeaderBadgeClick"
            >{{ headerBadgeLabel }}</span>
            <wa-button
                v-if="everHadProcess"
                variant="neutral"
                appearance="plain"
                size="small"
                class="size-toggle-btn"
                :id="refreshBtnId"
                @click="refresh"
            >
                <wa-icon name="arrows-rotate" variant="classic"></wa-icon>
            </wa-button>
            <AppTooltip v-if="everHadProcess" :for="refreshBtnId">Refresh the terminal</AppTooltip>
            <wa-button
                variant="neutral"
                appearance="plain"
                size="small"
                class="size-toggle-btn"
                :id="closeBtnId"
                @click="$emit('request-collapse')"
            >
                <wa-icon name="window-minimize" variant="classic"></wa-icon>
            </wa-button>
            <AppTooltip :for="closeBtnId">Hide the terminal (the CLI keeps running)</AppTooltip>
            <wa-button
                variant="neutral"
                appearance="plain"
                size="small"
                class="size-toggle-btn"
                :id="maximizeToggleId"
                @click="toggleMaximized"
            >
                <wa-icon :name="isMaximized ? 'compress' : 'expand'" variant="classic"></wa-icon>
            </wa-button>
            <AppTooltip :for="maximizeToggleId">{{ isMaximized ? 'Restore' : 'Maximize' }}</AppTooltip>
        </div>

        <!-- Terminal area: placeholder until the CLI runs (and again after a
             stop — the tmux is gone, nothing to reconnect to), then the live
             TUI. Hidden by CSS while closed so the xterm state survives. -->
        <div class="hybrid-terminal-body">
            <div v-if="!everHadProcess" class="hybrid-terminal-placeholder">
                <wa-icon name="terminal"></wa-icon>
                <span>The Claude CLI starts here when you send a message.</span>
            </div>
            <TerminalInstance
                v-else
                :key="terminalRefreshKey"
                :context-key="'h:' + sessionId"
                :session-id="sessionId"
                :active="!isClosed"
            />
        </div>
    </div>
    </div>
</template>

<style scoped>
/* The single root (footer motion, step 7d): flow-root keeps a child's margin inside, so an
   animated height never clips it. */
.footer-block {
    display: flow-root;
}

wa-divider {
    --width: var(--divider-size);
    --spacing: 0;
}

.hybrid-terminal-block {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs) var(--wa-space-s);
    background: var(--wa-color-surface-default);
    height: 40dvh;
    &.maximized {
        height: unset;
        position: absolute;
        inset: 0;
        /* Above the composer (itself position: relative), below the
           drag/drop overlay at z-index 100. */
        z-index: 2;
    }
}

/* Closed: zero visible footprint. The body stays mounted (xterm buffer
   survives) but hidden. When a warning callout shows (closed + attention) the
   block reclaims a little padding to frame it. */
.hybrid-terminal-block.closed {
    padding: 0;
    gap: 0;
    height: auto;
}
.hybrid-terminal-block.closed.attention {
    padding: var(--wa-space-2xs) var(--wa-space-s);
}
.hybrid-terminal-block.closed > .hybrid-terminal-body {
    display: none;
}

.hybrid-attention-callout {
    cursor: pointer;
}

.hybrid-terminal-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex-shrink: 0;
}

.hybrid-terminal-icon {
    color: var(--wa-color-brand-fill-loud);
}

.hybrid-terminal-title {
    font-weight: var(--wa-font-weight-semibold);
    font-size: var(--wa-font-size-s);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.hybrid-pending-badge {
    flex-shrink: 0;
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-warning-on-quiet);
    background: var(--wa-color-warning-fill-quiet);
    border: 1px solid var(--wa-color-warning-border-quiet);
    border-radius: var(--wa-border-radius-pill);
    padding: 0 var(--wa-space-s);
    line-height: 1.6;
}

/* When the form is available, the badge brings it back if minimized. */
.hybrid-pending-badge.clickable {
    cursor: pointer;
}

.size-toggle-btn {
    flex-shrink: 0;
}

.hybrid-terminal-body {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.hybrid-terminal-placeholder {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    border: 1px dashed var(--wa-color-surface-border);
    border-radius: var(--wa-border-radius-m);
}
</style>
