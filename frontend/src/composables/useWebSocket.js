// frontend/src/composables/useWebSocket.js

import { ref, watch, defineAsyncComponent } from 'vue'
import { useWebSocket as useVueWebSocket, useDebounceFn, useThrottleFn } from '@vueuse/core'
import { useRoute } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useSharesStore } from '../stores/shares'
import { useAuthStore } from '../stores/auth'
import { useReconciliation } from './useReconciliation'
import { toast } from './useToast'
import { computeUsageData, formatExtraUsageAmount } from '../utils/usage'
import { useSettingsStore } from '../stores/settings'
import { getProviderHelpers, getProviderLabel, getProviderIcon, getProviderIconColor, getProviderWsHandler, getProviderStore } from '../providers'
import { playNotificationSound, sendBrowserNotification, isPageActive } from '../utils/notificationSounds'
import { installPresenceHeartbeat, isLocallyPresent } from '../utils/presence'
import { handleResyncRequired } from '../utils/resync'
import { truncateTitle } from '../utils/truncate'
import { toWorkspaceProjectId } from '../utils/workspaceIds'
import { compareVersions } from '../utils/version'
import { getProcessStateNotificationEffects } from '../utils/processStateNotifications.js'

// Lazy (async) toast body for peer events — toast.custom detects a component
// via its `setup` key, which an async wrapper has (see useToast.js precedent
// with SessionToastContent). Keeps the peer components out of the main chunk.
const PeerToastContent = defineAsyncComponent(() => import('../components/peer/PeerToastContent.vue'))

// A burst of artifact file edits doesn't carry fresh share "outdated" state
// (source_updated_at is computed server-side only when a share is serialized), so
// debounce-refresh the shares once edits settle — gated on an artifact share
// actually existing, to avoid an idle refetch. Keeps the per-row "outdated" tag,
// the "Push update to all" banner and the warning-tinted share buttons live.
const refreshSharesOnArtifactChange = useDebounceFn(() => {
    const shares = useSharesStore()
    if (shares.list.some((s) => s.kind === 'artifact')) shares.loadShares()
}, 1500)

// WebSocket close code sent by backend when authentication fails
const WS_CLOSE_AUTH_FAILURE = 4001

// localStorage key for tracking the last version the user was notified about
const UPDATE_NOTIFIED_VERSION_KEY = 'twicc-update-notified-version'

// Module-level state, preserved across HMR reloads via import.meta.hot.
// Without this, Vite HMR resets these variables to their initial values,
// causing "WebSocket not initialized" errors even though the connection is alive.
const __hmrState = import.meta.hot?.data ?? {}

// Module-level reference to the WebSocket send function
// Allows components to access it without going through the composable
if (!('wsSendFn' in __hmrState)) __hmrState.wsSendFn = null

// Track whether the last close was due to auth failure.
// When true, auto-reconnect is suppressed until explicitly reset.
if (!('lastCloseWasAuthFailure' in __hmrState)) __hmrState.lastCloseWasAuthFailure = false

// Server version received on first WebSocket connection.
// Compared on reconnections to detect backend upgrades.
if (!('serverVersion' in __hmrState)) __hmrState.serverVersion = null

// Debounced function for user draft notifications (10 seconds)
// This prevents spamming the server while still keeping the process alive
if (!('debouncedDraftNotifications' in __hmrState)) __hmrState.debouncedDraftNotifications = new Map() // sessionId -> debouncedFn

// Throttled functions for session_viewed notifications (30 seconds per session)
// First call passes immediately, subsequent calls are throttled
if (!('throttledViewedNotifications' in __hmrState)) __hmrState.throttledViewedNotifications = new Map() // sessionId -> throttledFn

// Cancelled session_viewed throttles — prevents trailing calls from firing after mark-unread
if (!('cancelledViewedThrottles' in __hmrState)) __hmrState.cancelledViewedThrottles = new Set() // sessionId

// Active user_turn toast tracking — prevents duplicates, allows cleanup from SessionToastContent
if (!('activeUserTurnToasts' in __hmrState)) __hmrState.activeUserTurnToasts = new Set() // sessionId

// Route's current session id, kept in sync by useWebSocket()'s watcher.
// Used by the visibility listener to know which session to flush/mark on
// tab focus/blur transitions, independent of any Vue component lifecycle.
if (!('currentRouteSessionId' in __hmrState)) __hmrState.currentRouteSessionId = null

// Previous active-state of the page, for detecting transitions in the
// visibility listener. Initialised to the current state on first module load.
if (!('lastVisibilityActive' in __hmrState)) __hmrState.lastVisibilityActive = isPageActive()

// Timestamp of the last message received on the WebSocket, plus the live
// status ref / open function of the current useWebSocket() instance. Used by
// the visibility probe below to detect a zombie socket. Kept on __hmrState so
// the module-level listener keeps working across HMR reloads.
if (!('lastWsMessageAt' in __hmrState)) __hmrState.lastWsMessageAt = 0
if (!('wsStatusRef' in __hmrState)) __hmrState.wsStatusRef = null
if (!('wsOpenFn' in __hmrState)) __hmrState.wsOpenFn = null
if (!('wsCloseFn' in __hmrState)) __hmrState.wsCloseFn = null
if (!('wsProbeInFlight' in __hmrState)) __hmrState.wsProbeInFlight = false

// How long the visibility probe waits for ANY inbound traffic before declaring
// the socket dead. Matches the heartbeat's pongTimeout.
const WS_PROBE_TIMEOUT_MS = 5000

// After closing a zombie socket, how long to wait for its onclose to land
// before force-reopening anyway.
const WS_PROBE_REOPEN_DEADLINE_MS = 2000

/**
 * Liveness probe, fired when the page becomes visible/focused again.
 *
 * On mobile, a socket silently killed during sleep can still read as OPEN
 * after wake-up: without this probe the connection dot stays green with NO
 * data flowing until the 30s heartbeat finally times out (~35s of stale UI
 * and no reconciliation). Send an immediate ping; if nothing at all comes
 * back within the timeout, force a reconnect cycle — which triggers the
 * usual reconciliation on reopen.
 */
function probeConnectionAlive() {
    if (__hmrState.wsProbeInFlight) return
    if (__hmrState.wsStatusRef?.value !== 'OPEN' || !__hmrState.wsSendFn) return

    __hmrState.wsProbeInFlight = true
    const receivedBefore = __hmrState.lastWsMessageAt
    try {
        __hmrState.wsSendFn(JSON.stringify({ type: 'ping' }))
    } catch {
        // Sending may throw synchronously on a dead socket — the timeout
        // below handles the reopen either way.
    }
    setTimeout(() => {
        __hmrState.wsProbeInFlight = false
        const gotTraffic = __hmrState.lastWsMessageAt > receivedBefore
        if (gotTraffic || __hmrState.wsStatusRef?.value !== 'OPEN') return

        console.warn('[ws] no traffic after visibility probe — recycling zombie socket')
        // Close first, reopen only once the old socket's onclose has landed:
        // calling open() right away would race that stale onclose, which sets
        // the status to CLOSED unconditionally — it could land AFTER the new
        // socket reports OPEN and leave the app believing it is disconnected
        // while the fresh socket is alive.
        __hmrState.wsCloseFn?.()
        const deadline = Date.now() + WS_PROBE_REOPEN_DEADLINE_MS
        const reopenWhenClosed = () => {
            if (__hmrState.wsStatusRef?.value === 'CLOSED' || Date.now() > deadline) {
                __hmrState.wsOpenFn?.()
            } else {
                setTimeout(reopenWhenClosed, 50)
            }
        }
        reopenWhenClosed()
    }, WS_PROBE_TIMEOUT_MS)
}

// Lazy-cached reference to the workspaces store factory. The static import is
// avoided to prevent a useWebSocket.js ↔ workspaces.js circular dependency
// (workspaces.js → data.js, and other modules in that subgraph end up needing
// this composable). Used synchronously by the `session_updated` permissive
// guard once resolved; until then the guard simply falls back to the existing
// project/all-projects checks.
let _useWorkspacesStoreRef = null
import('../stores/workspaces')
    .then(({ useWorkspacesStore }) => { _useWorkspacesStoreRef = useWorkspacesStore })
    .catch(err => console.warn('Failed to preload workspaces store reference', err))

// Permissive guard: `true` if `projectId` belongs to any workspace whose
// sessions have been fetched (the workspace scope `ws:<id>` is in
// `localState.projects` with `sessionsFetched=true`). Returns `false` until
// the lazy reference above resolves — in that window the existing
// `areProjectSessionsFetched` / `areAllProjectsSessionsFetched` checks still
// apply, so the fallback is consistent with prior behavior.
function isProjectInFetchedWorkspace(projectId, dataStore) {
    if (!_useWorkspacesStoreRef) return false
    const wsStore = _useWorkspacesStoreRef()
    for (const ws of wsStore.workspaces) {
        if (!ws.projectIds.includes(projectId)) continue
        if (dataStore.areProjectSessionsFetched(toWorkspaceProjectId(ws.id))) {
            return true
        }
    }
    return false
}

// Install the visibility/focus listener once at module load. HMR-safe: the
// flag survives reloads via __hmrState, so we never attach duplicates.
//
// Semantics: a session is "viewed" only while the tab is actually visible AND
// the OS window has focus. On every transition we update last_viewed_at for
// the session currently on the SPA route:
// - becoming inactive → flush with NOW (capture "I was watching up to here"
//   before going dark, so a pending throttle trailing that fires later doesn't
//   matter — and the listener's call uses _sendSessionViewed directly to
//   bypass the gates installed in notifySessionViewed / forceNotifySessionViewed)
// - becoming active → mark with NOW (the user is back, anything new in the
//   meantime that survived the gates can be cleared from the unread state)
if (!__hmrState.visibilityListenerInstalled) {
    const handleVisibilityChange = () => {
        const nowActive = isPageActive()
        const wasActive = __hmrState.lastVisibilityActive
        __hmrState.lastVisibilityActive = nowActive
        if (nowActive === wasActive) return
        // Coming back to the page: make sure the socket is actually alive
        // (a zombie OPEN socket after mobile sleep would otherwise sit green
        // and silent until the heartbeat times out).
        if (nowActive) probeConnectionAlive()
        const sessionId = __hmrState.currentRouteSessionId
        if (!sessionId) return
        if (wasActive && !nowActive) {
            _sendSessionViewed(sessionId, 'tab-hide-flush')
        } else {
            _sendSessionViewed(sessionId, 'tab-reactivated')
        }
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    window.addEventListener('focus', handleVisibilityChange)
    window.addEventListener('blur', handleVisibilityChange)
    __hmrState.visibilityListenerInstalled = true
}

// Install the presence heartbeat once at module load (HMR-safe via __hmrState).
// It pings the backend while a human is active here so "away-only" external
// notifications are held while you're at a TwiCC client. Pings are best-effort:
// the wrapper drops them silently while the WS is down (routine on mobile
// backgrounding/reconnect) instead of going through sendWsMessage, which would
// log a "not initialized" warning on every attempt. Presence resumes on the
// next ping after reconnect.
if (!__hmrState.presenceHeartbeatInstalled) {
    installPresenceHeartbeat(
        (data) => {
            if (__hmrState.wsSendFn) sendWsMessage(data)
        },
        // On resumed local activity, refresh "viewed" for the session currently
        // on the route. Covers the case where the user steps away and returns
        // without any focus/visibility change (the window stayed focused), which
        // no other path would catch — otherwise the session would stay falsely
        // unread while the user is looking right at it. Throttled + gated inside
        // notifySessionViewed; the resolved isPageActive() holds because input
        // only reaches a focused, visible tab.
        () => {
            const sessionId = __hmrState.currentRouteSessionId
            if (sessionId) notifySessionViewed(sessionId, 'presence-active')
        },
    )
    __hmrState.presenceHeartbeatInstalled = true
}

/**
 * Reactive flag: true when a backend version change was detected.
 * Used by App.vue to show the reload dialog.
 */
export const versionMismatchDetected = ref(false)

/**
 * Send a JSON message through the WebSocket connection.
 * Returns false if not connected.
 * @param {object} data - The data to send (will be JSON-stringified)
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendWsMessage(data) {
    if (!__hmrState.wsSendFn) {
        console.warn('WebSocket not initialized, cannot send message')
        return false
    }
    __hmrState.wsSendFn(JSON.stringify(data))
    return true
}

/**
 * Kill a running Claude process by session ID.
 * Only processes in 'starting' or 'assistant_turn' state can be killed.
 * The state change to 'dead' will be received via the process_state message.
 * @param {string} sessionId - The session ID of the process to kill
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function killProcess(sessionId, { force = false } = {}) {
    return sendWsMessage({
        type: 'kill_process',
        session_id: sessionId,
        force,
    })
}

/**
 * Stop a running subagent (Task) via the SDK's stop_task.
 * @param {string} sessionId - The parent session ID that spawned the subagent
 * @param {string} subagentId - The subagent ID (from AgentLink)
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function stopSubagent(sessionId, subagentId) {
    return sendWsMessage({
        type: 'stop_subagent',
        session_id: sessionId,
        subagent_id: subagentId
    })
}

/**
 * Interrupt a session's current turn WITHOUT killing the process.
 * Aborts whatever the agent is doing now and drops it back to USER_TURN,
 * ready for the next message. Only meaningful while in ASSISTANT_TURN; the
 * backend no-ops otherwise. The resulting state arrives via process_state.
 * @param {string} sessionId - The session whose current turn to interrupt
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function interruptSession(sessionId) {
    return sendWsMessage({
        type: 'interrupt_session',
        session_id: sessionId,
    })
}

/**
 * Request a title suggestion for a session.
 * The result will arrive via WebSocket as title_suggested message.
 * @param {string} sessionId - The session ID (draft or canonical)
 * @param {string|null} prompt - Optional prompt text (for draft/new sessions)
 * @param {string} systemPrompt - System prompt with {text} placeholder
 * @returns {boolean} - True if message was sent, false if no provider could be resolved or WS is down
 */
export function requestTitleSuggestion(sessionId, prompt = null, systemPrompt) {
    // ``provider`` travels in the payload because draft sessions don't exist in
    // the backend DB yet — the backend can't resolve them via the ``Session``
    // table, so the frontend (which knows the provider from draft creation
    // onwards) hands it over explicitly. Resolved against the same store entry
    // for both draft and real sessions.
    const provider = useDataStore().getSession(sessionId)?.provider
    if (!provider) {
        console.warn(`[useWebSocket] requestTitleSuggestion: unknown provider for session ${sessionId}`)
        return false
    }
    const message = { type: 'suggest_title', sessionId, provider, systemPrompt }
    if (prompt) {
        message.prompt = prompt
    }
    return sendWsMessage(message)
}

/**
 * Notify the server that the user is actively preparing a message.
 * This resets the inactivity timeout for the process.
 * Debounced to 10 seconds per session to avoid spamming.
 * @param {string} sessionId - The session ID
 */
export function notifyUserDraftUpdated(sessionId) {
    if (!sessionId) return

    // Get or create debounced function for this session
    if (!__hmrState.debouncedDraftNotifications.has(sessionId)) {
        const debouncedFn = useDebounceFn(() => {
            sendWsMessage({
                type: 'user_draft_updated',
                session_id: sessionId
            })
        }, 10000) // 10 seconds debounce
        __hmrState.debouncedDraftNotifications.set(sessionId, debouncedFn)
    }

    // Call the debounced function
    __hmrState.debouncedDraftNotifications.get(sessionId)()
}

/**
 * Send a session_viewed message to the backend and update the store optimistically.
 * @param {string} sessionId - The session ID
 * @param {string} reason - Why this notification is being sent (for debugging)
 */
function _sendSessionViewed(sessionId, reason) {
    sendWsMessage({
        type: 'session_viewed',
        session_id: sessionId,
        viewed_at: new Date().toISOString(),
        reason,
    })
    // Optimistic update: set last_viewed_at locally immediately
    const store = useDataStore()
    const session = store.getSession(sessionId)
    if (session) {
        store.updateSession({ ...session, last_viewed_at: new Date().toISOString() })
    }
}

/**
 * Notify the server that the user is viewing a session.
 * Updates last_viewed_at in the database for "unread" detection.
 * Throttled to 30 seconds per session: first call fires immediately (leading),
 * and a trailing call is guaranteed at the end of the window if any calls
 * were made during the throttle period. This ensures last_viewed_at stays
 * fresh even when content arrives shortly after navigation.
 * @param {string} sessionId - The session ID
 * @param {string} reason - Why this notification is being sent (for debugging)
 * @param {object} [options]
 * @param {boolean} [options.requirePresence=false] - When true, only mark viewed
 *   if a human is genuinely present here (isLocallyPresent()), not merely if the
 *   tab is visible+focused. Use for passive auto-marks so an unattended desktop
 *   does not steal the global last_viewed_at from the user's actual device.
 */
export function notifySessionViewed(sessionId, reason, { requirePresence = false } = {}) {
    if (!sessionId) return
    // Skip when the page is not actively watched (tab hidden or window unfocused).
    // The visibility listener installed at module load handles flushing on hide
    // and re-marking on return, so there's nothing more to do here.
    if (!isPageActive()) return
    // For passive auto-marks (assistant finished, items streaming in), also
    // require a genuinely present human on this device — a tab left visible+
    // focused but unattended must NOT advance the (global) last_viewed_at, or it
    // steals "read" from the device where the user actually is. Explicit marks
    // (navigation, focus return) pass requirePresence=false and are unaffected.
    if (requirePresence && !isLocallyPresent()) return

    // Clear any cancellation from a previous mark-unread action
    __hmrState.cancelledViewedThrottles.delete(sessionId)

    // Store the latest reason + presence requirement so the throttled callback can use them
    __hmrState.lastViewedReason = __hmrState.lastViewedReason || {}
    __hmrState.lastViewedReason[sessionId] = reason
    __hmrState.lastViewedRequirePresence = __hmrState.lastViewedRequirePresence || {}
    __hmrState.lastViewedRequirePresence[sessionId] = requirePresence

    // Get or create throttled function for this session
    if (!__hmrState.throttledViewedNotifications.has(sessionId)) {
        const throttledFn = useThrottleFn(() => {
            // Skip if cancelled by mark-unread (trailing call after cancellation)
            if (__hmrState.cancelledViewedThrottles.has(sessionId)) return
            // Skip a trailing call that fires after the user has left the page —
            // the timestamp would otherwise mask new content that arrived while
            // the tab was hidden, leaving the session falsely marked as read.
            if (!isPageActive()) return
            // Same presence requirement on the trailing edge: the user may have
            // stepped away during the 30s window while the tab kept focus.
            if (__hmrState.lastViewedRequirePresence?.[sessionId] && !isLocallyPresent()) return
            _sendSessionViewed(sessionId, __hmrState.lastViewedReason?.[sessionId] || 'throttle-trailing')
        }, 30000, true) // 30s throttle, trailing=true (leading=true by default)
        __hmrState.throttledViewedNotifications.set(sessionId, throttledFn)
    }

    // Call the throttled function
    __hmrState.throttledViewedNotifications.get(sessionId)()
}

/**
 * Force-send a session_viewed notification, bypassing the throttle.
 * Used when leaving a session (onDeactivated) to ensure last_viewed_at
 * is up to date before the session becomes visible in the sidebar as potentially unread.
 * @param {string} sessionId - The session ID
 * @param {string} reason - Why this notification is being sent (for debugging)
 * @param {object} [options]
 * @param {boolean} [options.requirePresence=false] - See notifySessionViewed.
 */
export function forceNotifySessionViewed(sessionId, reason, { requirePresence = false } = {}) {
    if (!sessionId) return
    // Skip when the page is not actively watched. The visibility listener
    // already handles flushing/marking on tab transitions, so all the public
    // entry points stay gated symmetrically — only the listener bypasses.
    if (!isPageActive()) return
    // Passive auto-marks (assistant finished) also require a present human, so
    // an unattended-but-focused desktop does not advance the global last_viewed_at.
    if (requirePresence && !isLocallyPresent()) return
    // Skip if cancelled by mark-unread (e.g. onDeactivated fires after mark-unread navigates away)
    if (__hmrState.cancelledViewedThrottles.has(sessionId)) return
    // Cancel any pending trailing throttle call to prevent a stale session_viewed
    // from firing after the user has left this session. Deleting from the Map
    // prevents reuse, and adding to the cancel Set ensures the trailing callback
    // (whose timer is still alive inside useThrottleFn) is skipped when it fires.
    __hmrState.throttledViewedNotifications.delete(sessionId)
    __hmrState.cancelledViewedThrottles.add(sessionId)
    _sendSessionViewed(sessionId, reason)
}

/**
 * Mark a session as read or unread.
 * Mark as read: sets last_viewed_at = now.
 * Mark as unread: sets last_new_content_at = now, clears last_viewed_at.
 * The backend broadcasts session_updated to all clients after the change.
 * @param {string} sessionId - The session ID
 * @param {boolean} unread - True to mark as unread, false to mark as read
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function markSessionReadState(sessionId, unread) {
    if (!sessionId) return false
    return sendWsMessage({
        type: 'mark_session_read_state',
        session_id: sessionId,
        unread,
    })
}

/**
 * Cancel any pending session_viewed throttle for a session.
 * Called when marking a session as unread to prevent a trailing
 * throttle call from immediately resetting last_viewed_at.
 * @param {string} sessionId - The session ID
 */
export function cancelSessionViewedThrottle(sessionId) {
    __hmrState.throttledViewedNotifications.delete(sessionId)
    __hmrState.cancelledViewedThrottles.add(sessionId)
}

/**
 * Remove a session from the active user_turn toast tracking.
 * Called by SessionToastContent when the toast is dismissed (auto or manual).
 * @param {string} sessionId - The session ID
 */
export function clearUserTurnToast(sessionId) {
    __hmrState.activeUserTurnToasts.delete(sessionId)
}

/**
 * Send synced settings to the backend for persistence in settings.json.
 * The backend broadcasts an accepted update to all connected clients.
 * @param {Object} settings - The synced settings key-value pairs
 * @param {number} baseVersion - The current settings version
 * @param {string} [requestId] - Correlation ID for one direct result
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendSyncedSettings(settings, baseVersion, requestId) {
    return sendWsMessage({
        type: 'update_synced_settings', settings, baseVersion, request_id: requestId,
    })
}

/**
 * Send workspaces to the backend for persistence.
 * The backend will broadcast the updated workspaces to all connected clients.
 * @param {Array} workspaces - The workspaces array
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendWorkspaces(workspaces) {
    sendWsMessage({ type: 'update_workspaces', workspaces })
}

/**
 * Send the named-layouts catalog to the backend for persistence.
 * The backend overwrites layouts.json and broadcasts the catalog to all clients.
 * @param {Array} layouts - The layouts array
 */
export function sendLayouts(layouts) {
    sendWsMessage({ type: 'update_layouts', layouts })
}

/**
 * Send terminal config to the backend for persistence.
 * The backend will broadcast the updated config to all connected clients.
 * @param {Object} config - The terminal config key-value pairs
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendTerminalConfig(config) {
    return sendWsMessage({ type: 'update_terminal_config', config })
}

/**
 * Send message snippets config to the backend for persistence.
 * The backend will broadcast the updated config to all connected clients.
 * @param {Object} config - The message snippets config
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendMessageSnippetsConfig(config) {
    return sendWsMessage({ type: 'update_message_snippets', config })
}

/**
 * Push a provider's agent settings presets config to the backend for
 * persistence. The backend writes it to the per-provider presets file
 * (resolved via ``BaseProviderHelpers.get_settings_presets_path``) and
 * broadcasts ``agent_settings_presets_updated`` to every connected
 * client.
 * @param {string} provider - Provider wire key (e.g. ``"claude_code"``)
 * @param {Object} config - ``{ presets: [...] }``
 * @returns {boolean} True if message was sent, false if not connected.
 */
export function sendUpdateAgentSettingsPresets(provider, config) {
    return sendWsMessage({ type: 'update_agent_settings_presets', provider, config })
}

/**
 * Push the seen-tips state to the backend for persistence and broadcast.
 * @param {Object} seenTips - { <tip_key>: <ISO timestamp> }
 * @returns {boolean} True if message was sent, false if not connected.
 */
export function sendUpdateSeenTips(seenTips) {
    return sendWsMessage({ type: 'update_seen_tips', seen_tips: seenTips })
}

/**
 * Record the user's dismissal of a provider-status toast: the backend
 * persists it in providers-status.json and broadcasts the file, which clears
 * that toast on every tab and device.
 * @param {string} provider - Provider wire key.
 * @param {{ started_at: string, status: string }} episode - The dismissed episode.
 * @returns {boolean} True if the message was sent, false if not connected.
 */
export function sendAcknowledgeProviderStatus(provider, episode) {
    return sendWsMessage({ type: 'acknowledge_provider_status', provider, episode })
}

/**
 * Persist + broadcast the seen-help state (sibling of sendUpdateSeenTips).
 * @param {Object} seenHelp - { <help_key>: <ISO timestamp> }
 * @returns {boolean} True if message was sent, false if not connected.
 */
export function sendUpdateSeenHelp(seenHelp) {
    return sendWsMessage({ type: 'update_seen_help', seen_help: seenHelp })
}

/**
 * Acknowledge that the user has seen the forced changelog for a version.
 * @param {string} version - The version that was displayed
 * @returns {boolean} - True if message was sent
 */
export function sendChangelogSeen(version) {
    return sendWsMessage({
        type: 'changelog_seen',
        version
    })
}

// Pending resolve callbacks for path validation request-response round-trips.
// Kept on __hmrState so a Vite HMR reload of this module doesn't strand the
// WebSocket onMessage handler (captured in the old useWebSocket() closure)
// looking at a freshly-nulled resolver while sendValidate*() in the new
// module instance just wrote to a different one — the symptom is an Apply
// button stuck on its spinner because the response is dispatched but the
// matching Promise never resolves.
if (!('usageDumpPathValidateResolve' in __hmrState)) __hmrState.usageDumpPathValidateResolve = null
if (!('tmuxConfigPathValidateResolve' in __hmrState)) __hmrState.tmuxConfigPathValidateResolve = null
// Usage *file* (read mode) validation can run concurrently for several
// providers, so the resolver is keyed by provider rather than singular.
if (!('usageFileValidateResolvers' in __hmrState)) __hmrState.usageFileValidateResolvers = new Map()
if (!('telemetryPayloadResolve' in __hmrState)) __hmrState.telemetryPayloadResolve = null
if (!('telemetryInstanceIdResetResolve' in __hmrState)) __hmrState.telemetryInstanceIdResetResolve = null

/**
 * Validate a usage dump file path (write mode) on the backend.
 * @param {string} filePath - The file path to validate
 * @returns {Promise<{valid: boolean, message: string}>}
 */
export function sendValidateUsageDumpPath(filePath) {
    return new Promise((resolve) => {
        __hmrState.usageDumpPathValidateResolve = resolve
        const sent = sendWsMessage({ type: 'validate_usage_dump_path', file_path: filePath })
        if (!sent) {
            __hmrState.usageDumpPathValidateResolve = null
            resolve({ valid: false, message: 'Not connected' })
        }
    })
}

/**
 * Validate a usage *read* file path for a given provider on the backend.
 * The reply is dispatched back via ``usage_file_validated`` and routed
 * to this provider's pending resolver.
 * @param {string} provider - Provider wire key (e.g. ``"claude_code"``)
 * @param {string} filePath - The file path to validate
 * @returns {Promise<{valid: boolean, message: string}>}
 */
export function sendValidateUsageFile(provider, filePath) {
    return new Promise((resolve) => {
        __hmrState.usageFileValidateResolvers.set(provider, resolve)
        const sent = sendWsMessage({ type: 'validate_usage_file', provider, file_path: filePath })
        if (!sent) {
            __hmrState.usageFileValidateResolvers.delete(provider)
            resolve({ valid: false, message: 'Not connected' })
        }
    })
}

/**
 * Validate a tmux config file path on the backend.
 * @param {string} filePath - The file path to validate
 * @returns {Promise<{valid: boolean, message: string}>}
 */
export function sendValidateTmuxConfigPath(filePath) {
    return new Promise((resolve) => {
        __hmrState.tmuxConfigPathValidateResolve = resolve
        const sent = sendWsMessage({ type: 'validate_tmux_config_path', file_path: filePath })
        if (!sent) {
            __hmrState.tmuxConfigPathValidateResolve = null
            resolve({ valid: false, message: 'Not connected' })
        }
    })
}

/**
 * Request the last-sent (or a fresh preview, when telemetry has never sent
 * yet) anonymous telemetry payload from the backend.
 * @returns {Promise<{payload: Object|null, sent_at: string|null, preview: boolean}>}
 */
export function requestTelemetryPayload() {
    return new Promise((resolve) => {
        __hmrState.telemetryPayloadResolve = resolve
        const sent = sendWsMessage({ type: 'get_telemetry_payload' })
        if (!sent) {
            __hmrState.telemetryPayloadResolve = null
            resolve({ payload: null, sent_at: null, preview: false })
        }
    })
}

/**
 * Ask the backend to reset (regenerate) the anonymous telemetry instance id.
 * @returns {Promise<{instance_id: string|null}>}
 */
export function requestTelemetryInstanceIdReset() {
    return new Promise((resolve) => {
        __hmrState.telemetryInstanceIdResetResolve = resolve
        const sent = sendWsMessage({ type: 'reset_telemetry_instance_id' })
        if (!sent) {
            __hmrState.telemetryInstanceIdResetResolve = null
            resolve({ instance_id: null })
        }
    })
}

/**
 * Build a notification body string from the enriched WebSocket message.
 * Format: "Project: <name>\nSession: <title>" (both truncated).
 *
 * When the project is a git worktree, the backend also injects
 * project_parent_name; the project line then reads "<parent> › <worktree>"
 * — a plain-text stand-in for the code-branch badge shown in in-app toasts
 * (native OS notifications can't render icons). Each part is truncated
 * shorter so the combined line stays readable.
 *
 * Uses session_title / project_name / project_parent_name injected by the
 * backend so that notifications display correctly even when session data
 * isn't loaded in the frontend store (e.g. on the projects list page).
 *
 * @param {Object} msg - The WebSocket process_state message (with session_title / project_name / project_parent_name)
 * @returns {string}
 */
function buildNotificationBody(msg) {
    const sessionTitle = truncateTitle(msg.session_title, 50)
    const projectName = msg.project_parent_name
        ? `${truncateTitle(msg.project_parent_name, 40)} › ${truncateTitle(msg.project_name, 40)}`
        : truncateTitle(msg.project_name, 50)
    return `Project: ${projectName}\nSession: ${sessionTitle}`
}

/**
 * Show toast notification and trigger external notifications (sound + browser)
 * for process state changes.
 * @param {Object} msg - The WebSocket process_state message (enriched with session_title / project_name)
 * @param {Object|null} previousState - The previous process state (before update), null if new process
 * @param {Object} route - The current Vue route object (used to suppress toasts when viewing the session)
 */
function notifyProcessStateChange(msg, previousState, route) {
    const sessionId = msg.session_id
    const settings = useSettingsStore()
    const providerLabel = getProviderLabel(msg.provider)
    const isViewingSession = route?.params?.sessionId === sessionId
    const effects = getProcessStateNotificationEffects(msg, previousState, {
        isViewingSession,
        userTurnToastEnabled: settings.notifUserTurnToast,
        userTurnBrowserEnabled: settings.notifUserTurnBrowser,
        pendingRequestBrowserEnabled: settings.notifPendingRequestBrowser,
    })

    // --- Transition to user_turn: "<Provider> finished working" ---
    // If viewing the session, force-update last_viewed_at immediately.
    // This prevents stale state if the user locks their phone or switches
    // apps before the trailing throttle call fires. requirePresence: a
    // desktop left open+focused but unattended must not auto-mark read here.
    if (effects.markViewed) {
        forceNotifySessionViewed(sessionId, 'process-user-turn', { requirePresence: true })
    }
    // In-app toast when the user is on TwiCC but not viewing this session
    if (effects.showUserTurnToast && !__hmrState.activeUserTurnToasts.has(sessionId)) {
        __hmrState.activeUserTurnToasts.add(sessionId)
        toast.session(sessionId, {
            type: 'info',
            title: `${providerLabel} finished working`,
            duration: 15000,
            autoDismiss: true,
            showActions: true,
        })
    }
    // Sound notification
    if (effects.playUserTurnSound) {
        playNotificationSound(settings.notifUserTurnSound)
    }
    // Browser notification
    if (effects.sendUserTurnBrowser) {
        sendBrowserNotification(
            `${providerLabel} finished working`,
            buildNotificationBody(msg),
        )
    }

    // --- Pending request: "<Provider> needs your attention" ---
    // Notify when at least one new pending request appeared (transition from
    // 0 → ≥1, or any growth in count for parallel concurrency-safe tools).
    if (effects.showPendingRequestToast) {
        // Use the freshest (last-arrived) request to title the toast.
        const latest = msg.pending_requests[effects.newPendingCount - 1]
        const pendingTitle = latest?.request_type === 'ask_user_question'
            ? `🖐️ ${providerLabel} has a question for you`
            : `🖐️ ${providerLabel} needs your approval`
        toast.session(sessionId, { type: 'warning', title: pendingTitle })
    }

    // Sound notification
    if (effects.playPendingRequestSound) {
        playNotificationSound(settings.notifPendingRequestSound)
    }
    // Browser notification
    if (effects.sendPendingRequestBrowser) {
        sendBrowserNotification(
            `${providerLabel} needs your attention`,
            buildNotificationBody(msg),
        )
    }

    // --- Process death notifications (toast only, unchanged) ---
    if (msg.state === 'dead') {
        // Only notify for errors and timeouts, not for normal lifecycle
        if (msg.kill_reason === 'error') {
            toast.session(sessionId, {
                type: 'error',
                title: `${providerLabel} terminated due to error`,
                errorMessage: msg.error || 'Unknown error',
            })
        } else if (msg.kill_reason === 'timeout_starting') {
            toast.session(sessionId, {
                type: 'error',
                title: `${providerLabel} stopped: failed to start within 1 minute`,
            })
        } else if (msg.kill_reason === 'timeout_assistant_turn') {
            toast.session(sessionId, {
                type: 'warning',
                title: `${providerLabel} stopped: no activity for 2 hours`,
            })
        } else if (msg.kill_reason === 'timeout_assistant_turn_absolute') {
            toast.session(sessionId, {
                type: 'warning',
                title: `${providerLabel} stopped: running for over 6 hours`,
            })
        }
        // No per-session toast for kill_reason === 'auth_required':
        // the global "Claude CLI not authenticated" toast and the sidebar
        // callout (driven by useClaudeCodeStore().authenticated) already
        // surface the state, and the dead session itself shows an inline error.

        // If a send was still awaiting its real user_message line when the
        // agent died, the message may never have been processed — offer a
        // manual restore through the composer callout. Late failures are
        // ambiguous (delivery cannot be ruled out), so never auto-restore.
        if (['error', 'startup-failed', 'timeout_starting'].includes(msg.kill_reason)) {
            useDataStore().failPendingSendsForSession(sessionId, {
                code: 'agent_died',
                message: 'The agent stopped before confirming your message was received.',
            })
        }
    }
}

/**
 * Handle update_available message from the backend.
 * Shows a persistent toast if the user hasn't been notified for this version yet.
 * Deduplication is done via localStorage to survive page reloads.
 */
function handleUpdateAvailable(msg) {
    const { latest_version, release_url } = msg
    if (!latest_version) return

    // Check localStorage: skip if already notified for this version (or newer).
    // Compare numerically — a string comparison gets "1.9.2" >= "1.10.0" wrong
    // (true), which would silence the toast for any double-digit minor/patch bump.
    const lastNotified = localStorage.getItem(UPDATE_NOTIFIED_VERSION_KEY)
    if (lastNotified && compareVersions(lastNotified, latest_version) >= 0) return

    // Store the version so we don't notify again
    localStorage.setItem(UPDATE_NOTIFIED_VERSION_KEY, latest_version)

    // Show persistent toast with upgrade instructions
    const settings = useSettingsStore()
    const upgradeHint = settings.isUvxMode
        ? 'Stop and re-run: <code style="background: var(--wa-color-neutral-80); padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.9em;">uvx twicc@latest</code>'
        : 'Update TwiCC (with <code style="background: var(--wa-color-neutral-80); padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.9em;">uv tool upgrade twicc</code> if installed with uv) and restart'
    toast.custom({
        type: 'info',
        title: `TwiCC v${latest_version} is available`,
        duration: Infinity,
        html: `
            <div style="display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.25rem;">
                <span>${upgradeHint}</span>
                <a href="#" onclick="window.dispatchEvent(new CustomEvent('open-changelog')); return false;" style="color: var(--wa-color-primary-600); text-decoration: underline;">View changes</a>
            </div>
        `,
    })
}

/**
 * Format the credit-detail line of the "extra usage started" alert from the
 * backend ``extra_usage_started`` event payload.
 *
 * Two display modes, like the sidebar ring: Anthropic-style providers report
 * used/limit credits (``utilization`` set), Codex-style providers report only a
 * remaining balance. When the event carries a currency, the used/limit figures
 * are money rather than credits. Reads the snake_case fields the backend sends.
 *
 * @param {object|null|undefined} extra - The event's ``extra_usage`` block.
 * @returns {string} A short credit-detail line, or '' when no figure is available.
 */
function formatExtraUsageStartedDetail(extra) {
    if (!extra) return ''
    if (extra.utilization != null) {
        const usedAmount = extra.currency
            ? formatExtraUsageAmount(extra.used_credits ?? 0, extra.decimal_places)
            : null
        const unit = usedAmount != null ? extra.currency : 'credits'
        const used = usedAmount ?? (extra.used_credits ?? 0)
        const limit = usedAmount != null
            ? formatExtraUsageAmount(extra.monthly_limit, extra.decimal_places)
            : extra.monthly_limit
        return limit != null
            ? `${used} of ${limit} ${unit} used.`
            : `${used} ${unit} used.`
    }
    if (extra.remaining_credits != null) {
        return `${Math.round(extra.remaining_credits)} credits remaining.`
    }
    return ''
}

export function useWebSocket() {
    const store = useDataStore()
    const route = useRoute()
    const { onReconnected } = useReconciliation()

    // Mirror the current route's session id into module-level state so the
    // visibility listener (installed at module load, outside any composable)
    // can flush/mark the right session on tab focus/blur transitions.
    watch(
        () => route.params.sessionId,
        (id) => { __hmrState.currentRouteSessionId = id || null },
        { immediate: true },
    )

    // Track if we've ever been connected (to distinguish first connect from reconnect)
    let wasConnected = false

    // Use wss:// for https, ws:// for http
    // immediate: false — connection is deferred until openWs() is called by App.vue
    // after authentication is confirmed. This prevents WebSocket errors when the
    // backend rejects unauthenticated connections.
    const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const { status, send, open, close } = useVueWebSocket(`${wsProtocol}//${location.host}/ws/`, {
        immediate: false,
        autoReconnect: {
            // Don't reconnect if the last close was an auth failure.
            // For all other cases, always retry (equivalent to Infinity).
            retries: () => !__hmrState.lastCloseWasAuthFailure,
            delay: 1000,
        },
        heartbeat: {
            message: JSON.stringify({ type: 'ping' }),
            responseMessage: JSON.stringify({ type: 'pong' }),
            interval: 30000,
            pongTimeout: 5000
        },
        onDisconnected(ws, event) {
            if (event.code === WS_CLOSE_AUTH_FAILURE) {
                console.warn('WebSocket closed with auth failure code (4001), stopping reconnection')
                __hmrState.lastCloseWasAuthFailure = true
                handleWsAuthFailure()
            }
        },
        onMessage(ws, event) {
            // Any inbound traffic proves the socket is alive (visibility probe).
            __hmrState.lastWsMessageAt = Date.now()
            let msg
            try {
                msg = JSON.parse(event.data)
            } catch (e) {
                console.warn('WebSocket received non-JSON message:', event.data)
                return
            }
            try {
                handleMessage(msg)
            } catch (e) {
                console.error('Error handling WebSocket message:', e, '\nMessage was:', msg)
            }
        }
    })

    // Expose the live status ref and open/close functions to the module-level
    // visibility probe (re-assigned on every useWebSocket() call so they stay
    // fresh across HMR reloads of this module).
    __hmrState.wsStatusRef = status
    __hmrState.wsOpenFn = open
    __hmrState.wsCloseFn = close

    /**
     * Handle WebSocket authentication failure.
     * Re-checks auth state to confirm, then either redirects to login
     * or reconnects if the session is actually still valid.
     */
    async function handleWsAuthFailure() {
        const authStore = useAuthStore()
        // Re-check auth state — the session might still be valid
        // (e.g., transient WS issue, not a real auth problem)
        await authStore.checkAuthOnce()
        if (authStore.needsLogin) {
            authStore.handleUnauthorized()
            // Lazy import to avoid circular dependency
            // (router imports views → views import components → components import useWebSocket)
            const { router } = await import('../router')
            const currentPath = router.currentRoute.value.fullPath
            if (router.currentRoute.value.name !== 'login') {
                router.replace({ name: 'login', query: { redirect: currentPath } })
            }
        } else {
            // Auth is actually fine, the WS close was spurious — reconnect
            __hmrState.lastCloseWasAuthFailure = false
            open()
        }
    }

    function handleMessage(msg) {
        switch (msg.type) {
            case 'server_version':
                console.log(`[TwiCC] Server version: ${msg.version}`)
                store.setCurrentVersion(msg.version)
                // Store previous changelog version for combined changelog entry
                if (msg.previous_last_changelog_version_seen) {
                    store.setPreviousChangelogVersion(msg.previous_last_changelog_version_seen)
                }
                // Auto-show changelog on version update (backend decides when)
                if (msg.show_changelog_for_version) {
                    store.setPendingChangelogVersion(msg.show_changelog_for_version)
                }
                if (__hmrState.serverVersion === null) {
                    // First connection — store the version
                    __hmrState.serverVersion = msg.version
                } else if (msg.version !== __hmrState.serverVersion) {
                    // Reconnection with different version — trigger reload
                    versionMismatchDetected.value = true
                }
                break
            case 'auth_failure':
                // Fallback for auth rejection: the backend sends this message
                // before closing with code 4001. Some proxies (e.g. Vite dev
                // proxy) strip the close code, so this message ensures we
                // detect auth failure even when the close code is lost.
                console.warn('WebSocket received auth_failure message, treating as auth rejection')
                __hmrState.lastCloseWasAuthFailure = true
                handleWsAuthFailure()
                break
            case 'project_added':
                store.addProject(msg.project)
                break
            case 'project_updated':
                store.updateProject(msg.project)
                break
            case 'session_bound': {
                // The backend has confirmed the provider-side canonical id
                // bound to a local draft. If the canonical session is already
                // in the store, finalize immediately; otherwise arm a pending
                // draft binding that will fire when the watcher lands the
                // session via `session_updated` (handled in addSession /
                // updateSession). For providers that accept a client-supplied
                // id (Claude Code), draft_session_id === session_id and
                // bindDraftSession is a no-op.
                const draftId = msg.draft_session_id
                const sessionId = msg.session_id
                if (store.getSession(sessionId)) {
                    store.bindDraftSession(draftId, sessionId)
                } else {
                    store.localState.pendingDraftBindings[draftId] = sessionId
                }
                break
            }
            case 'session_updated': {
                const existingSession = store.getSession(msg.session.id)
                if (existingSession?.draft) {
                    // Draft session confirmed by the backend — update with real data.
                    // Preserve locally-set title if it differs from what the backend has.
                    // This handles the race condition where the user sets a title via
                    // the rename dialog (needs-title flow) before the first session_updated
                    // arrives: the dialog stores the title locally, but the backend never
                    // received it (title wasn't in the send_message payload). We keep it
                    // in the store to avoid a visual flash, then persist it via API.
                    // Note: we compare against backend title (not just check for null)
                    // because the backend may already have a default title (first user message).
                    const localTitle = existingSession.title
                    const hasOrphanedLocalTitle = localTitle && localTitle !== msg.session.title
                    store.updateSession({
                        ...msg.session,
                        draft: false,
                        // Keep local title to avoid flashing null before renameSession restores it
                        ...(hasOrphanedLocalTitle ? { title: localTitle } : {}),
                    })
                    // Persist the local title to the backend (session now exists in DB)
                    if (hasOrphanedLocalTitle) {
                        store.renameSession(msg.session.project_id, msg.session.id, localTitle)
                    }

                } else if (existingSession) {
                    store.updateSession(msg.session)

                } else if (
                    store.areProjectSessionsFetched(msg.session.project_id)
                    || store.areAllProjectsSessionsFetched
                    || isProjectInFetchedWorkspace(msg.session.project_id, store)
                ) {
                    // Session not yet in store but sessions for this project have been
                    // fetched — add it (new session or missed earlier update). The
                    // workspace check covers the case where the user is browsing a
                    // workspace and the cascade in loadSessions hasn't yet marked the
                    // session's project (e.g. project auto-added after the load).
                    store.addSession(msg.session)
                }
                break
            }
            case 'artifacts_available': {
                // The ArtifactsWatcher detected the session's first artifact on
                // disk. Flip has_artifacts on (monotonic) so an open session view
                // reveals its Artifacts tab live. Ignored if the session isn't in
                // the store yet — a later fetch carries has_artifacts via the
                // serializer.
                const s = store.getSession(msg.session_id)
                if (s && !s.has_artifacts) {
                    store.updateSession({ ...s, has_artifacts: true, artifacts_dir: msg.artifacts_dir })
                }
                break
            }
            case 'artifact_files_changed': {
                // The ArtifactsWatcher relayed file change(s) under a session
                // whose Artifacts tab is already known. Forward to the matching
                // SessionView (which filters on its own session_id) so an open
                // Artifacts tab can live-refresh its tree and reload a rendered
                // HTML page. Paths are relative to the session's artifacts dir.
                window.dispatchEvent(new CustomEvent('twicc:artifact-files-changed', {
                    detail: { sessionId: msg.session_id, paths: msg.paths || [] },
                }))
                // Refresh shares so an artifact edit re-marks its links as outdated
                // (badge + warning-tinted share button) without a reconnect.
                refreshSharesOnArtifactChange()
                break
            }
            case 'plan_available': {
                // The provider's plans watcher saw the session's plan file
                // appear. Flip has_plan on so an open session view reveals its
                // Plan tab live. Ignored if the session isn't in the store yet —
                // a later fetch carries has_plan via the serializer. NOT
                // monotonic (see plan_gone).
                const s = store.getSession(msg.session_id)
                if (s && !s.has_plan) {
                    store.updateSession({ ...s, has_plan: true })
                }
                break
            }
            case 'plan_gone': {
                // The plan file was deleted — drop has_plan so the Plan tab
                // disappears (the SessionView absent-tab redirect navigates away
                // if it was active). Unlike artifacts, plan presence is two-way.
                const s = store.getSession(msg.session_id)
                if (s && s.has_plan) {
                    store.updateSession({ ...s, has_plan: false })
                }
                break
            }
            case 'plan_changed': {
                // The plan file's content changed. Forward to the matching
                // PlanPane (which filters on its own session_id) so an open Plan
                // tab live-refreshes.
                window.dispatchEvent(new CustomEvent('twicc:plan-changed', {
                    detail: { sessionId: msg.session_id },
                }))
                break
            }
            case 'artifact_bookmarks_updated': {
                // Full snapshot pushed on every WS connect (incl. reconnects):
                // replace the whole set so changes missed while disconnected
                // (incl. removals) are reflected. Singular *_updated/_removed
                // below handle the incremental live case.
                store.setArtifactBookmarks(msg.bookmarks || [])
                break
            }
            case 'artifact_bookmark_updated': {
                store.upsertArtifactBookmark(msg.bookmark)
                break
            }
            case 'artifact_bookmark_removed': {
                store.removeArtifactBookmark(msg.bookmark_id)
                // The bookmark's shares cascaded away server-side; drop them locally
                // too (only the bookmark removal is broadcast).
                useSharesStore().removeForBookmark(msg.bookmark_id)
                break
            }
            case 'artifact_bookmark_denials_updated': {
                // Denial rows changed for this bookmark; an open bookmark dialog
                // refetches its Network access list (nothing stored globally).
                window.dispatchEvent(new CustomEvent('twicc:network-denials-updated', {
                    detail: { bookmarkId: msg.bookmark_id },
                }))
                break
            }
            case 'shares_updated': {
                useSharesStore().setShares(msg.shares || [])
                break
            }
            case 'share_updated': {
                useSharesStore().upsertShare(msg.share)
                break
            }
            case 'share_removed': {
                useSharesStore().removeShare(msg.share_id)
                break
            }
            case 'resync_required':
                // The server's WebSocket layer had to discard updates for this
                // client and flushed whatever was still queued. We cannot know
                // what was lost, so the local state is torn in an undescribable
                // way — the policy (reload, defer to a visible tab, or ask)
                // lives in utils/resync.js.
                handleResyncRequired(msg.reason)
                break
            case 'hidden_sessions':
                // Sent on every connect: the full set of hidden session ids.
                // Hiding a session emits a single `session_removed` frame and
                // then nothing — no REST listing returns a hidden session, so a
                // client that missed that one frame would keep the row forever,
                // reconciliation included. This is the server stating the fact
                // rather than the client inferring it from an absence.
                for (const id of msg.session_ids || []) {
                    if (store.getSession(id)) store.removeSession(id)
                }
                break
            case 'peers_updated': {
                // Full snapshot pushed on WS connect (share precedent).
                // Lazy import to avoid a useWebSocket ↔ store cycle.
                import('../stores/peers').then(({ usePeersStore }) => {
                    usePeersStore().applyPeers(msg.peers || [])
                })
                break
            }
            case 'peer_messages_updated': {
                import('../stores/peers').then(({ usePeersStore }) => {
                    usePeersStore().applyMessages(msg.messages || [])
                })
                break
            }
            case 'peer_updated': {
                import('../stores/peers').then(({ usePeersStore }) => {
                    const peersStore = usePeersStore()
                    // Held-accept transition (security-relevant — must be visible
                    // outside the manager): the peer accepted our request but our
                    // own code verification hasn't completed yet.
                    const stored = peersStore.getPeerById(msg.peer?.id)
                    const becameHeld = stored
                        && stored.state === 'pending_sent' && !stored.remote_accepted_at
                        && msg.peer?.state === 'pending_sent'
                        && msg.peer?.remote_accepted_at && !msg.peer?.code_confirmed_at
                    peersStore.upsertPeer(msg.peer)
                    if (becameHeld) {
                        const peerName = peersStore.peerLabel(msg.peer.id)
                        toast.custom(PeerToastContent, {
                            type: 'warning',
                            title: `"${peerName}" accepted your request — complete the code verification to activate`,
                            duration: 15000,
                            props: { mode: 'request', peer: msg.peer },
                        })
                    }
                })
                break
            }
            case 'peer_removed': {
                import('../stores/peers').then(({ usePeersStore }) => {
                    usePeersStore().removePeer(msg.peer_id)
                })
                break
            }
            case 'peer_request_received': {
                import('../stores/peers').then(({ usePeersStore }) => {
                    usePeersStore().upsertPeer(msg.peer)
                    toast.custom(PeerToastContent, {
                        type: 'info',
                        title: 'Peer request',
                        duration: 15000,
                        props: { mode: 'request', peer: msg.peer },
                    })
                })
                break
            }
            case 'peer_accepted': {
                import('../stores/peers').then(({ usePeersStore }) => {
                    const peersStore = usePeersStore()
                    peersStore.upsertPeer(msg.peer)
                    // Pairing toasts always carry the address: it is what the
                    // two users verify, and a name alone can name the wrong
                    // instance. The body holds it, the title names the peer.
                    toast.info(msg.peer?.base_url || '', {
                        title: `"${peersStore.peerLabel(msg.peer?.id)}" accepted your peer request`,
                    })
                })
                break
            }
            case 'peer_message_received': {
                import('../stores/peers').then(({ usePeersStore }) => {
                    const peersStore = usePeersStore()
                    peersStore.upsertMessage(msg.message)
                    const peerName = peersStore.peerLabel(msg.message?.peer_id)
                    // Auto-dismissing, like the extra-usage alert above: the
                    // persistent surface is the sidebar inbox badge, which
                    // counts this message for as long as it awaits review.
                    // A toast cannot hold that role — it is per-tab, so an
                    // infinite one survives on every other device long after
                    // the message was answered somewhere else. 30s is short
                    // enough that no cross-tab dismissal is needed: both of
                    // the toast's buttons are local-only anyway (open the
                    // review dialog, or just close), so a stale copy elsewhere
                    // costs nothing.
                    toast.custom(PeerToastContent, {
                        type: 'info',
                        title: `${msg.message?.reply_to_ref ? 'Reply' : 'Message'} from ${peerName}`,
                        duration: 30000,
                        props: { mode: 'message', message: msg.message },
                    })
                })
                break
            }
            case 'peer_message_updated': {
                import('../stores/peers').then(({ usePeersStore }) => {
                    const peersStore = usePeersStore()
                    const stored = peersStore.messages.find(m => m.id === msg.message?.id)
                    const resolvedNow = stored
                        && stored.direction === 'out' && stored.status === 'pending'
                        && ['delivered', 'done', 'refused'].includes(msg.message?.status)
                    peersStore.upsertMessage(msg.message)
                    if (resolvedNow) {
                        const peerName = peersStore.peerLabel(msg.message.peer_id)
                        // The title says WHICH message: several can await the
                        // same peer at once. 40 characters keep the sentence on
                        // one line (a title is capped at 100 server-side).
                        const excerpt = truncateTitle(msg.message.title, 40, '')
                        const quoted = excerpt ? ` "${excerpt}"` : ''
                        // "done": their user dealt with it themselves, no agent
                        // received it — "was done" would not read as a sentence.
                        toast.info(msg.message.status === 'done'
                            ? `"${peerName}" marked your message${quoted} as done`
                            : `Your message${quoted} to "${peerName}" was ${msg.message.status}`)
                    }
                })
                break
            }
            case 'session_removed':
                store.removeSession(msg.session_id)
                break
            case 'sessions_bulk_archived':
                store.applyBulkArchiveFromBroadcast(msg.session_ids)
                break
            case 'session_items_added':
                // Mark items as live (arrived via WebSocket) before adding them,
                // so the live flag is available when components mount during addSessionItems.
                if (msg.items?.length) {
                    store.markItemsLive(msg.session_id, msg.items.map(i => i.line_num))
                }
                // Only add if we've fetched items for this session
                if (store.areSessionItemsFetched(msg.session_id)) {
                    store.addSessionItems(msg.session_id, msg.items, msg.updated_metadata)
                }
                // If user is currently viewing this session, mark as viewed (throttled
                // with trailing edge, so last_viewed_at stays fresh as content arrives).
                // requirePresence: without it, an unattended desktop would mark read via
                // the final streamed item, defeating the unattended-device guard.
                if (route.params.sessionId === msg.session_id) {
                    notifySessionViewed(msg.session_id, 'items-added', { requirePresence: true })
                }
                break
            case 'process_state': {
                // Capture previous state before updating (needed for transition detection)
                const previousProcessState = store.processStates[msg.session_id] || null
                // When leaving assistant_turn, optimistically set last_new_content_at
                // to ensure the session appears unread immediately. The process_state
                // message (from SDK) and session_updated (from file watcher) travel
                // independent async paths — process_state can arrive first, before the
                // store has the updated last_new_content_at. This optimistic value will
                // be overwritten by the watcher's session_updated with the real JSONL
                // timestamp, which is also after last_viewed_at, so the session stays unread.
                if (previousProcessState?.state === 'assistant_turn' && msg.state !== 'assistant_turn') {
                    const session = store.getSession(msg.session_id)
                    if (session) {
                        store.updateSession({ ...session, last_new_content_at: new Date().toISOString() })
                    }
                }
                // Update process state for a session
                store.setProcessState(msg.session_id, msg.project_id, msg.state, {
                    provider: msg.provider,
                    started_at: msg.started_at,
                    state_changed_at: msg.state_changed_at,
                    memory: msg.memory,
                    error: msg.error,
                    pending_requests: msg.pending_requests,
                    active_crons: msg.active_crons,
                    session_title: msg.session_title,
                    project_name: msg.project_name,
                    extra: msg.extra,
                    stopping: msg.stopping,
                    label: msg.label,
                })
                // Ensure the session is present in data.sessions so the cross-filter
                // active block (sessions with a running process) can surface it
                // regardless of the current scope or whether its project has been
                // fetched. loadSessionById is a no-op when the session is already
                // loaded, so this is cheap on repeated process_state messages.
                if (!store.getSession(msg.session_id)) {
                    store.loadSessionById(msg.session_id).catch(err => {
                        console.warn(`Failed to hydrate session ${msg.session_id} from process_state`, err)
                    })
                }
                // Show toast + sound + browser notifications for process state changes
                notifyProcessStateChange(msg, previousProcessState, route)
                break
            }
            case 'process_label': {
                // Transient label override for WorkingAssistantMessage (e.g. "compacting")
                const ps = store.processStates[msg.session_id]
                if (ps) {
                    ps.label = msg.label || null
                    store.recomputeVisualItems(msg.session_id)
                }
                break
            }
            case 'manual_compaction_done': {
                // Codex finished a manually-triggered /compact. No real
                // user_message JSONL line is ever produced for the command, so
                // the optimistic "/compact" bubble would otherwise stay stuck —
                // drop it here. (The "compacting" placeholder itself clears via
                // the accompanying process_state → USER_TURN.) The agent only
                // emits this for a compaction it triggered while gating input,
                // so any pending optimistic message is necessarily the command.
                store.clearOptimisticMessage(msg.session_id)
                break
            }
            case 'goal_command_done': {
                // Codex applied a user /goal command (set or clear) via the
                // app-server goal RPCs. Like /compact, no user_message JSONL
                // line is written for it, so the optimistic "/goal …" bubble
                // would otherwise stay stuck — drop it. (A cold-woken session's
                // STARTING placeholder clears via the paired process_state →
                // USER_TURN.)
                store.clearOptimisticMessage(msg.session_id)
                break
            }
            case 'plan_command_done': {
                // Codex applied a bare /plan (Plan collaboration mode switch)
                // via thread/settings/update. The durable "/plan" transcript
                // marker is only best-effort injected, so like /goal the
                // optimistic bubble is retired explicitly here.
                store.clearOptimisticMessage(msg.session_id)
                break
            }
            case 'process_tools': {
                // Active-tool list for the WorkingAssistantMessage status line.
                const ps = store.processStates[msg.session_id]
                if (!ps) break
                ps.tools = Array.isArray(msg.tools) ? msg.tools : []
                ps.lastStartedToolId = msg.last_started_id || null
                store.recomputeVisualItems(msg.session_id)
                break
            }
            case 'agent_link_created': {
                // New agent link created — populate cache and create synthetic process state
                const agentSessionId = msg.agent_session_id
                if (msg.tool_use_id && msg.parent_session_id) {
                    store.setAgentLink(msg.parent_session_id, msg.tool_use_id, agentSessionId, msg.is_background, msg.tool_use_line_num, msg.agent_slug ?? null)
                }
                // Agent just linked → create synthetic process state
                const startedAtUnix = msg.started_at ? new Date(msg.started_at).getTime() / 1000 : null
                store.setSyntheticProcessState(agentSessionId, msg.parent_session_id, msg.project_id, startedAtUnix)
                break
            }
            case 'workflow_link_created': {
                // A Workflow tool_use paired with its run → show "View Workflow".
                if (msg.session_id && msg.tool_use_id && msg.run_id) {
                    store.setWorkflowLink(msg.session_id, msg.tool_use_id, msg.run_id)
                }
                break
            }
            case 'workflow_changed': {
                // A wf_*.json was created/updated → let an open Workflows tab refetch.
                window.dispatchEvent(new CustomEvent('twicc:workflow-changed', { detail: { sessionId: msg.session_id } }))
                break
            }
            case 'tool_state': {
                // Update tool state for spinner/running display
                store.setToolState(msg.session_id, msg.tool_use_id, msg.result_count, msg.completed_at, msg.error || null, msg.extra || null, Array.isArray(msg.tool_result_line_nums) ? msg.tool_result_line_nums : [])

                // For agent tools: remove synthetic process state when done
                const agentLink = store.getAgentLink(msg.session_id, msg.tool_use_id)
                if (agentLink) {
                    const requiredCount = agentLink.isBackground ? 2 : 1
                    if (msg.result_count >= requiredCount) {
                        store.removeSyntheticProcessState(agentLink.agentId)
                    }
                }
                break
            }
            case 'active_processes':
                // Initialize process states from server on connection
                store.setActiveProcesses(msg.processes)
                break
            case 'invalid_title':
                // Show error toast for invalid session title
                toast.session(msg.session_id, {
                    type: 'error',
                    title: 'Invalid title',
                    errorMessage: msg.error || 'Unknown error',
                })
                // The whole send was aborted with the title — restore the
                // message into the composer too.
                if (msg.request_id) {
                    store.failInflightSend(msg.request_id, {
                        code: 'invalid_title',
                        message: `Invalid session title: ${msg.error || 'unknown error'}`,
                    })
                }
                break
            case 'title_suggested':
                // Handle title suggestion response
                store.handleTitleSuggested(msg)
                break
            case 'usage_updated': {
                // Handle usage quota update — dispatched to the matching provider's
                // store so the data lives next to the rest of that provider's state.
                const provider = msg.provider ?? msg.usage?.provider
                if (!provider) break
                const computed = msg.usage ? computeUsageData(msg.usage) : null
                getProviderStore(provider)?.setUsage(msg.success, msg.reason, msg.usage, computed)
                break
            }
            case 'extra_usage_started': {
                // Backend-detected rising edge of extra-usage consumption. The
                // backend already gated on the master ``notifyOnExtraUsageStart``
                // switch, so the event's mere arrival means "notify". Show an
                // auto-dismissing toast (the persistent surface is the sidebar
                // ring) plus the per-device sound / browser notification. Built
                // via toast.custom html so the title can carry the provider's
                // brand icon (the plain title field is rendered as text).
                const provider = msg.provider
                if (!provider) break
                const settings = useSettingsStore()
                const label = getProviderLabel(provider)
                const iconName = getProviderIcon(provider)
                const iconColor = getProviderIconColor(provider)
                const iconHtml = iconName
                    ? `<wa-icon family="brands" name="${iconName}" style="margin-inline-end: 0.4em;${iconColor ? ` color: ${iconColor};` : ''}"></wa-icon>`
                    : ''
                const detail = formatExtraUsageStartedDetail(msg.extra_usage)
                const sentence = `${label} is currently drawing from your extra usage credit, billed on top of your plan.`
                toast.custom({
                    type: 'warning',
                    duration: 60000,
                    html: `
                        <div style="display: flex; flex-direction: column; gap: 0.2rem;">
                            <span style="font-weight: 600; display: inline-flex; align-items: center;">${iconHtml}${label} — Extra usage started</span>
                            <span>${sentence}</span>
                            ${detail ? `<span>${detail}</span>` : ''}
                        </div>
                    `,
                })
                playNotificationSound(settings.notifExtraUsageStartSound)
                if (settings.notifExtraUsageStartBrowser) {
                    sendBrowserNotification(
                        `${label} — Extra usage started`,
                        detail ? `${sentence}\n${detail}` : sentence,
                    )
                }
                break
            }
            case 'usage_dump_path_validated':
                // Intentionally provider-agnostic: this is a write-target validation
                // (directory exists/writable). Unlike a read file (which has a
                // provider-specific format and goes through ``usage_file_validated``),
                // a dump just writes the raw payload back, so the check has no
                // provider-specific content to verify. Do not move under a provider
                // handler.
                if (__hmrState.usageDumpPathValidateResolve) {
                    __hmrState.usageDumpPathValidateResolve({ valid: msg.valid, message: msg.message })
                    __hmrState.usageDumpPathValidateResolve = null
                }
                break
            case 'usage_file_validated': {
                // Cross-provider read-mode validation reply. The backend
                // delegates the format check to the provider's helper but
                // the wire envelope is generic — keyed resolvers let
                // simultaneous validations for different providers coexist.
                const provider = msg.provider
                if (!provider) break
                const resolver = __hmrState.usageFileValidateResolvers.get(provider)
                if (resolver) {
                    __hmrState.usageFileValidateResolvers.delete(provider)
                    resolver({ valid: msg.valid, message: msg.message })
                }
                break
            }
            case 'tmux_config_path_validated':
                if (__hmrState.tmuxConfigPathValidateResolve) {
                    __hmrState.tmuxConfigPathValidateResolve({ valid: msg.valid, message: msg.message })
                    __hmrState.tmuxConfigPathValidateResolve = null
                }
                break
            case 'telemetry_payload':
                if (__hmrState.telemetryPayloadResolve) {
                    __hmrState.telemetryPayloadResolve({ payload: msg.payload, sent_at: msg.sent_at, preview: msg.preview })
                    __hmrState.telemetryPayloadResolve = null
                }
                break
            case 'telemetry_instance_id_reset':
                if (__hmrState.telemetryInstanceIdResetResolve) {
                    __hmrState.telemetryInstanceIdResetResolve({ instance_id: msg.instance_id })
                    __hmrState.telemetryInstanceIdResetResolve = null
                }
                break
            case 'synced_settings_updated':
                // Apply synced settings from backend (on connect or when another client updates)
                // Lazy import to avoid circular dependency (useWebSocket.js → settings.js)
                import('../stores/settings').then(({ useSettingsStore }) => {
                    useSettingsStore().applySyncedSettings(msg.settings, msg.version)
                })
                break
            case 'synced_settings_result':
                window.dispatchEvent(new CustomEvent('twicc:synced-settings-result', {
                    detail: msg,
                }))
                break
            case 'provider_state_changed':
                // Live transition of a provider's lifecycle state (stopped /
                // starting / running / stopping). Used by isProviderAvailable
                // to gate runtime UI and by the Settings switch UX.
                if (msg.provider && msg.state) {
                    useDataStore().setProviderState(msg.provider, msg.state)
                }
                break
            case 'workspaces_updated':
                // Apply workspaces from backend (on connect or when another client updates)
                // Lazy import to avoid circular dependency (useWebSocket.js → workspaces.js)
                import('../stores/workspaces').then(({ useWorkspacesStore }) => {
                    useWorkspacesStore().applyWorkspaces(msg.workspaces)
                })
                break
            case 'layouts_updated':
                // Apply the named-layouts catalog from backend (connect push / another client's update)
                import('../stores/layouts').then(({ useLayoutsStore }) => {
                    useLayoutsStore().applyLayouts(msg.layouts)
                })
                break
            case 'terminal_config_updated':
                // Apply terminal config from backend (on connect or when another client updates)
                // Lazy import to avoid circular dependency (useWebSocket.js → terminalConfig.js)
                import('../stores/terminalConfig').then(({ useTerminalConfigStore }) => {
                    useTerminalConfigStore().applyConfig(msg.config)
                })
                break
            case 'message_snippets_updated':
                // Apply message snippets config from backend (on connect or when another client updates)
                // Lazy import to avoid circular dependency (useWebSocket.js → messageSnippets.js)
                import('../stores/messageSnippets').then(({ useMessageSnippetsStore }) => {
                    useMessageSnippetsStore().applyConfig(msg.config)
                })
                break
            case 'benchmarks_updated':
                // Full benchmark dataset pushed after the daily sync wrote at
                // least one row. Replace the store's set wholesale — scores
                // recompute over the whole dataset. Lazy import mirrors siblings.
                import('../stores/benchmarks').then(({ useBenchmarksStore }) => {
                    useBenchmarksStore().applyBenchmarks(msg.benchmarks)
                })
                break
            case 'agent_settings_presets_updated':
                // Cross-provider preset update — the on-disk format is identical
                // for every provider, the ``provider`` field selects the bucket
                // in the per-provider keyed store. Mirrors ``usage_updated``.
                if (msg.provider) {
                    import('../stores/agentSettingsPresets').then(({ useAgentSettingsPresetsStore }) => {
                        useAgentSettingsPresetsStore().applyConfig(msg.provider, msg.config)
                    })
                }
                break
            case 'tips_manifest_pushed':
                // Read-only manifest pushed by the backend on connect.
                import('../stores/tips').then(({ useTipsStore }) => {
                    useTipsStore().applyManifest(msg.manifest)
                })
                break
            case 'seen_tips_updated':
                // Multi-device sync : another client (or this one's last write)
                // updated the seen-state.
                import('../stores/tips').then(({ useTipsStore }) => {
                    useTipsStore().applySeenTips(msg.seen_tips)
                })
                break
            case 'providers_status_updated': {
                // The whole providers-status file (current status, incident and
                // acknowledgment per provider), on connect and after every write.
                // The store keeps it — App.vue derives the status toasts from it —
                // and each provider's own store gets the bare status value for the
                // Settings footer.
                const providersStatus = msg.providers_status ?? {}
                import('../stores/providersStatus').then(({ useProvidersStatusStore }) => {
                    useProvidersStatusStore().applyProvidersStatus(providersStatus)
                })
                for (const [provider, record] of Object.entries(providersStatus)) {
                    getProviderHelpers(provider)?.applyServiceStatus(record?.status)
                }
                break
            }
            case 'help_manifest_pushed':
                // Read-only help manifest pushed by the backend on connect.
                import('../stores/help').then(({ useHelpStore }) => {
                    useHelpStore().applyManifest(msg.manifest)
                })
                break
            case 'seen_help_updated':
                // Multi-device sync of the help seen-state.
                import('../stores/help').then(({ useHelpStore }) => {
                    useHelpStore().applySeenHelp(msg.seen_help)
                })
                break
            case 'terminal_list':
                import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
                    const store = useTerminalTabsStore()
                    store.setIndices(msg.terminal_context, msg.terminals)
                    if (msg.labels) {
                        store.setLabels(msg.terminal_context, msg.labels)
                    }
                    store.setAutoAttachMap(msg.terminal_context, msg.autoAttach || {})
                })
                break
            case 'terminal_created':
                import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
                    useTerminalTabsStore().addIndex(msg.terminal_context, msg.terminal_index)
                })
                break
            case 'terminal_killed':
                import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
                    useTerminalTabsStore().removeIndex(msg.terminal_context, msg.terminal_index)
                })
                break
            case 'terminal_renamed':
                import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
                    useTerminalTabsStore().setLabel(msg.terminal_context, msg.terminal_index, msg.label)
                })
                break
            case 'terminal_autoattach_changed':
                import('../stores/terminalTabs').then(({ useTerminalTabsStore }) => {
                    useTerminalTabsStore().setAutoAttach(msg.terminal_context, msg.terminal_index, !!msg.enabled)
                })
                break
            case 'stream_block_start':
                store.streamBlockStart(msg.session_id, msg.message_id, msg.block_index, msg.block_type)
                break
            case 'stream_block_delta':
                store.streamBlockDelta(msg.session_id, msg.message_id, msg.block_index, msg.text)
                break
            case 'stream_block_stop':
                store.streamBlockStop(msg.session_id, msg.message_id, msg.block_index)
                break
            case 'stream_block_end':
                store.streamBlockEnd(msg.session_id, msg.message_id, msg.block_index, msg.uuid)
                break
            case 'startup_progress':
                store.setStartupProgress(msg.provider, msg.phase, msg.current, msg.total, msg.completed)
                break
            case 'update_available':
                store.setLatestVersion(msg.latest_version, msg.release_url)
                handleUpdateAvailable(msg)
                break
            case 'send_ack': {
                // Positive delivery acknowledgement for a send: the message
                // reached the agent. Drop its in-flight snapshot (and heal any
                // failed bubble produced by a lost/premature failure signal).
                // This is the only delivery confirmation for messages Claude
                // Code accepts mid-turn — they never get a user_message line.
                if (msg.request_id) {
                    store.confirmInflightSend(msg.session_id, msg.request_id)
                }
                break
            }
            case 'error': {
                // A send_message failure matching an in-flight send: the store
                // drops the optimistic ghosts and surfaces the composer callout
                // (with draft restoration) — no toast needed on top.
                if (msg.request_id && store.failInflightSend(msg.request_id, msg)) {
                    break
                }
                if (msg.code === 'provider_disabled') {
                    const providerLabel = getProviderLabel(msg.provider) || msg.provider
                    toast.error(`Cannot send to ${providerLabel}: it is disabled. Enable it from Settings → Providers.`)
                } else if (msg.code === 'invalid_synced_settings') {
                    toast.error(msg.message || 'A settings value is invalid.')
                } else {
                    console.warn('[ws] Server error message:', msg)
                }
                break
            }
            default: {
                // Provider-prefixed message? ``<provider>:<action>`` is delegated
                // to the provider's WS handler. Generic dispatcher is unaware of
                // any specific provider key.
                const colonIdx = msg.type.indexOf(':')
                if (colonIdx > 0) {
                    const provider = msg.type.slice(0, colonIdx)
                    const action = msg.type.slice(colonIdx + 1)
                    const handler = getProviderWsHandler(provider)
                    if (handler) {
                        handler.handle(action, msg)
                        break
                    }
                }
                if (msg.type !== "pong") {
                    console.warn('[ws] Unknown message type:', msg.type)
                }
                break
            }
        }
    }

    // Detect WebSocket connection/reconnection and trigger reconciliation
    // We reconcile on EVERY connection (not just reconnections) because:
    // - Initial data is loaded via API before WebSocket connects
    // - If WebSocket connects late, we may have missed updates
    watch(status, (newStatus, oldStatus) => {
        if (newStatus === 'OPEN') {
            // Store send function at module level for global access
            __hmrState.wsSendFn = send
            store.wsConnected = true

            const currentProjectId = route.params.projectId || null
            const currentSessionId = route.params.sessionId || null
            // A reconnection is any OPEN once we'd already connected before. NOT
            // `oldStatus === 'CLOSED'`: VueUse's socket goes CLOSED → CONNECTING →
            // OPEN, and `onopen` (which sets OPEN) fires a tick after CONNECTING,
            // so at this OPEN transition oldStatus is always 'CONNECTING', never
            // 'CLOSED'. The old check left isReconnection permanently false,
            // silently disabling every reconnect-only path (pane refresh, and the
            // outage-edit auto-open). `wasConnected` (false only on the very first
            // connect) is the correct, robust signal.
            const isReconnection = wasConnected
            console.log(`WebSocket ${isReconnection ? 'reconnected' : 'connected'}, starting reconciliation...`)
            onReconnected(currentProjectId, currentSessionId, isReconnection)
            // After a real reconnection, the reconciliation re-syncs session
            // payloads (so presence flags like has_artifacts / has_plan are
            // fresh), but the transient tool-pane content events
            // (artifact_files_changed / plan_changed) emitted during the outage
            // are gone. Signal open panes to refresh their content. Not on the
            // first connect — panes load on mount, and there was no outage.
            if (isReconnection) {
                window.dispatchEvent(new CustomEvent('twicc:ws-reconnected'))
            }
            wasConnected = true
        } else if (newStatus === 'CLOSED') {
            // Clear send function when disconnected
            __hmrState.wsSendFn = null
            store.wsConnected = false
        }
    })

    /**
     * Open WebSocket, resetting the auth failure flag.
     * Called by App.vue when authentication is confirmed.
     */
    function openWs() {
        __hmrState.lastCloseWasAuthFailure = false
        open()
    }

    return { wsStatus: status, send, openWs, closeWs: close }
}
