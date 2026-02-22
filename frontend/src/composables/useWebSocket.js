// frontend/src/composables/useWebSocket.js

import { watch } from 'vue'
import { useWebSocket as useVueWebSocket, useDebounceFn } from '@vueuse/core'
import { useRoute } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useAuthStore } from '../stores/auth'
import { useReconciliation } from './useReconciliation'
import { toast } from './useToast'
import { computeUsageData } from '../utils/usage'
import { useSettingsStore } from '../stores/settings'
import { playNotificationSound, sendBrowserNotification } from '../utils/notificationSounds'
import { truncateTitle } from '../utils/truncate'

// WebSocket close code sent by backend when authentication fails
const WS_CLOSE_AUTH_FAILURE = 4001

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

// Debounced function for user draft notifications (10 seconds)
// This prevents spamming the server while still keeping the process alive
if (!('debouncedDraftNotifications' in __hmrState)) __hmrState.debouncedDraftNotifications = new Map() // sessionId -> debouncedFn

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
export function killProcess(sessionId) {
    return sendWsMessage({
        type: 'kill_process',
        session_id: sessionId
    })
}

/**
 * Request a title suggestion for a session.
 * The result will arrive via WebSocket as title_suggested message.
 * @param {string} sessionId - The session ID
 * @param {string|null} prompt - Optional prompt text (for draft/new sessions)
 * @param {string} systemPrompt - System prompt with {text} placeholder
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function requestTitleSuggestion(sessionId, prompt = null, systemPrompt) {
    const message = { type: 'suggest_title', sessionId, systemPrompt }
    if (prompt) {
        message.prompt = prompt
    }
    return sendWsMessage(message)
}

/**
 * Respond to a pending request on a session's process.
 * Sends a pending_request_response message via WebSocket.
 * @param {string} sessionId - The session ID
 * @param {string} requestId - The pending request ID
 * @param {object} responseData - The response payload (request_type, decision/answers, etc.)
 * @returns {boolean} True if the message was sent
 */
export function respondToPendingRequest(sessionId, requestId, responseData) {
    return sendWsMessage({
        type: 'pending_request_response',
        session_id: sessionId,
        request_id: requestId,
        ...responseData,
    })
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
 * Build a notification body string from the enriched WebSocket message.
 * Format: "Project: <name>\nSession: <title>" (both truncated).
 *
 * Uses session_title / project_name injected by the backend so that
 * notifications display correctly even when session data isn't loaded
 * in the frontend store (e.g. on the projects list page).
 *
 * @param {Object} msg - The WebSocket process_state message (with session_title / project_name)
 * @returns {string}
 */
function buildNotificationBody(msg) {
    const projectName = truncateTitle(msg.project_name, 50)
    const sessionTitle = truncateTitle(msg.session_title, 50)
    return `Project: ${projectName}\nSession: ${sessionTitle}`
}

/**
 * Show toast notification and trigger external notifications (sound + browser)
 * for process state changes.
 * @param {Object} msg - The WebSocket process_state message (enriched with session_title / project_name)
 * @param {Object|null} previousState - The previous process state (before update), null if new process
 */
function notifyProcessStateChange(msg, previousState) {
    const sessionId = msg.session_id
    const settings = useSettingsStore()

    // --- Transition to user_turn: "Claude finished working" ---
    if (msg.state === 'user_turn' && previousState?.state !== 'user_turn') {
        // Sound notification
        playNotificationSound(settings.notifUserTurnSound)
        // Browser notification
        if (settings.notifUserTurnBrowser) {
            sendBrowserNotification(
                'Claude finished working',
                buildNotificationBody(msg),
            )
        }
    }

    // --- Pending request: "Claude needs your attention" ---
    if (msg.pending_request && !previousState?.pending_request) {
        const pendingTitle = msg.pending_request.request_type === 'ask_user_question'
            ? '🖐️ Claude has a question for you'
            : '🖐️ Claude needs your approval'
        toast.session(sessionId, { type: 'warning', title: pendingTitle })

        // Sound notification
        playNotificationSound(settings.notifPendingRequestSound)
        // Browser notification
        if (settings.notifPendingRequestBrowser) {
            sendBrowserNotification(
                'Claude needs your attention',
                buildNotificationBody(msg),
            )
        }
    }

    // --- Process death notifications (toast only, unchanged) ---
    if (msg.state === 'dead') {
        // Only notify for errors and timeouts, not for normal lifecycle
        if (msg.kill_reason === 'error') {
            toast.session(sessionId, {
                type: 'error',
                title: 'Claude Code terminated due to error',
                errorMessage: msg.error || 'Unknown error',
            })
        } else if (msg.kill_reason === 'timeout_starting') {
            toast.session(sessionId, {
                type: 'error',
                title: 'Claude Code stopped: failed to start within 1 minute',
            })
        } else if (msg.kill_reason === 'timeout_user_turn') {
            toast.session(sessionId, {
                type: 'info',
                title: 'Claude Code stopped: inactive for 15 minutes',
            })
        } else if (msg.kill_reason === 'timeout_assistant_turn') {
            toast.session(sessionId, {
                type: 'warning',
                title: 'Claude Code stopped: no activity for 2 hours',
            })
        } else if (msg.kill_reason === 'timeout_assistant_turn_absolute') {
            toast.session(sessionId, {
                type: 'warning',
                title: 'Claude Code stopped: running for over 6 hours',
            })
        }
    }
}

export function useWebSocket() {
    const store = useDataStore()
    const route = useRoute()
    const { onReconnected } = useReconciliation()

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
            try {
                const msg = JSON.parse(event.data)
                handleMessage(msg)
            } catch (e) {
                console.warn('WebSocket received non-JSON message:', event.data)
            }
        }
    })

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
            case 'session_added':
                // Check if this is a draft session being confirmed by the backend
                const existingSession = store.getSession(msg.session.id)
                if (existingSession?.draft) {
                    // Draft session confirmed - update with real data and remove draft flag
                    store.updateSession({ ...msg.session, draft: false })
                } else if (store.areProjectSessionsFetched(msg.session.project_id) ||
                    store.areAllProjectsSessionsFetched) {
                    // Only add if we've fetched sessions for this project or all projects
                    // (subagents are filtered out in getProjectSessions getter)
                    store.addSession(msg.session)
                }
                break
            case 'session_updated':
                // Only update if session exists in store (was previously fetched)
                if (store.getSession(msg.session.id)) {
                    store.updateSession(msg.session)
                }
                break
            case 'session_items_added':
                // Only add if we've fetched items for this session
                if (store.areSessionItemsFetched(msg.session_id)) {
                    store.addSessionItems(msg.session_id, msg.items, msg.updated_metadata)
                }
                break
            case 'process_state': {
                // Capture previous state before updating (needed for transition detection)
                const previousProcessState = store.processStates[msg.session_id] || null
                // Update process state for a session
                store.setProcessState(msg.session_id, msg.project_id, msg.state, {
                    started_at: msg.started_at,
                    state_changed_at: msg.state_changed_at,
                    memory: msg.memory,
                    error: msg.error,
                    pending_request: msg.pending_request,
                    session_title: msg.session_title,
                    project_name: msg.project_name,
                })
                // Show toast + sound + browser notifications for process state changes
                notifyProcessStateChange(msg, previousProcessState)
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
                break
            case 'title_suggested':
                // Handle title suggestion response
                store.handleTitleSuggested(msg)
                break
            case 'usage_updated': {
                // Handle usage quota update
                const computed = msg.has_oauth ? computeUsageData(msg.usage) : null
                store.setUsage(msg.has_oauth, msg.success, msg.reason, msg.usage, computed)
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

            const currentProjectId = route.params.projectId || null
            const currentSessionId = route.params.sessionId || null
            const isReconnection = wasConnected && oldStatus === 'CLOSED'
            console.log(`WebSocket ${isReconnection ? 'reconnected' : 'connected'}, starting reconciliation...`)
            onReconnected(currentProjectId, currentSessionId)
            wasConnected = true
        } else if (newStatus === 'CLOSED') {
            // Clear send function when disconnected
            __hmrState.wsSendFn = null
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
