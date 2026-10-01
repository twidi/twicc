import assert from 'node:assert/strict'
import { test } from 'node:test'
import { effectScope, nextTick } from 'vue'

// VueUse detects the environment when imported. Supply the browser surface
// before loading it, and use its real heartbeat and scope disposal code.
globalThis.window = new EventTarget()
globalThis.document = {}

class FakeWebSocket {
    constructor(url) {
        this.url = url
        this.sent = []
        this.closed = false
    }
    send(message) { this.sent.push(message) }
    close(code) {
        this.closed = true
        this.onclose?.({ code })
    }
}
globalThis.WebSocket = FakeWebSocket

const { useWebSocket } = await import('@vueuse/core')

test('server pong reaches onMessage for the visibility liveness probe', async () => {
    const scope = effectScope()
    const messages = []
    let connection
    try {
        scope.run(() => {
            connection = useWebSocket('ws://localhost/ws/', {
                autoReconnect: { delay: 1000 },
                heartbeat: {
                    message: JSON.stringify({ type: 'ping' }),
                    responseMessage: JSON.stringify({ type: 'pong' }),
                    interval: 30000,
                    pongTimeout: 5000,
                },
                onMessage: (_socket, event) => messages.push(event.data),
            })
        })
        const socket = connection.ws.value
        socket.onopen()
        await nextTick()
        socket.onmessage({ data: '{"type": "pong"}' })
        assert.deepEqual(messages, ['{"type": "pong"}'])
        assert.equal(connection.data.value, '{"type": "pong"}')
        // The compact response is filtered by this installed VueUse version.
        socket.onmessage({ data: '{"type":"pong"}' })
        assert.equal(messages.length, 1)
        scope.stop()
        assert.equal(socket.closed, true)
        assert.equal(connection.status.value, 'CLOSED')
    } finally {
        connection?.close()
        scope.stop()
    }
})
