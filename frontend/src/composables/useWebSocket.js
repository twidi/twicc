// frontend/src/composables/useWebSocket.js

import { useWebSocket as useVueWebSocket } from '@vueuse/core'
import { useDataStore } from '../stores/data'

export function useWebSocket() {
    const store = useDataStore()

    const { status, send } = useVueWebSocket(`ws://${location.host}/ws/`, {
        autoReconnect: {
            retries: 5,
            delay: 1000
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

    return { wsStatus: status, send }
}
