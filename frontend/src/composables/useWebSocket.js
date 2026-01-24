// frontend/src/composables/useWebSocket.js

import { watch } from 'vue'
import { useWebSocket as useVueWebSocket } from '@vueuse/core'
import { useRoute } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useReconciliation } from './useReconciliation'

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
                // Only add if we've fetched sessions for this project
                if (store.areProjectSessionsFetched(msg.session.project_id)) {
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
                    store.addSessionItems(msg.session_id, msg.items)
                }
                break
        }
    }

    // Detect WebSocket connection/reconnection and trigger reconciliation
    // We reconcile on EVERY connection (not just reconnections) because:
    // - Initial data is loaded via API before WebSocket connects
    // - If WebSocket connects late, we may have missed updates
    watch(status, (newStatus, oldStatus) => {
        if (newStatus === 'OPEN') {
            const currentProjectId = route.params.projectId || null
            const currentSessionId = route.params.sessionId || null
            const isReconnection = wasConnected && oldStatus === 'CLOSED'
            console.log(`WebSocket ${isReconnection ? 'reconnected' : 'connected'}, starting reconciliation...`)
            onReconnected(currentProjectId, currentSessionId)
            wasConnected = true
        }
    })

    return { wsStatus: status, send }
}
