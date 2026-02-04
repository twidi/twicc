// frontend/src/composables/useWebSocket.js

import { watch } from 'vue'
import { useWebSocket as useVueWebSocket, useDebounceFn } from '@vueuse/core'
import { useRoute } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useReconciliation } from './useReconciliation'
import { toast } from './useToast'

// Module-level reference to the WebSocket send function
// Allows components to access it without going through the composable
let wsSendFn = null

// Debounced function for user draft notifications (10 seconds)
// This prevents spamming the server while still keeping the process alive
const debouncedDraftNotifications = new Map() // sessionId -> debouncedFn

/**
 * Send a JSON message through the WebSocket connection.
 * Returns false if not connected.
 * @param {object} data - The data to send (will be JSON-stringified)
 * @returns {boolean} - True if message was sent, false if not connected
 */
export function sendWsMessage(data) {
    if (!wsSendFn) {
        console.warn('WebSocket not initialized, cannot send message')
        return false
    }
    wsSendFn(JSON.stringify(data))
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
 * Notify the server that the user is actively preparing a message.
 * This resets the inactivity timeout for the process.
 * Debounced to 10 seconds per session to avoid spamming.
 * @param {string} sessionId - The session ID
 */
export function notifyUserDraftUpdated(sessionId) {
    if (!sessionId) return

    // Get or create debounced function for this session
    if (!debouncedDraftNotifications.has(sessionId)) {
        const debouncedFn = useDebounceFn(() => {
            sendWsMessage({
                type: 'user_draft_updated',
                session_id: sessionId
            })
        }, 10000) // 10 seconds debounce
        debouncedDraftNotifications.set(sessionId, debouncedFn)
    }

    // Call the debounced function
    debouncedDraftNotifications.get(sessionId)()
}

/**
 * Show toast notification for process state changes.
 * Only notifies for specific state transitions (started, stopped).
 */
function notifyProcessStateChange(store, msg) {
    const session = store.getSession(msg.session_id)
    const title = session?.title || 'Unknown'
    const truncatedTitle = title.length > 50 ? title.slice(0, 50) + '…' : title
    const sessionLabel = `Session: "${truncatedTitle}"`

    if (msg.state === 'starting') {
        // Process started
        toast.success(sessionLabel, { title: 'Claude Code started' })
    } else if (msg.state === 'dead') {
        // Process stopped - check the reason
        if (msg.kill_reason === 'error') {
            toast.error(`Error: "${msg.error || 'Unknown'}"\n${sessionLabel}`, {
                title: 'Claude Code terminated due to error',
            })
        } else if (msg.kill_reason === 'timeout_starting') {
            // Starting timeout - something went wrong
            toast.error(sessionLabel, {
                title: 'Claude Code stopped: failed to start within 1 minute',
            })
        } else if (msg.kill_reason === 'timeout_user_turn') {
            // User turn timeout - normal cleanup, just info
            toast.info(sessionLabel, {
                title: 'Claude Code stopped: inactive for 15 minutes',
            })
        } else if (msg.kill_reason === 'timeout_assistant_turn') {
            // Assistant turn inactivity timeout - might be stuck
            toast.warning(sessionLabel, {
                title: 'Claude Code stopped: no activity for 2 hours',
            })
        } else if (msg.kill_reason === 'timeout_assistant_turn_absolute') {
            // Assistant turn absolute timeout - ran too long
            toast.warning(sessionLabel, {
                title: 'Claude Code stopped: running for over 6 hours',
            })
        } else {
            // Manual kill or shutdown
            toast.info(sessionLabel, { title: 'Claude Code terminated' })
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
    const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const { status, send } = useVueWebSocket(`${wsProtocol}//${location.host}/ws/`, {
        autoReconnect: {
            retries: Infinity,
            delay: 1000,
            maxDelay: 30000
        },
        heartbeat: {
            message: JSON.stringify({ type: 'ping' }),
            responseMessage: JSON.stringify({ type: 'pong' }),
            interval: 30000,
            pongTimeout: 5000
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

    function handleMessage(msg) {
        switch (msg.type) {
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
            case 'process_state':
                // Update process state for a session
                store.setProcessState(msg.session_id, msg.project_id, msg.state, {
                    started_at: msg.started_at,
                    state_changed_at: msg.state_changed_at,
                    memory: msg.memory,
                    error: msg.error,
                })
                // Show toast notifications for process state changes
                notifyProcessStateChange(store, msg)
                break
            case 'active_processes':
                // Initialize process states from server on connection
                store.setActiveProcesses(msg.processes)
                break
            case 'invalid_title':
                // Show error toast for invalid session title
                toast.error(`Error: "${msg.error || 'Unknown'}"\nSession: "${msg.title}"`, {
                    title: 'Invalid title',
                })
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
            wsSendFn = send

            const currentProjectId = route.params.projectId || null
            const currentSessionId = route.params.sessionId || null
            const isReconnection = wasConnected && oldStatus === 'CLOSED'
            console.log(`WebSocket ${isReconnection ? 'reconnected' : 'connected'}, starting reconciliation...`)
            onReconnected(currentProjectId, currentSessionId)
            wasConnected = true
        } else if (newStatus === 'CLOSED') {
            // Clear send function when disconnected
            wsSendFn = null
        }
    })

    return { wsStatus: status, send }
}
