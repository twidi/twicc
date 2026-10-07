import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readFileSync } from 'node:fs'
import { effectScope } from 'vue'

// Real VueUse with a browser surface (same harness as websocketHeartbeat.test.js).
globalThis.window = new EventTarget()
globalThis.document = {}

class FakeWebSocket {
    constructor(url) {
        this.url = url
        this.closed = false
    }
    send() {}
    close(code) {
        this.closed = true
        this.onclose?.({ code })
    }
}
globalThis.WebSocket = FakeWebSocket

const { useWebSocket } = await import('@vueuse/core')

function openSocket(options) {
    const scope = effectScope()
    let connection
    scope.run(() => {
        connection = useWebSocket('ws://localhost/ws/', { autoReconnect: { delay: 1000 }, ...options })
    })
    connection.ws.value.onopen()
    return { scope, socket: connection.ws.value }
}

// "Leave site?" cancelled during an upload: `beforeunload` fires, the page stays.
// VueUse's default `autoClose` closes the socket on `beforeunload` for good
// (explicit close, no reconnect), leaving a live page without updates: an
// upload's chip never learns its end. The browser closes the socket itself on a real unload.
test('VueUse closes the socket on beforeunload by default (the mechanism)', () => {
    const { scope, socket } = openSocket({})
    try {
        globalThis.window.dispatchEvent(new Event('beforeunload'))
        assert.equal(socket.closed, true)
    } finally {
        scope.stop()
    }
})

test('autoClose false keeps the socket open when the unload is cancelled', () => {
    const { scope, socket } = openSocket({ autoClose: false })
    try {
        globalThis.window.dispatchEvent(new Event('beforeunload'))
        assert.equal(socket.closed, false)
    } finally {
        scope.stop()
    }
})

test('the app socket is opened with autoClose false', () => {
    const source = readFileSync(new URL('./useWebSocket.js', import.meta.url), 'utf8')
    assert.match(source, /useVueWebSocket\([^)]*\{[\s\S]*?\bautoClose:\s*false/)
})
