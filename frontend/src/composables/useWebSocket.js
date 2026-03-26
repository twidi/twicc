// frontend/src/composables/useWebSocket.js

import { ref, watch } from 'vue'
import { useWebSocket as useVueWebSocket, useDebounceFn, useThrottleFn } from '@vueuse/core'
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
 * Send a session_viewed message to the backend and update the store optimistically.
 * @param {string} sessionId - The session ID
 */
function _sendSessionViewed(sessionId) {
    sendWsMessage({
        type: 'session_viewed',
        session_id: sessionId,
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
 * Throttled to 30 seconds per session: first call fires immediately,
 * subsequent calls within the window are dropped.
 * @param {string} sessionId - The session ID
 */
export function notifySessionViewed(sessionId) {
    if (!sessionId) return

    // Get or create throttled function for this session
    if (!__hmrState.throttledViewedNotifications.has(sessionId)) {
        const throttledFn = useThrottleFn(() => {
            _sendSessionViewed(sessionId)
        }, 30000) // 30s throttle (leading=true, trailing=false by default)
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
 */
export function forceNotifySessionViewed(sessionId) {
    if (!sessionId) return
    _sendSessionViewed(sessionId)
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
 * Send synced settings to the backend for persistence in settings.json.
 * The backend will broadcast the updated settings to all connected clients.
 * @param {Object} settings - The synced settings key-value pairs
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendSyncedSettings(settings) {
    return sendWsMessage({ type: 'update_synced_settings', settings })
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
 * @param {Object} route - The current Vue route object (used to suppress toasts when viewing the session)
 */
function notifyProcessStateChange(msg, previousState, route) {
    const sessionId = msg.session_id
    const settings = useSettingsStore()

    // --- Transition to user_turn: "Claude finished working" ---
    if (msg.state === 'user_turn' && previousState?.state !== 'user_turn') {
        // In-app toast when the user is on TwiCC but not viewing this session
        const isViewingSession = route?.params?.sessionId === sessionId
        if (!isViewingSession) {
            toast.session(sessionId, { type: 'info', title: 'Claude finished working' })
        }
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
        // Skip toast if the user is already viewing this session (they see the
        // pending-request indicator directly in the Chat tab)
        const isViewingSession = route?.params?.sessionId === sessionId
        if (!isViewingSession) {
            const pendingTitle = msg.pending_request.request_type === 'ask_user_question'
                ? '🖐️ Claude has a question for you'
                : '🖐️ Claude needs your approval'
            toast.session(sessionId, { type: 'warning', title: pendingTitle })
        }

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

/**
 * Status-specific messages for Claude Code outage notifications.
 * Keys are the component status values from the Atlassian Statuspage API.
 */
const CLAUDE_STATUS_MESSAGES = {
    degraded_performance: {
        type: 'warning',
        message: 'Claude Code is currently experiencing degraded performance on Anthropic\'s side',
    },
    partial_outage: {
        type: 'warning',
        message: 'Claude Code is currently experiencing a partial outage on Anthropic\'s side',
    },
    major_outage: {
        type: 'error',
        message: 'Claude Code is currently experiencing a major outage on Anthropic\'s side',
    },
    under_maintenance: {
        type: 'info',
        message: 'Claude Code is currently under maintenance on Anthropic\'s side',
    },
}

/**
 * Handle claude_status message from the backend.
 * Shows a persistent toast when Claude Code is not operational,
 * or a resolution toast when it returns to operational.
 */
function handleClaudeStatus(msg) {
    const { status } = msg
    if (!status) return

    const statusLink = '<a href="https://status.claude.com/" target="_blank" rel="noopener" style="color: inherit; text-decoration: underline;">status.claude.com</a>'

    if (status === 'operational') {
        toast.custom({
            type: 'success',
            title: 'Anthropic status update',
            html: `Claude Code issues on Anthropic's side are now resolved — ${statusLink}`,
            duration: Infinity,
        })
    } else {
        const config = CLAUDE_STATUS_MESSAGES[status]
        if (config) {
            toast.custom({
                type: config.type,
                title: 'Anthropic status update',
                html: `${config.message} — ${statusLink}`,
                duration: Infinity,
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

    // Check localStorage: skip if already notified for this version (or newer)
    const lastNotified = localStorage.getItem(UPDATE_NOTIFIED_VERSION_KEY)
    if (lastNotified && lastNotified >= latest_version) return

    // Store the version so we don't notify again
    localStorage.setItem(UPDATE_NOTIFIED_VERSION_KEY, latest_version)

    // Show persistent toast with upgrade instructions
    toast.custom({
        type: 'info',
        title: `TwiCC v${latest_version} is available`,
        duration: Infinity,
        html: `
            <div style="display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.25rem;">
                <span>Stop and re-run: <code style="background: var(--wa-color-neutral-100); padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.9em;">uvx twicc@latest</code></span>
                <a href="${release_url}" target="_blank" rel="noopener" style="color: var(--wa-color-primary-600); text-decoration: underline;">View release notes</a>
            </div>
        `,
    })
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
                } else if (store.areProjectSessionsFetched(msg.session.project_id) ||
                    store.areAllProjectsSessionsFetched) {
                    // Session not yet in store but sessions for this project have been
                    // fetched — add it (new session or missed earlier update).
                    store.addSession(msg.session)
                }
                break
            }
            case 'session_items_added':
                // Only add if we've fetched items for this session
                if (store.areSessionItemsFetched(msg.session_id)) {
                    store.addSessionItems(msg.session_id, msg.items, msg.updated_metadata)
                }
                // If user is currently viewing this session, mark as viewed (throttled)
                if (route.params.sessionId === msg.session_id) {
                    notifySessionViewed(msg.session_id)
                }
                break
            case 'process_state': {
                // If the backend confirms a process is running for a draft session,
                // drop the draft flag immediately — this is the earliest signal that
                // the session is real, before the watcher even sees the JSONL file.
                if (msg.state !== 'dead') {
                    const session = store.getSession(msg.session_id)
                    if (session?.draft) {
                        store.updateSession({ ...session, draft: false })
                    }
                }
                // Capture previous state before updating (needed for transition detection)
                const previousProcessState = store.processStates[msg.session_id] || null
                // Update process state for a session
                store.setProcessState(msg.session_id, msg.project_id, msg.state, {
                    started_at: msg.started_at,
                    state_changed_at: msg.state_changed_at,
                    memory: msg.memory,
                    error: msg.error,
                    pending_request: msg.pending_request,
                    active_crons: msg.active_crons,
                    session_title: msg.session_title,
                    project_name: msg.project_name,
                })
                // Show toast + sound + browser notifications for process state changes
                notifyProcessStateChange(msg, previousProcessState, route)
                break
            }
            case 'agent_link_created': {
                // New agent link created — populate cache and create synthetic process state
                const agentSessionId = msg.agent_session_id
                if (msg.tool_use_id && msg.parent_session_id) {
                    store.setAgentLink(msg.parent_session_id, msg.tool_use_id, agentSessionId, msg.is_background)
                }
                // Agent just linked → create synthetic process state
                const startedAtUnix = msg.started_at ? new Date(msg.started_at).getTime() / 1000 : null
                store.setSyntheticProcessState(agentSessionId, msg.project_id, startedAtUnix)
                break
            }
            case 'tool_state': {
                // Update tool state for spinner/running display
                store.setToolState(msg.session_id, msg.tool_use_id, msg.result_count, msg.completed_at, msg.error || null, msg.extra || null, true)

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
            case 'synced_settings_updated':
                // Apply synced settings from backend (on connect or when another client updates)
                // Lazy import to avoid circular dependency (useWebSocket.js → settings.js)
                import('../stores/settings').then(({ useSettingsStore }) => {
                    useSettingsStore().applySyncedSettings(msg.settings)
                })
                break
            case 'startup_progress':
                store.setStartupProgress(msg.phase, msg.current, msg.total, msg.completed)
                break
            case 'update_available':
                store.setLatestVersion(msg.latest_version, msg.release_url)
                handleUpdateAvailable(msg)
                break
            case 'claude_status':
                store.setClaudeStatus(msg.status)
                handleClaudeStatus(msg)
                break
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
            const isReconnection = wasConnected && oldStatus === 'CLOSED'
            console.log(`WebSocket ${isReconnection ? 'reconnected' : 'connected'}, starting reconciliation...`)
            onReconnected(currentProjectId, currentSessionId)
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
