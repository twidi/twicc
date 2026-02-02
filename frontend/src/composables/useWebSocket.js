// frontend/src/composables/useWebSocket.js

import { watch } from 'vue'
import { useWebSocket as useVueWebSocket } from '@vueuse/core'
import { useRoute } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useReconciliation } from './useReconciliation'
import { toast } from './useToast'

// Module-level reference to the WebSocket send function
// Allows components to access it without going through the composable
let wsSendFn = null

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
        // Process stopped - check if voluntary or error
        if (msg.kill_reason === 'error') {
            toast.error(sessionLabel, {
                title: 'Claude Code terminated due to error',
                details: msg.error || undefined
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
                // Only add if we've fetched sessions for this project or all projects
                // (subagents are filtered out in getProjectSessions getter)
                if (store.areProjectSessionsFetched(msg.session.project_id) ||
                    store.areAllProjectsSessionsFetched) {
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
