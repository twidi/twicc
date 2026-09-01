<script setup>
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Notivue, Notification, lightTheme, slateTheme } from 'notivue'
import { useWebSocket, versionMismatchDetected } from './composables/useWebSocket'
import { useDataStore } from './stores/data'
import { useSettingsStore } from './stores/settings'
import { useAuthStore } from './stores/auth'
import { COLOR_SCHEME, PROCESS_STATE } from './constants'
import { useFavicon } from './composables/useFavicon'
import { useTipScheduler } from './composables/useTipScheduler'
import { toast } from './composables/useToast'
import ProviderAuthToastContent from './components/app/ProviderAuthToastContent.vue'
import { getRegisteredProviders, getProviderHelpers } from './providers'
import ConnectionIndicator from './components/app/ConnectionIndicator.vue'
import CustomNotification from './components/app/CustomNotification.vue'
import CommandPalette from './components/app/CommandPalette.vue'
import SearchOverlay from './components/app/SearchOverlay.vue'
import SessionSwitcher from './components/app/SessionSwitcher.vue'
import StopProcessConfirmDialog from './components/app/StopProcessConfirmDialog.vue'
import ProviderActivationDialog from './components/app/ProviderActivationDialog.vue'
import HybridAnnouncementDialog from './components/app/HybridAnnouncementDialog.vue'
import TelemetryNoticeDialog from './components/app/TelemetryNoticeDialog.vue'
import HelpDialog from './components/help/HelpDialog.vue'
import GlobalMediaPreview from './components/media/GlobalMediaPreview.vue'
import ProjectTrustDialog from './components/project/ProjectTrustDialog.vue'
import ProjectEditDialog from './components/project/ProjectEditDialog.vue'
import WorktreeDialog from './components/project/WorktreeDialog.vue'
import ShareManagerDialog from './components/share/ShareManagerDialog.vue'
import PeersManagerDialog from './components/peer/PeersManagerDialog.vue'
import PeerInboxDialog from './components/peer/PeerInboxDialog.vue'
import PeerMessageReviewDialog from './components/peer/PeerMessageReviewDialog.vue'
import PeerComposeDialog from './components/peer/PeerComposeDialog.vue'
import TerminalPool from './components/terminal/TerminalPool.vue'
import { registerTrustDialog, ensureProjectTrust } from './composables/useTrustGate'
import { initStaticCommands } from './commands/staticCommands'
import {
    pendingConfirmation,
    confirmPendingStop,
    cancelPendingStop,
    stopSessionProcess,
    hardKillSessionProcess,
} from './composables/useStopSessionProcess'
import { canStealFocus, hasBlockingOverlay } from './utils/focusGuard'
import { focusChatPrimary, gotoChatFooterPanel } from './utils/focusChat'
import { TERMINAL_ROUTES, WORKFLOW_ROUTES } from './utils/tabRoutes'
import { toggleSearchInActiveCodeMirror } from './composables/useCodeMirror'
import { useSessionSwitcher } from './composables/useSessionSwitcher'

const route = useRoute()
const router = useRouter()
initStaticCommands(router)
const authStore = useAuthStore()

// "App is ready" — the user is fully through the login layer and visibly
// off the /login route. Use this for anything that should NOT activate
// during the brief window between a successful authStore.login() and the
// full page reload that follows (during which authStore.authenticated is
// already true but the user is still on /login).
const isAppReady = computed(() => !authStore.needsLogin && route.name !== 'login')
const isConnecting = computed(() => authStore.isConnecting)

// Initialize WebSocket connection for real-time updates.
// Connection is deferred until authenticated (see useWebSocket).
const { wsStatus, openWs, closeWs } = useWebSocket()

// Dynamic favicon: overlays a status badge based on global process state
useFavicon()

// Start the tip scheduler: first tip after FIRST_TIP_DELAY_MS, then
// polling every SCHEDULER_POLL_MS once the per-dismiss cooldown expires.
// Skipped entirely when the app isn't ready (e.g. on /login). LoginView
// triggers a full page reload after login, so this setup re-runs with
// isAppReady true and the scheduler kicks in then.
if (isAppReady.value) {
    useTipScheduler()
}

// Load initial data and connect WebSocket when authenticated
const dataStore = useDataStore()

// Load app data and open the WS only once the app is actually ready
// (auth OK AND not on the /login page). The combined `isAppReady`
// avoids the brief flash where authStore.authenticated has already
// flipped to true but LoginView's full page reload hasn't fired yet.
watch(isAppReady, async (ready) => {
    if (ready) {
        await Promise.all([
            dataStore.loadHomeData(),
            // Preload "sticky" sessions (pinned, unread, running process)
            // from every project so the sidebar can render them cross-filter
            // without waiting for their project to be loaded on demand.
            dataStore.loadStickySessions(),
            // Preload all artifact bookmarks so the Artifacts sidebar mode can
            // render them across projects without per-project loading.
            dataStore.loadArtifactBookmarks(),
        ])
        openWs()
    } else {
        closeWs()
    }
}, { immediate: true })

// Sync display mode to body data attribute
const settingsStore = useSettingsStore()
const displayMode = computed(() => settingsStore.getDisplayMode)

// Set initial value and watch for changes
document.body.dataset.displayMode = displayMode.value
watch(displayMode, (newMode) => {
    document.body.dataset.displayMode = newMode
})

// Auto-reload when backend version changes
watch(versionMismatchDetected, (mismatch) => {
    if (mismatch) {
        setTimeout(() => window.location.reload(), 3000)
    }
})

// Persistent toast(s) when a provider's CLI is not authenticated.
// One toast per registered provider whose helpers expose ``getAuthState``;
// providers without an auth gate (default base implementation) are skipped
// entirely. The toast is shown only when the provider is BOTH enabled
// (settingsStore.enabledProviders) AND known to be unauthenticated, so a
// disabled provider never surfaces a banner — and an active banner is
// cleared as soon as the user disables its provider.
const _providerAuthToastItems = new Map()
for (const provider of getRegisteredProviders()) {
    const helpers = getProviderHelpers(provider)
    const authStateGetter = helpers.getAuthState()
    if (!authStateGetter) continue
    const shouldShow = computed(
        () => settingsStore.enabledProviders.includes(provider) && authStateGetter() === false,
    )
    watch(shouldShow, (show) => {
        const existing = _providerAuthToastItems.get(provider)
        if (show && !existing) {
            const item = toast.custom(ProviderAuthToastContent, {
                type: 'warning',
                title: `${helpers.constructor.label} CLI not authenticated`,
                duration: Infinity,
                props: {
                    provider,
                    loginCommand: helpers.getAuthLoginCommand(),
                },
            })
            _providerAuthToastItems.set(provider, item)
        } else if (!show && existing) {
            existing.clear?.()
            _providerAuthToastItems.delete(provider)
        }
    })
}

// ─── Command Palette (Ctrl+K / Cmd+K) & Search (Ctrl+Shift+F / Ctrl+F) ──
const commandPaletteRef = ref(null)
const searchOverlayRef = ref(null)

// ─── Session switcher (Ctrl+`) — Alt-Tab between recently-visited sessions ──
const sessionSwitcher = useSessionSwitcher()

// Route names where Ctrl+F opens in-session search (main chat tab only)
const SESSION_CHAT_ROUTES = new Set(['session', 'projects-session'])

// Triple-Escape shortcut (emergency stop of the current session's process)
const TRIPLE_ESCAPE_WINDOW_MS = 333  // max gap between two consecutive Escape presses
const TRIPLE_ESCAPE_COOLDOWN_MS = 1000  // after a trigger, ignore Escape for this long
let escapeTimestamps = []  // rolling window of recent Escape presses
let lastTripleEscapeAt = 0

// All session route names (for tab keyboard shortcuts: Alt+Shift+{1-9, 0, ←, →, ↑})
const SESSION_ROUTES = new Set([
    'session', 'session-subagent', 'session-files', 'session-artifacts', 'session-git', 'session-terminal', 'session-orchestration', 'session-plan', 'session-tasks', 'session-workflows', 'session-browser',
    'projects-session', 'projects-session-subagent', 'projects-session-files', 'projects-session-artifacts', 'projects-session-git', 'projects-session-terminal', 'projects-session-orchestration', 'projects-session-plan', 'projects-session-tasks', 'projects-session-workflows', 'projects-session-browser',
])

// Project detail route names (for tab keyboard shortcuts: Alt+Shift+{1-4, ←, →, ↑})
const PROJECT_DETAIL_ROUTES = new Set([
    'project', 'project-files', 'project-git', 'project-terminal',
    'projects-all', 'projects-files', 'projects-git', 'projects-terminal',
])

// TERMINAL_ROUTES / WORKFLOW_ROUTES come from ./utils/tabRoutes — shared with the
// command registry's route-gated "Go to … tab" commands (imported above).

// Routes whose Files / Git panel hosts a CodeMirror editor with an Edit switch
// (for the Alt+E toggle-edit shortcut).
const FILE_EDITOR_ROUTES = new Set([
    'session-files', 'projects-session-files', 'session-artifacts', 'projects-session-artifacts', 'session-git', 'projects-session-git',
    'project-files', 'projects-files', 'project-git', 'projects-git',
])

function handleGlobalKeydown(e) {
    const modKey = settingsStore.isMac ? e.metaKey : e.ctrlKey

    // ─── Ctrl+` session switcher (Alt-Tab between sessions) ─────────────────
    // Ctrl is used uniformly on every OS (never swapped to Cmd: Cmd+` is an OS
    // window-cycle shortcut on macOS). Held Ctrl keeps the panel open; the
    // matching keyup (handleGlobalKeyup) commits. e.code === 'Backquote' is the
    // physical key, layout-independent. Shift on the engaging press opens the
    // "currently displayed" source instead of the recent (MRU) one.
    if (e.ctrlKey && !e.altKey && !e.metaKey && e.code === 'Backquote') {
        // Don't engage on top of a modal; once engaged, keep cycling regardless.
        if (!sessionSwitcher.active.value && hasBlockingOverlay()) return
        e.preventDefault()
        e.stopPropagation()
        sessionSwitcher.onCycleKey({
            engageMode: e.shiftKey ? 'list' : 'mru',
            repeat: e.repeat,
            currentSessionId: route.params.sessionId || null,
        })
        return
    }
    // While the switcher is open: Shift flips the source, arrows move the cursor,
    // Escape cancels (handled here so it pre-empts the triple-Escape branch).
    if (sessionSwitcher.active.value) {
        // A fresh Shift press (not its autorepeat) toggles MRU ↔ displayed.
        if (e.key === 'Shift' && !e.repeat) {
            e.preventDefault()
            e.stopPropagation()
            sessionSwitcher.toggleMode(route.params.sessionId || null)
            return
        }
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault()
            e.stopPropagation()
            sessionSwitcher.onArrow({ backward: e.key === 'ArrowUp', repeat: e.repeat })
            return
        }
        if (e.key === 'Escape') {
            e.preventDefault()
            e.stopPropagation()
            sessionSwitcher.cancel()
            return
        }
    }
    if (modKey && e.key === 'k') {
        e.preventDefault()
        e.stopPropagation()
        commandPaletteRef.value?.open()
    }
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'F') {
        e.preventDefault()
        e.stopPropagation()
        searchOverlayRef.value?.open()
    }
    // Ctrl+F (without Shift):
    // 1. If focus is inside a CodeMirror editor (Files, Git, embedded chat
    //    viewers…), own the shortcut: first press opens the editor's search
    //    panel (prefilled with the selection), second press closes it and
    //    falls through to the browser's native Find bar.
    // 2. Otherwise, on a session's chat tab, toggle the in-session search bar
    //    with the same first-open / second-close-and-fall-through pattern.
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key === 'f') {
        const cmAction = toggleSearchInActiveCodeMirror()
        if (cmAction) {
            // stopPropagation prevents CodeMirror's own searchKeymap from
            // re-toggling the panel after us (we run in capture phase).
            e.stopPropagation()
            if (cmAction === 'opened') {
                e.preventDefault()  // block browser Find — CM panel just opened
            }
            // 'closed' → leave default so browser Find opens
            return
        }
        if (SESSION_CHAT_ROUTES.has(route.name)) {
            const detail = { handled: false }
            window.dispatchEvent(new CustomEvent('twicc:toggle-session-search', { detail }))
            if (detail.handled) {
                e.preventDefault()
                e.stopPropagation()
            }
        }
    }
    // Alt+Ctrl+Shift+{1-9, ←, →, ↑, ↓}: terminal tab navigation within the terminal panel.
    // Dispatches a custom event handled by the active TerminalPanel instance.
    if (e.altKey && e.shiftKey && e.ctrlKey && !e.metaKey && TERMINAL_ROUTES.has(route.name)) {
        let tabAction = null
        const digitMatch = e.code.match(/^(?:Digit|Numpad)([1-9])$/)
        if (digitMatch) {
            tabAction = { type: 'direct', index: parseInt(digitMatch[1]) }
        } else if (e.key === 'ArrowLeft') {
            tabAction = { type: 'prev' }
        } else if (e.key === 'ArrowRight') {
            tabAction = { type: 'next' }
        } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            tabAction = { type: 'last-visited' }
        }
        if (tabAction) {
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:terminal-tab-shortcut', { detail: tabAction }))
        }
    }
    // Alt+Ctrl+Shift+{1-9, ←, →, ↑, ↓}: workflow run-tab navigation within the Workflows pane.
    // Dispatches a custom event handled by the active WorkflowsPane instance.
    if (e.altKey && e.shiftKey && e.ctrlKey && !e.metaKey && WORKFLOW_ROUTES.has(route.name)) {
        let tabAction = null
        const digitMatch = e.code.match(/^(?:Digit|Numpad)([1-9])$/)
        if (digitMatch) {
            tabAction = { type: 'direct', index: parseInt(digitMatch[1]) }
        } else if (e.key === 'ArrowLeft') {
            tabAction = { type: 'prev' }
        } else if (e.key === 'ArrowRight') {
            tabAction = { type: 'next' }
        } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            tabAction = { type: 'last-visited' }
        }
        if (tabAction) {
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:workflow-tab-shortcut', { detail: tabAction }))
        }
    }
    // Alt+Shift+{1-9, 0, ←, →, ↑, ↓}: tab navigation within a session or project detail panel.
    // Dispatches a custom event handled by the active SessionView or ProjectDetailPanel instance.
    // (Indices 5/6/7/8/9 are the session-only Tasks/Plan/Artifacts/Orchestration/Workflows tabs
    // and 0 the session-only Browser tab; project-detail panels ignore them.)
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && (SESSION_ROUTES.has(route.name) || PROJECT_DETAIL_ROUTES.has(route.name))) {
        let tabAction = null
        // Use e.code (physical key) for digits — e.key depends on keyboard layout
        // and modifiers (e.g. French AZERTY: Alt+Shift+number row produces unexpected e.key values).
        const digitMatch = e.code.match(/^(?:Digit|Numpad)([0-9])$/)
        if (digitMatch) {
            tabAction = { type: 'direct', index: parseInt(digitMatch[1]) }
        } else if (e.key === 'ArrowLeft') {
            tabAction = { type: 'prev' }
        } else if (e.key === 'ArrowRight') {
            tabAction = { type: 'next' }
        } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            tabAction = { type: 'last-visited' }
        }
        if (tabAction) {
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:tab-shortcut', { detail: tabAction }))
        }
    }
    // Alt+Shift+O: toggle the Agent Settings popover. Only active when focus
    // is in the message input's wa-textarea (open intent) or already inside
    // the open agent-settings popover (close intent). Uses e.code (physical
    // key) because Alt+Shift+O produces a composed character on macOS (Ø)
    // which makes e.key unreliable across layouts.
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyO' && SESSION_CHAT_ROUTES.has(route.name)) {
        const active = document.activeElement
        const inTextarea = active?.tagName === 'WA-TEXTAREA' && active.closest('.message-input') !== null
        // The agent-settings popover is identified by .settings-panel-presets,
        // which the global app SettingsPopover (sharing the .settings-popover
        // class) doesn't render.
        const inAgentPopover = active?.closest('wa-popover')?.querySelector('.settings-panel-presets') != null
        if (inTextarea || inAgentPopover) {
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:toggle-agent-settings'))
        }
    }
    // Alt+Shift+M: focus the message input. Active on any session sub-route
    // (chat, files, git, terminal, subagent); navigates to the chat tab
    // first if needed. Standard focus-steal rules apply (see canStealFocus).
    //
    // Exception: the Agent Settings popover (identified by
    // .settings-panel-presets, since it shares the .settings-popover class
    // with the global app SettingsPopover) is NOT a blocker — instead
    // Alt+Shift+M closes it and refocuses the textarea, same as Alt+Shift+O
    // from inside the popover. This branch deliberately skips the in-editable
    // check (handled by hasBlockingOverlay only) so the shortcut still
    // closes the popover even if focus is inside one of its inputs.
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyM' && SESSION_ROUTES.has(route.name)) {
        const agentPopoverOpen = document.querySelector('wa-popover[open] .settings-panel-presets') !== null

        if (agentPopoverOpen && !hasBlockingOverlay({ allowPopoverContaining: '.settings-panel-presets' })) {
            // Reuse the toggle event — its handler closes the popover and
            // focuses the textarea when the popover was open.
            e.preventDefault()
            e.stopPropagation()
            window.dispatchEvent(new CustomEvent('twicc:toggle-agent-settings'))
        } else if (canStealFocus()) {
            e.preventDefault()
            e.stopPropagation()
            if (SESSION_CHAT_ROUTES.has(route.name)) {
                focusChatPrimary()
            } else {
                const chatRouteName = route.name.startsWith('projects-session') ? 'projects-session' : 'session'
                // `query` carried explicitly: the guard only re-propagates ?workspace= when the
                // target project belongs to it, so a session viewed from outside the workspace
                // would otherwise lose it on the way to its own Chat tab.
                router.push({ name: chatRouteName, params: route.params, query: route.query }).then(() => focusChatPrimary())
            }
        }
    }
    // Alt+Shift+PageDown / PageUp: move between the chat footer panels (the
    // message-input / pending-request / embedded-terminal accordion). PageDown
    // always goes to the message input; PageUp to the open pending request (else,
    // from the message input on a hybrid session, the CLI terminal).
    //
    // Chat tab ONLY (SESSION_CHAT_ROUTES): unlike Alt+Shift+T, these are
    // contextual — where they land depends on what's currently open — so they
    // make no sense without the footer already in view; we don't yank you to the
    // chat tab for them. No in-editable guard (the point is to leave the
    // composer); only a modal-overlay skip. We always swallow the chord (when no
    // overlay) so a no-op PageUp never falls through to the composer's own PageUp
    // = message-history shortcut.
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && (e.code === 'PageDown' || e.code === 'PageUp') && SESSION_CHAT_ROUTES.has(route.name)) {
        if (!hasBlockingOverlay()) {
            e.preventDefault()
            e.stopPropagation()
            if (e.code === 'PageDown') {
                gotoChatFooterPanel(route, router, 'twicc:goto-message-input')
            } else {
                // PageUp → the open pending request when one is shown, else (from
                // the message input, hybrid session) the CLI terminal. The active
                // SessionItemsList makes the final call; we only gate the dispatch
                // so a non-hybrid session with no pending does nothing.
                const head = dataStore.getPendingRequests(route.params.sessionId)?.[0]
                const hasPending = !!head && head.request_type !== 'hybrid_terminal'
                const sess = dataStore.getSession(route.params.sessionId)
                if (hasPending || (sess?.hybrid === true && settingsStore.isClaudeHybridEnabled)) {
                    gotoChatFooterPanel(route, router, 'twicc:goto-pending-request')
                }
            }
        }
    }
    // Alt+Shift+T: toggle between the embedded Claude CLI terminal and the message
    // input — hybrid sessions only, like Alt+Shift+M for the message input (works
    // from any session sub-tab, navigating to the chat tab first). e.code
    // (physical key) for layout safety.
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyT' && SESSION_ROUTES.has(route.name)) {
        const sess = dataStore.getSession(route.params.sessionId)
        if (sess?.hybrid === true && settingsStore.isClaudeHybridEnabled && !hasBlockingOverlay()) {
            e.preventDefault()
            e.stopPropagation()
            gotoChatFooterPanel(route, router, 'twicc:toggle-terminal')
        }
    }
    // Alt+Shift+H: toggle hybrid mode — same as clicking the composer's hybrid
    // button, where it's toggleable: enable/disable a Claude draft, stage/un-stage
    // an SDK session, or open the confirm dialog. A committed-permanent session
    // has nothing to toggle, so the chord is left untouched there.
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyH' && SESSION_ROUTES.has(route.name)) {
        const sess = dataStore.getSession(route.params.sessionId)
        const toggleable = sess
            && settingsStore.isClaudeHybridEnabled
            && sess.provider === 'claude_code'
            && !sess.hidden
            && !sess.parent_session_id
            && !(!sess.draft && sess.hybrid === true)  // not committed-permanent
        if (toggleable && !hasBlockingOverlay()) {
            e.preventDefault()
            e.stopPropagation()
            gotoChatFooterPanel(route, router, 'twicc:toggle-hybrid')
        }
    }
    // Alt+E: toggle the Edit switch of the active Files/Git CodeMirror editor.
    // Dispatched as a window event; the active FilePane flips detail.handled
    // when it actually toggles (writable file open), so we only swallow the key
    // — and the browser's native Edit menu — when something happened. e.code
    // (physical key) because Alt+E is a dead key on macOS (´ accent).
    if (e.altKey && !e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyE' && FILE_EDITOR_ROUTES.has(route.name)) {
        const detail = { handled: false }
        window.dispatchEvent(new CustomEvent('twicc:toggle-file-edit', { detail }))
        if (detail.handled) {
            e.preventDefault()
            e.stopPropagation()
        }
    }
    // Alt+Shift+Enter: maximize the focused dockable pane, or restore it when already
    // maximized — a toggle, mirroring the maximize/restore double-click on a region.
    // Alt+Shift+Backspace: minimize the focused docked panel. Enter / Backspace are special
    // keys (same physical key and e.code on every layout, no shifted-glyph issue), unlike
    // +/−, and sit together under the right hand. Session routes only (project-detail panels
    // have no docking). The active SessionView flips detail.handled when it acts, so we
    // swallow the key (and any composed character) only then.
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && SESSION_ROUTES.has(route.name)) {
        let layoutAction = null
        if (e.code === 'Enter' || e.code === 'NumpadEnter') layoutAction = 'maximize'
        else if (e.code === 'Backspace') layoutAction = 'minimize'
        if (layoutAction && !hasBlockingOverlay()) {
            const detail = { action: layoutAction, handled: false }
            window.dispatchEvent(new CustomEvent('twicc:layout-shortcut', { detail }))
            if (detail.handled) {
                e.preventDefault()
                e.stopPropagation()
            }
        }
    }
    // Alt+Shift+B: toggle the sidebar — the keyboard equivalent of the sidebar footer
    // toggle button. Global (any route where ProjectView is mounted); its listener flips
    // detail.handled so we swallow the key (and the character macOS composes from
    // Alt+Shift+B) only when it actually toggled. e.code is the physical key (layout-safe).
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyB' && !hasBlockingOverlay()) {
        const detail = { handled: false }
        window.dispatchEvent(new CustomEvent('twicc:toggle-sidebar', { detail }))
        if (detail.handled) {
            e.preventDefault()
            e.stopPropagation()
        }
    }
    // Triple-Escape: emergency stop of the current chat session's process.
    // Only active on chat routes (session, projects-session), only when a
    // stoppable process exists. Does NOT preventDefault/stopPropagation —
    // we let the Escape propagate so overlays close naturally.
    //
    // NOTE: the `return` statements below exit handleGlobalKeydown entirely.
    // This is intentional and safe because this IS the last branch in the
    // function. If you add a new shortcut AFTER this block, convert these
    // early returns to not exit the function.
    if (e.key === 'Escape' && !e.repeat) {
        const now = performance.now()

        if (now - lastTripleEscapeAt < TRIPLE_ESCAPE_COOLDOWN_MS) {
            // Swallow counting during cooldown (but still let the event bubble)
            return
        }

        if (!SESSION_CHAT_ROUTES.has(route.name)) {
            escapeTimestamps = []
            return
        }
        const sessionId = route.params.sessionId
        if (!sessionId) return

        const ps = dataStore.getProcessState(sessionId)
        const canStop = ps && !ps.synthetic && ps.state && ps.state !== PROCESS_STATE.DEAD
        if (!canStop) {
            escapeTimestamps = []
            return
        }

        // Reset the sequence if the gap since the previous press exceeded the
        // window. Otherwise append. We measure gap-to-previous, NOT distance-to-first,
        // so a natural tap-tap-tap at ~150 ms/key still triggers (3 gaps of 150 ms
        // each fit within the 200 ms per-gap budget, total span ~300–450 ms).
        const last = escapeTimestamps[escapeTimestamps.length - 1]
        if (last === undefined || now - last < TRIPLE_ESCAPE_WINDOW_MS) {
            escapeTimestamps.push(now)
        } else {
            escapeTimestamps = [now]
        }

        if (escapeTimestamps.length >= 3) {
            lastTripleEscapeAt = now
            escapeTimestamps = []
            // Holding Shift on the triggering Escape forces a hard kill (no
            // grace, no confirmation); a plain triple-Escape runs the soft stop.
            const force = e.shiftKey
            // Defer to the next tick so the triggering Escape event finishes
            // propagating BEFORE the confirmation dialog opens. Otherwise the
            // wa-dialog would catch the still-bubbling Escape and close itself
            // immediately (only visible in the crons-confirmation path).
            setTimeout(() => {
                if (force) hardKillSessionProcess(sessionId)
                else stopSessionProcess(sessionId)
            }, 0)
        }
    }
}

// Releasing Ctrl is the keystone of the switcher gesture: commit the highlighted
// session. (Releasing the cycle key ` alone, with Ctrl still held, is ignored —
// only Ctrl up commits.)
function handleGlobalKeyup(e) {
    if (!sessionSwitcher.active.value) return
    if (e.key === 'Control' || !e.ctrlKey) {
        sessionSwitcher.commit()
    }
}

// If the window loses focus while Ctrl is still held (OS Alt-Tab, click into
// another app), we'll never receive the Ctrl keyup — cancel rather than leave
// the panel stuck open.
function handleWindowBlur() {
    if (sessionSwitcher.active.value) sessionSwitcher.cancel()
}

const trustDialogRef = ref(null)

// ─── Global, command-palette-driven dialogs ──────────────────────────────
// Mounted here (not in ProjectView/HomeView) so the command palette can open
// them from anywhere — home, a project, or a session. Driven by window events,
// matching the existing dialog-event pattern.
const globalProjectEditRef = ref(null)
const globalEditingProject = ref(null)
const worktreeDialogRef = ref(null)

// Shared-links manager — opened from the command palette (and reachable from
// Settings → Sharing, which owns its own instance). Global here so the palette
// can open it from anywhere.
const showShareManager = ref(false)
function openShareManager() {
    showShareManager.value = true
}

// Peer dialogs — mounted here so the settings section, the toasts and the
// inbox badge can all open them from anywhere via window events.
const showPeersManager = ref(false)
function openPeersManager() {
    showPeersManager.value = true
}
const showPeerInbox = ref(false)
const peerReviewMessageId = ref(null)
// A message opened FROM the inbox goes back to it when closed: triaging is a
// list-then-message-then-list loop, and reaching the inbox again costs several
// clicks (more so with no unread badge to click).
const peerReviewFromInbox = ref(false)
function openPeerInbox(e) {
    const messageId = e?.detail?.messageId
    if (messageId != null) {
        // A toast's Read button targets one message: open the review directly.
        peerReviewFromInbox.value = false
        peerReviewMessageId.value = messageId
    } else {
        showPeerInbox.value = true
    }
}
function onPeerInboxReview(messageId) {
    showPeerInbox.value = false
    peerReviewFromInbox.value = true
    peerReviewMessageId.value = messageId
}
function onPeerReviewClose(reason) {
    // The dialog closes twice: once on the action (button, refusal, delivery),
    // once on the wa-hide that follows. Only the first one decides.
    if (peerReviewMessageId.value == null) return
    const fromInbox = peerReviewFromInbox.value
    const messageId = peerReviewMessageId.value
    peerReviewFromInbox.value = false
    peerReviewMessageId.value = null
    if (reason === 'compose') {
        // "Reply manually": the composer takes over and comes back to this
        // very message on close — with the inbox chain intact behind it.
        peerComposeReturn = { kind: 'review', messageId, fromInbox }
        return
    }
    showPeerInbox.value = fromInbox && reason !== 'navigating'
}
// Direct owner-written message (manager row, inbox footer, review's "Reply
// manually"). The composer is a detour: whatever closes it — send, cancel,
// discard, Esc — reopens the dialog it was opened from.
const peerCompose = ref(null)
let peerComposeReturn = null   // { kind: 'manager' | 'inbox' | 'review', messageId?, fromInbox? }
function openPeerCompose(e) {
    const detail = e?.detail || {}
    // The review dialog registers its own return (with the inbox chain) in
    // onPeerReviewClose('compose'), before dispatching this event.
    if (detail.returnTo !== 'review') {
        peerComposeReturn = detail.returnTo ? { kind: detail.returnTo } : null
    }
    peerCompose.value = {
        peerId: detail.peerId || null,
        replyTo: detail.replyTo || null,
        replyToTitle: detail.replyToTitle || '',
        replyPending: !!detail.replyPending,
    }
}
function onPeerComposeClose() {
    // Same double close as the review dialog (action, then wa-hide).
    if (peerCompose.value == null) return
    peerCompose.value = null
    const back = peerComposeReturn
    peerComposeReturn = null
    if (back?.kind === 'manager') showPeersManager.value = true
    else if (back?.kind === 'inbox') showPeerInbox.value = true
    else if (back?.kind === 'review') {
        peerReviewFromInbox.value = back.fromInbox
        peerReviewMessageId.value = back.messageId
    }
}

// Edit any project (current project, or one picked from a palette list).
function openEditProjectDialog(e) {
    const projectId = e.detail?.projectId
    const project = projectId ? dataStore.getProject(projectId) : null
    if (!project) return
    globalEditingProject.value = project
    globalProjectEditRef.value?.open()
}

// Open the worktree dialog for a project (create a new worktree or pick an
// existing one), then drop the user into a fresh draft session in the resolved
// worktree — mirrors the "New session" dropdown's per-row worktree button.
function openWorktreeDialog(e) {
    const projectId = e.detail?.projectId
    const project = projectId ? dataStore.getProject(projectId) : null
    if (!project) return
    worktreeDialogRef.value?.open(project)
}
async function handleWorktreeResolved(project) {
    if (!project) return
    const gate = await ensureProjectTrust(project.id)
    if (!gate) return
    const sessionId = dataStore.createDraftSession(project.id, gate.state)
    const allProjects = route.name?.startsWith('projects-')
    router.push({
        name: allProjects ? 'projects-session' : 'session',
        params: { projectId: project.id, sessionId },
        query: route.query,
    })
}

onMounted(() => {
    document.addEventListener('keydown', handleGlobalKeydown, { capture: true })
    document.addEventListener('keyup', handleGlobalKeyup, { capture: true })
    window.addEventListener('blur', handleWindowBlur)
    registerTrustDialog(trustDialogRef.value)
    window.addEventListener('twicc:open-edit-project-dialog', openEditProjectDialog)
    window.addEventListener('twicc:open-worktree-dialog', openWorktreeDialog)
    window.addEventListener('twicc:open-share-manager', openShareManager)
    window.addEventListener('twicc:open-peers-manager', openPeersManager)
    window.addEventListener('twicc:open-peer-inbox', openPeerInbox)
    window.addEventListener('twicc:open-peer-compose', openPeerCompose)
})
onBeforeUnmount(() => {
    document.removeEventListener('keydown', handleGlobalKeydown, { capture: true })
    document.removeEventListener('keyup', handleGlobalKeyup, { capture: true })
    window.removeEventListener('blur', handleWindowBlur)
    window.removeEventListener('twicc:open-edit-project-dialog', openEditProjectDialog)
    window.removeEventListener('twicc:open-worktree-dialog', openWorktreeDialog)
    window.removeEventListener('twicc:open-share-manager', openShareManager)
    window.removeEventListener('twicc:open-peers-manager', openPeersManager)
    window.removeEventListener('twicc:open-peer-inbox', openPeerInbox)
    window.removeEventListener('twicc:open-peer-compose', openPeerCompose)
})

// Notivue theme - inverted for contrast (dark theme when app is light, and vice-versa)
const toastTheme = computed(() => {
    const isDark = settingsStore.getEffectiveColorScheme === COLOR_SCHEME.DARK
    // Invert: use light toast theme when app is dark, and vice-versa
    return {
        ...(isDark ? lightTheme : slateTheme),
        '--nv-width': '100%',
        '--nv-min-width': '30rem',
    }
})
</script>

<template>
    <!-- Provider activation: non-dismissible first-run / recovery dialog -->
    <ProviderActivationDialog v-if="isAppReady" />

    <!-- Hybrid mode / billing-change announcement: auto-opens once for users who
         haven't seen the explainer yet (self-gates on the synced flag). Gated by
         the hybrid feature flag — never mounts while hybrid mode is disabled. -->
    <HybridAnnouncementDialog v-if="isAppReady && settingsStore.isClaudeHybridEnabled" />
    <TelemetryNoticeDialog v-if="isAppReady" />

    <!-- Version mismatch: non-dismissible reload dialog -->
    <wa-dialog :open="versionMismatchDetected || undefined" without-header @wa-hide.prevent>
        <div class="version-reload-content">
            <wa-spinner></wa-spinner>
            <p class="version-reload-text">TwiCC has been updated, reloading…</p>
        </div>
    </wa-dialog>

    <!-- Connecting overlay: shown while waiting for backend during auth check retry -->
    <div v-if="isConnecting" class="connecting-backdrop">
        <div class="connecting-content">
            <wa-spinner></wa-spinner>
            <p class="connecting-text">Connecting to server...</p>
        </div>
    </div>

    <ConnectionIndicator v-if="isAppReady && !isConnecting" :status="wsStatus" />
    <CommandPalette ref="commandPaletteRef" />
    <SearchOverlay ref="searchOverlayRef" />
    <SessionSwitcher />
    <StopProcessConfirmDialog
        :open="pendingConfirmation !== null"
        :mode="pendingConfirmation?.mode ?? 'stop'"
        :cron-count="pendingConfirmation?.cronCount ?? 0"
        @confirm="confirmPendingStop"
        @cancel="cancelPendingStop"
    />
    <!-- Singleton dialog used to preview images & SVGs embedded in markdown
         (and any other "open this media fullscreen" call site that doesn't
         own its own MediaPreviewDialog instance). -->
    <GlobalMediaPreview />
    <!-- Single app-wide help dialog, driven by the help store (showHelp() /
         helpStore.maybeAutoShow()). Renders help/<key>.md pages. -->
    <HelpDialog v-if="isAppReady" />
    <!-- Global trust gate dialog, driven by composables/useTrustGate. -->
    <ProjectTrustDialog ref="trustDialogRef" />
    <!-- Global command-palette dialogs: edit any project, or open a worktree
         — new or existing — (+ a draft session in it). Reachable from home, a
         project, or a session. -->
    <ProjectEditDialog ref="globalProjectEditRef" :project="globalEditingProject" />
    <WorktreeDialog ref="worktreeDialogRef" @resolved="handleWorktreeResolved" />
    <!-- Shared-links manager (command palette: "Manage shared links"). -->
    <ShareManagerDialog :open="showShareManager" @close="showShareManager = false" />
    <!-- Peer messaging: manager, inbox, read-and-route dialog and the
         owner-written composer. -->
    <PeersManagerDialog :open="showPeersManager" @close="showPeersManager = false" />
    <PeerInboxDialog :open="showPeerInbox" @close="showPeerInbox = false" @review="onPeerInboxReview" />
    <PeerMessageReviewDialog
        :open="peerReviewMessageId != null"
        :message-id="peerReviewMessageId"
        @close="onPeerReviewClose"
    />
    <PeerComposeDialog
        :open="peerCompose != null"
        v-bind="peerCompose || {}"
        @close="onPeerComposeClose"
    />
    <!-- Prevent browser default drop behavior (e.g. navigating to a dropped image).
         Our specific drop handlers in SessionItemsList call preventDefault themselves;
         this catches any drops that miss those zones. -->
    <div class="app-container" @dragover.prevent @drop.prevent>
        <router-view />
    </div>

    <!-- App-level pool hosting every TerminalPanel-managed terminal instance.
         Instances are teleported into the active panel's slots and survive
         navigation, enabling parent-terminal attachment. -->
    <TerminalPool v-if="isAppReady" />

    <!-- Toast notification system (theme inverted for contrast).
         The `.wa-invert` box flips the whole `--wa-color-*` set for every toast,
         so WA components and semantic tokens used inside toast content match the
         inverted background without each call site opting in. It sits INSIDE the
         Notivue slot (not around <Notivue>) so it follows the notifications even
         if Notivue teleports its root, and uses `display: contents` so it stays
         out of Notivue's layout and animations. -->
    <Notivue v-slot="item">
        <div class="toast-invert wa-invert">
            <CustomNotification v-if="item.props?.custom" :item="item" :theme="toastTheme" />
            <Notification v-else :item="item" :theme="toastTheme" />
        </div>
    </Notivue>
</template>

<style>
/* Carries the inverted color tokens for every toast, nothing else. */
.toast-invert {
    display: contents;
}

.version-reload-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    padding: var(--wa-space-l);
    text-align: center;
}

.version-reload-text {
    font-size: var(--wa-font-size-l);
    color: var(--wa-color-text-normal);
    margin: 0;
}

.connecting-backdrop {
    position: fixed;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--wa-color-surface-default);
    z-index: 10000;
}

.connecting-content {
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
}

.connecting-text {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    margin: 0;
}

/* box-sizing: border-box override (WA @layer wa-native) moved to the shared
   styles/transcript-tokens.css so the share bundle (no App.vue) gets it too. */

body {
    margin: 0;
    padding: 0;
}

/* Web Awesome removes the footer's block-start padding. Restore the dialog's
   configured spacing so scrolling content does not touch fixed actions. */
wa-dialog::part(footer) {
    padding-block-start: var(--spacing, var(--wa-space-m));
}

/* Clearance the closed-sidebar floating reopen toggle (bottom-left) needs from nearby content — the
   single source of these values. SessionLayout refines them per dock context on .session-layout (see
   there). Consumers, no fallback: the composer (MessageInput toolbar + CollapsedBar) reads -x; the
   left gutter reads -y; the terminal extra-keys bar derives from -left-x. Defined here so it also
   covers components mounted outside the dockable layout (e.g. the project view's terminal). */
body.sidebar-closed {
    /* Left-edge-only clearance, by left dock context: nothing → full, a thin left gutter → reduced,
       a full left column → none. The base both -x and the extra-keys bar derive from. */
    --sidebar-toggle-clearance-left-x: 2.5rem;
    /* Composer clearance = the left-edge value; a bottom dock region zeroes it in SessionLayout (it
       lifts the composer above the toggle). Mirrors -left-x here for composers outside the layout. */
    --sidebar-toggle-clearance-x: var(--sidebar-toggle-clearance-left-x);
    --sidebar-toggle-clearance-y: 3.25rem;
}

.app-container {
    min-height: 100dvh;
    background: var(--wa-color-surface-default);
    color: var(--wa-color-text-normal);
}

:root {
    overflow-y: auto;

    --selection-bg-color: oklch(from var(--wa-color-brand-50) l c h / 0.25);
    ::selection {
        background-color: var(--selection-bg-color);
    }

    /* --base/user/assistant-card-color + --main-shadow-size live in
       styles/transcript-tokens.css (shared with the share bundle, design §8.8). */

    --wa-font-size-3xs: round(calc(var(--wa-font-size-2xs) / 1.125), 1px);

    /* --main-header-footer-bg-color: var(--wa-color-surface-raised); */
    --main-header-footer-bg-color: transparent;

    /* Sparkline / heatmap graph colors (light mode) — green (default) */
    --sparkline-project-gradient-color-0: #ebedf0;
    --sparkline-project-gradient-color-1: #aceebb;
    --sparkline-project-gradient-color-2: #4ac26b;
    --sparkline-project-gradient-color-3: #2da44e;
    --sparkline-project-gradient-color-4: #116329;
    --sparkline-project-stroke-color: #8cc665;

    /* Sparkline graph colors — blue (sessions) */
    --sparkline-blue-gradient-color-1: #a8d4ff;
    --sparkline-blue-gradient-color-2: #4da6ff;
    --sparkline-blue-gradient-color-3: #1a7fdb;
    --sparkline-blue-gradient-color-4: #0a4f8a;
    --sparkline-blue-stroke-color: #6ab8f7;

    /* Sparkline graph colors — red (cost) */
    --sparkline-red-gradient-color-1: #ffb3b3;
    --sparkline-red-gradient-color-2: #ff5c5c;
    --sparkline-red-gradient-color-3: #d63333;
    --sparkline-red-gradient-color-4: #8b1a1a;
    --sparkline-red-stroke-color: #f77070;

    /* Sparkline graph colors — green (temporal) */
    --sparkline-green-gradient-color-1: #aceebb;
    --sparkline-green-gradient-color-2: #4ac26b;
    --sparkline-green-gradient-color-3: #2da44e;
    --sparkline-green-gradient-color-4: #116329;
    --sparkline-green-stroke-color: #8cc665;

    /* Sparkline graph colors — orange */
    --sparkline-orange-gradient-color-1: #ffe6b3;
    --sparkline-orange-gradient-color-2: #f5a623;
    --sparkline-orange-gradient-color-3: #d4760a;
    --sparkline-orange-gradient-color-4: #8a4d0f;
    --sparkline-orange-stroke-color: #e89d3f;

    /* Sparkline graph colors — purple (recent rate long) */
    --sparkline-purple-gradient-color-1: #e8d5f5;
    --sparkline-purple-gradient-color-2: #a855f7;
    --sparkline-purple-gradient-color-3: #7c3aed;
    --sparkline-purple-gradient-color-4: #4c1d95;
    --sparkline-purple-stroke-color: #b07ce8;

    /* Sparkline graph colors — pink (recent rate short) */
    --sparkline-pink-gradient-color-1: #fce4ec;
    --sparkline-pink-gradient-color-2: #f06292;
    --sparkline-pink-gradient-color-3: #d81b60;
    --sparkline-pink-gradient-color-4: #880e4f;
    --sparkline-pink-stroke-color: #e57399;

    --divider-size: 1px;
    &[data-theme="awesome"] {
        --divider-size: 4px;
    }

    /* Diff editor colors (light mode) */
    --diff-removedLineBackground: #FEF1F1;
    --diff-removedTextBackground: #FFC4C3;
    --diff-insertedLineBackground: #C0FFD8;
    --diff-insertedTextBackground: #A7E9B8;
    --diff-selectionBackground: var(--selection-bg-color);

    /* --wa-font-mono moved to the shared styles/transcript-tokens.css. */
}

wa-split-panel {
    --divider-width: var(--divider-size) !important;
}

.wa-dark {

    /* --base-user-assistant-card-color (dark) lives in styles/transcript-tokens.css */
    --wa-color-brand-border-loud: var(--wa-color-brand-50);

    /* Sparkline / heatmap graph colors (dark mode) — green (default) */
    --sparkline-project-gradient-color-0: #151b23;
    --sparkline-project-gradient-color-1: #033a16;
    --sparkline-project-gradient-color-2: #196c2e;
    --sparkline-project-gradient-color-3: #2ea043;
    --sparkline-project-gradient-color-4: #56d364;
    --sparkline-project-stroke-color: #8cc665;

    /* Sparkline graph colors — blue (sessions, dark mode) */
    --sparkline-blue-gradient-color-1: #0a2d4f;
    --sparkline-blue-gradient-color-2: #1a5a8a;
    --sparkline-blue-gradient-color-3: #3a8fd4;
    --sparkline-blue-gradient-color-4: #6abef7;
    --sparkline-blue-stroke-color: #6ab8f7;

    /* Sparkline graph colors — red (cost, dark mode) */
    --sparkline-red-gradient-color-1: #4f0a0a;
    --sparkline-red-gradient-color-2: #8a1a1a;
    --sparkline-red-gradient-color-3: #d44040;
    --sparkline-red-gradient-color-4: #f77070;
    --sparkline-red-stroke-color: #f77070;

    /* Sparkline graph colors — green (temporal, dark mode) */
    --sparkline-green-gradient-color-1: #033a16;
    --sparkline-green-gradient-color-2: #196c2e;
    --sparkline-green-gradient-color-3: #2ea043;
    --sparkline-green-gradient-color-4: #56d364;
    --sparkline-green-stroke-color: #8cc665;

    /* Sparkline graph colors — orange (dark mode) */
    --sparkline-orange-gradient-color-1: #3d1e00;
    --sparkline-orange-gradient-color-2: #7a3c00;
    --sparkline-orange-gradient-color-3: #d4760a;
    --sparkline-orange-gradient-color-4: #f5a623;
    --sparkline-orange-stroke-color: #e89d3f;

    /* Sparkline graph colors — purple (recent rate long, dark mode) */
    --sparkline-purple-gradient-color-1: #2e1065;
    --sparkline-purple-gradient-color-2: #5b21b6;
    --sparkline-purple-gradient-color-3: #8b5cf6;
    --sparkline-purple-gradient-color-4: #c084fc;
    --sparkline-purple-stroke-color: #b07ce8;

    /* Sparkline graph colors — pink (recent rate short, dark mode) */
    --sparkline-pink-gradient-color-1: #4a0e2a;
    --sparkline-pink-gradient-color-2: #9d174d;
    --sparkline-pink-gradient-color-3: #ec4899;
    --sparkline-pink-gradient-color-4: #f9a8d4;
    --sparkline-pink-stroke-color: #e57399;

    /* Diff editor colors (dark mode) */
    --diff-removedLineBackground: #451B1B;
    --diff-removedTextBackground: #5E1B1B;
    --diff-insertedLineBackground: #1B452B;
    --diff-insertedTextBackground: #2A573B;
    --diff-selectionBackground: var(--selection-bg-color);
}

/* Reset Web Awesome button styles inside Notivue notifications */
.Notivue__close {
    all: unset;
    cursor: pointer;
    padding: calc(var(--nv-spacing) / 2);
    margin: var(--nv-spacing) var(--nv-spacing) var(--nv-spacing) 0;
    font-weight: 700;
    line-height: 1;
    font-size: var(--nv-message-size);
    color: var(--nv-fg, var(--nv-global-fg));
    -webkit-tap-highlight-color: transparent;
    position: relative;
    align-self: flex-start;
    /* Without this, the close button (a flex item of .Notivue__notification)
       gets squeezed by longer message content, distorting the X icon's width
       while keeping its height — Notivue applies the same protection on
       .Notivue__icon but forgot it here. */
    flex-shrink: 0;
}

/* Add separator line below title in notifications */
body .Notivue__content-title {
    border-bottom: 1px solid var(--nv-accent, var(--nv-global-accent));
    padding-bottom: var(--nv-spacing);
    margin-bottom: var(--nv-spacing);
}

/* Floating drag-hover indicator (spring-loaded folder pattern) */
.drag-hover-indicator {
    position: fixed;
    z-index: 10000;
    pointer-events: none;
    left: calc(var(--x) * 1px + 16px);
    top: calc(var(--y) * 1px - 16px);
    --size: 20px;
    --track-width: 2.5px;
    --indicator-color: var(--wa-color-success);
    --indicator-transition-duration: 0s;
}

/* .floating-over-text (buttons that float over readable content) lives in the
   shared styles/transcript-tokens.css, so the share bundle gets it too. */

/* Style for low-height buttons */

.reduced-height {
    font-size: var(--wa-font-size-3xs);
    &::part(label) {
        scale: 1.3;
    }
}

/* blockquote font-family/size normalization moved to the shared
   styles/transcript-tokens.css so the share bundle inherits it too. */

</style>
