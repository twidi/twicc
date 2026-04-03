<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, watch } from 'vue'
import { useSettingsStore } from '../stores/settings'
import { useDataStore } from '../stores/data'
import { useTerminalConfigStore } from '../stores/terminalConfig'
import { useTerminalTabsStore } from '../stores/terminalTabs'
import { sendWsMessage } from '../composables/useWebSocket'
import { toast } from '../composables/useToast'
import { getUnavailablePlaceholders } from '../utils/snippetPlaceholders'
import AppTooltip from './AppTooltip.vue'
import TerminalInstance from './TerminalInstance.vue'
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
const terminalTabsStore = useTerminalTabsStore()

// Resolve projectId from sessionId
const session = computed(() => props.sessionId ? dataStore.getSession(props.sessionId) : null)
const projectId = computed(() => session.value?.project_id)

// Snippets for the current project (global + project-specific, merged),
// enriched with placeholder availability info (_disabled / _disabledReason).
const snippetsForProject = computed(() => {
    const raw = projectId.value ? terminalConfigStore.getSnippetsForProject(projectId.value) : []
    const s = session.value
    const pid = projectId.value
    const project = pid ? dataStore.getProject(pid) : null
    const projectName = pid ? dataStore.getProjectDisplayName(pid) : null
    const ctx = { session: s, project, projectName }

    return raw.map(snippet => {
        const placeholders = snippet.placeholders || []
        if (placeholders.length === 0) return snippet
        const unavailable = getUnavailablePlaceholders(placeholders, ctx)
        if (unavailable.length === 0) return snippet
        return {
            ...snippet,
            _disabled: true,
            _disabledReason: `Not available: ${unavailable.map(p => p.label).join(', ')}`,
        }
    })
})

// Dialog refs
const manageCombosDialogRef = ref(null)
const manageSnippetsDialogRef = ref(null)

// Terminal instance registration (provide/inject for toolbar + ExtraKeysBar routing)
const terminalApis = reactive(new Map())
provide('registerTerminal', (index, api) => { terminalApis.set(index, api) })
provide('unregisterTerminal', (index) => { terminalApis.delete(index) })

// --- Terminal tab management ---
const terminals = ref([{ index: 0, label: 'Main' }])
const activeIndex = ref(0)
const nextIndex = ref(1) // monotonically increasing counter

const activeTabPanel = computed(() => `term-${activeIndex.value}`)
const activeApi = computed(() => terminalApis.get(activeIndex.value) || null)
const isActiveMain = computed(() => activeIndex.value === 0)

// Flattened toolbar state from the active terminal's API.
// Note: reactive Map's .get() wraps results in reactive(), which auto-unwraps
// refs — so activeApi.value.isConnected returns the boolean directly, not a Ref.
const tb = reactive({
    get isConnected() { return activeApi.value?.isConnected ?? false },
    get canScrollUp() { return activeApi.value?.canScrollUp ?? false },
    get canScrollDown() { return activeApi.value?.canScrollDown ?? false },
    get paneAlternate() { return activeApi.value?.paneAlternate ?? false },
    get scrollingToEdge() { return activeApi.value?.scrollingToEdge ?? false },
    get hasSelection() { return activeApi.value?.hasSelection ?? false },
    get touchMode() { return activeApi.value?.touchMode ?? 'scroll' },
})

function createTerminal() {
    const index = nextIndex.value++
    terminals.value.push({ index, label: `Term ${index + 1}` })
    activeIndex.value = index
}

/** Remove a terminal tab (idempotent — no-op if already removed). */
function removeTerminalTab(index) {
    if (index === 0) return // main terminal tab is permanent
    const idx = terminals.value.findIndex(t => t.index === index)
    if (idx === -1) return
    terminals.value.splice(idx, 1)
    if (activeIndex.value === index) {
        const prevTerminal = terminals.value[Math.max(0, idx - 1)]
        activeIndex.value = prevTerminal?.index ?? 0
    }
}

/** Kill a secondary terminal: send WS message to clean up tmux + remove tab. */
function killTerminal(index) {
    if (index === 0) return
    sendWsMessage({
        type: 'kill_terminal',
        session_id: props.sessionId,
        terminal_index: index,
    })
    removeTerminalTab(index)
}

/** Called when a TerminalInstance's WS disconnects (PTY died, Ctrl+D, network, etc.) */
function onTerminalDisconnected(index) {
    if (index === 0) return // main terminal: keep tab, show reconnect overlay
    removeTerminalTab(index)
}

function onTerminalTabShow(event) {
    const panelName = event.detail?.name
    if (panelName?.startsWith('term-')) {
        activeIndex.value = parseInt(panelName.slice(5), 10)
    }
}

// Focus the active terminal when switching tabs
watch(activeIndex, () => {
    nextTick(() => {
        activeApi.value?.focus?.()
    })
})

// --- Toolbar action helpers (delegate to activeApi) ---

function handleScrollToEdge(direction) {
    const api = activeApi.value
    if (!api) return
    api.scrollingToEdge ? api.cancelScrollToEdge() : api.scrollToEdge(direction)
}

function handleTouchModeChange(event) {
    const api = activeApi.value
    if (!api) return
    api.touchMode = event.target.checked ? 'select' : 'scroll'
}

function handleTouchModeToggle() {
    const api = activeApi.value
    if (!api) return
    api.touchMode = api.touchMode === 'scroll' ? 'select' : 'scroll'
}

function handleCopy() {
    activeApi.value?.copySelection?.()
}

function handlePaste() {
    activeApi.value?.handleExtraKeyPaste?.()
}

function handleDisconnect() {
    // Send Ctrl+D (EOF) to the active terminal. The shell exits naturally,
    // the backend sends pty_exited, and the tab is removed for secondary terminals.
    // For the main terminal, the tab stays with the reconnect overlay.
    activeApi.value?.disconnect?.()
}

// --- Discovery and cross-device sync ---

let discoveryDone = false

// When the panel first becomes active, request terminal list from backend
watch(
    () => props.active,
    (active) => {
        if (active && !discoveryDone) {
            discoveryDone = true
            sendWsMessage({
                type: 'list_terminals',
                session_id: props.sessionId,
            })
        }
    },
    { immediate: true },
)

// Watch the terminalTabsStore for backend terminal updates
watch(
    () => terminalTabsStore.indices[props.sessionId],
    (backendIndices, oldIndices) => {
        if (!backendIndices) return
        syncTerminalsFromBackend(backendIndices, oldIndices)
    },
)

function syncTerminalsFromBackend(backendIndices, oldIndices) {
    const localIndices = new Set(terminals.value.map(t => t.index))

    // Add tabs for backend terminals not present locally
    for (const index of backendIndices) {
        if (!localIndices.has(index)) {
            const label = index === 0 ? 'Main' : `Term ${index + 1}`
            terminals.value.push({ index, label })
        }
    }

    // Remove tabs for terminals killed from another device
    // (only if we have old indices to compare — skip on first load)
    if (oldIndices) {
        const removedIndices = oldIndices.filter(i => !backendIndices.includes(i))
        for (const index of removedIndices) {
            if (index === 0) continue // main terminal never removed
            const idx = terminals.value.findIndex(t => t.index === index)
            if (idx !== -1) {
                terminals.value.splice(idx, 1)
                if (activeIndex.value === index) {
                    const prevTerminal = terminals.value[Math.max(0, idx - 1)]
                    activeIndex.value = prevTerminal?.index ?? 0
                }
            }
        }
    }

    // Sort terminals by index for consistent ordering
    terminals.value.sort((a, b) => a.index - b.index)

    // Update nextIndex to avoid collisions
    const maxIndex = Math.max(0, ...backendIndices, ...terminals.value.map(t => t.index))
    if (maxIndex >= nextIndex.value) {
        nextIndex.value = maxIndex + 1
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard shortcuts: terminal tab navigation (Alt+Ctrl+Shift+{1-9, ←/→, ↑})
// Events dispatched by App.vue, handled here by the active instance only.
// ═══════════════════════════════════════════════════════════════════════════

// Tab visit history for Alt+Ctrl+Shift+↑ (last-visited, Alt+Tab-like behavior).
const terminalTabHistory = []
const MAX_TERMINAL_TAB_HISTORY = 50

function pushTerminalTabHistory(termIndex) {
    if (terminalTabHistory.length > 0 && terminalTabHistory[terminalTabHistory.length - 1] === termIndex) return
    terminalTabHistory.push(termIndex)
    if (terminalTabHistory.length > MAX_TERMINAL_TAB_HISTORY) terminalTabHistory.shift()
}

// Track terminal tab transitions for history
watch(activeIndex, (newIndex, oldIndex) => {
    if (!props.active) return
    if (oldIndex !== undefined && oldIndex !== newIndex) pushTerminalTabHistory(oldIndex)
})

function handleTerminalTabShortcut(event) {
    if (!props.active) return

    const { type, index } = event.detail

    if (type === 'direct') {
        // Direct access: number N → the Nth terminal tab (1-based positional)
        const term = terminals.value[index - 1]
        if (term) activeIndex.value = term.index
    } else if (type === 'prev' || type === 'next') {
        const currentIdx = terminals.value.findIndex(t => t.index === activeIndex.value)
        if (currentIdx === -1) return
        const newIdx = type === 'next'
            ? (currentIdx + 1) % terminals.value.length
            : (currentIdx - 1 + terminals.value.length) % terminals.value.length
        activeIndex.value = terminals.value[newIdx].index
    } else if (type === 'last-visited') {
        const validIndices = new Set(terminals.value.map(t => t.index))
        for (let i = terminalTabHistory.length - 1; i >= 0; i--) {
            const idx = terminalTabHistory[i]
            if (idx !== activeIndex.value && validIndices.has(idx)) {
                activeIndex.value = idx
                return
            }
        }
    }
}

onMounted(() => {
    window.addEventListener('twicc:terminal-tab-shortcut', handleTerminalTabShortcut)
})
onBeforeUnmount(() => {
    window.removeEventListener('twicc:terminal-tab-shortcut', handleTerminalTabShortcut)
})
</script>

<template>
    <div class="terminal-panel">
        <!-- Merged toolbar: terminal tabs (left) + action buttons (right) -->
        <div class="terminal-actions-bar">
            <!-- Left: wa-tab-group used only for its scrollable nav -->
            <wa-tab-group
                :active="activeTabPanel"
                class="terminal-tab-nav"
                @wa-tab-show="onTerminalTabShow"
            >
                <wa-tab
                    v-for="term in terminals"
                    :key="term.index"
                    slot="nav"
                    :panel="`term-${term.index}`"
                >
                    {{ term.label }}
                </wa-tab>

                <wa-button
                    slot="nav"
                    variant="brand"
                    appearance="outlined"
                    size="small"
                    class="add-terminal-button"
                    @click="createTerminal"
                >
                    <wa-icon name="plus"></wa-icon>
                </wa-button>
            </wa-tab-group>

            <!-- Right: terminal-specific actions (driven by activeApi) -->
            <div v-if="tb.isConnected" class="terminal-actions">
                <!-- Scroll to edge buttons -->
                <wa-button
                    v-if="tb.canScrollUp || tb.paneAlternate"
                    id="terminal-scroll-top-button"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="scroll-edge-button"
                    :loading="tb.scrollingToEdge"
                    @click="handleScrollToEdge('top')"
                >
                    <wa-icon name="angles-up"></wa-icon>
                </wa-button>
                <AppTooltip
                    v-if="tb.canScrollUp || tb.paneAlternate"
                    for="terminal-scroll-top-button"
                >Scroll to top</AppTooltip>

                <wa-button
                    v-if="tb.canScrollDown || tb.paneAlternate"
                    id="terminal-scroll-bottom-button"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="scroll-edge-button"
                    :loading="tb.scrollingToEdge"
                    @click="handleScrollToEdge('bottom')"
                >
                    <wa-icon name="angles-down"></wa-icon>
                </wa-button>
                <AppTooltip
                    v-if="tb.canScrollDown || tb.paneAlternate"
                    for="terminal-scroll-bottom-button"
                >Scroll to bottom</AppTooltip>

                <!-- Mobile-only: scroll/select mode toggle -->
                <div v-if="settingsStore.isTouchDevice" class="touch-mode-group">
                    <span
                        class="touch-mode-label"
                        @click="handleTouchModeToggle"
                    >Scroll</span>
                    <wa-switch
                        size="small"
                        class="touch-mode-switch"
                        :checked="tb.touchMode === 'select'"
                        @change="handleTouchModeChange"
                    >Select</wa-switch>
                </div>

                <!-- Copy button (all devices) -->
                <wa-button
                    v-if="tb.hasSelection"
                    id="terminal-copy-button"
                    variant="neutral"
                    appearance="filled"
                    size="small"
                    class="copy-button"
                    @click="handleCopy"
                >
                    <wa-icon name="copy" variant="regular"></wa-icon>
                </wa-button>
                <AppTooltip v-if="tb.hasSelection" for="terminal-copy-button">Copy selection</AppTooltip>

                <!-- Paste button -->
                <wa-button
                    id="terminal-paste-button"
                    variant="neutral"
                    appearance="filled"
                    size="small"
                    class="paste-button"
                    @click="handlePaste"
                >
                    <wa-icon name="paste" variant="regular"></wa-icon>
                </wa-button>
                <AppTooltip for="terminal-paste-button">Paste from clipboard</AppTooltip>

                <!-- Disconnect / Kill button -->
                <wa-button
                    id="terminal-disconnect-button"
                    variant="danger"
                    appearance="filled"
                    size="small"
                    class="disconnect-button"
                    @click="handleDisconnect"
                >
                    <wa-icon name="ban" :label="isActiveMain ? 'Disconnect' : 'Kill terminal'"></wa-icon>
                </wa-button>
                <AppTooltip for="terminal-disconnect-button">{{ isActiveMain ? 'Disconnect' : 'Kill terminal' }}</AppTooltip>
            </div>
        </div>

        <!-- Terminal panels: all overlay each other, only the active one is visible.
             Uses visibility:hidden (not display:none) so hidden terminals keep their
             dimensions — prevents xterm.js resize flash on tab switch. -->
        <div class="terminal-panels-container">
            <div
                v-for="term in terminals"
                :key="term.index"
                :class="['terminal-panel-wrapper', { active: activeIndex === term.index }]"
            >
                <TerminalInstance
                    :session-id="sessionId"
                    :terminal-index="term.index"
                    :active="active && activeIndex === term.index"
                    @disconnected="onTerminalDisconnected(term.index)"
                />
            </div>
        </div>

        <ExtraKeysBar
            :active-modifiers="activeApi?.activeModifiers ?? { ctrl: false, alt: false, shift: false }"
            :locked-modifiers="activeApi?.lockedModifiers ?? { ctrl: false, alt: false, shift: false }"
            :is-touch-device="settingsStore.isTouchDevice"
            :combos="terminalConfigStore.combos"
            :snippets="snippetsForProject"
            @key-input="(...args) => activeApi?.handleExtraKeyInput?.(...args)"
            @modifier-toggle="(...args) => activeApi?.handleExtraKeyModifierToggle?.(...args)"
            @paste="() => activeApi?.handleExtraKeyPaste?.()"
            @combo-press="(...args) => activeApi?.handleComboPress?.(...args)"
            @snippet-press="(...args) => activeApi?.handleSnippetPress?.(...args)"
            @snippet-disabled-press="(snippet) => toast.warning(snippet._disabledReason)"
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

/* ── Merged toolbar ─────────────────────────────────────── */

.terminal-actions-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: start;
    column-gap: var(--wa-space-m);
    padding: var(--wa-space-2xs) var(--wa-space-s);
    border-bottom: 1px solid var(--wa-color-surface-border);
    flex-shrink: 0;
    min-height: 2rem;
}

/* wa-tab-group used only for its scrollable nav — hide its body */
.terminal-tab-nav {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    font-size: var(--wa-font-size-s);
    --indicator-color: var(--wa-color-primary-500);
    --track-color: transparent;
    --track-width: 2px;
}
.terminal-tab-nav::part(base) {
    overflow: hidden;
}
.terminal-tab-nav::part(body) {
    display: none;
}
.terminal-tab-nav::part(nav) {
    border-bottom: none;
    padding-bottom: 0;
}
.terminal-tab-nav::part(tabs) {
    align-items: center;
}
.terminal-tab-nav wa-tab::part(base) {
    padding: 0.25em 0.75em;
}
.terminal-tab-nav wa-tab[active] {
    margin-block-end: 0;
}
.add-terminal-button {
    font-size: var(--wa-font-size-3xs);
    align-self: center;
    &::part(label) {
        scale: 1.2;
    }
}

.terminal-actions {
    margin-inline-start: auto;
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    flex-shrink: 0;
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

.copy-button,
.paste-button {
    flex-shrink: 0;
}

.copy-button::part(base),
.paste-button::part(base) {
    --wa-form-control-padding-block: .5em;
    /* Had to force it here, but the same as default one: */
    --wa-form-control-height: round( calc(2 * var(--wa-form-control-padding-block) + 1em * var(--wa-form-control-value-line-height)), 1px );
}


/* ── Terminal panels ─────────────────────────────────────── */

.terminal-panels-container {
    flex: 1;
    min-height: 0;
    position: relative;
    overflow: hidden;
}

.terminal-panel-wrapper {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    visibility: hidden;
}

.terminal-panel-wrapper.active {
    visibility: visible;
}
</style>
