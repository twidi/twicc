import test from 'node:test'
import assert from 'node:assert/strict'
import { connectShareLive } from './shareLive.js'

test('live inline manifests and reconnects use the token connection and dispose it', () => {
    const oldSocket = globalThis.WebSocket, oldLocation = globalThis.location
    const sockets = [], manifests = []
    let reconnects = 0
    globalThis.location = { origin: 'https://share.example.com' }
    globalThis.WebSocket = class {
        constructor(url) { this.url = url; sockets.push(this) }
        close() { this.closed = true; this.onclose() }
    }
    try {
        const dispose = connectShareLive({ tokenPath: '/share/token/', sessionId: 'root',
            onInlineArtifacts: manifest => manifests.push(manifest), onReconnect: () => reconnects++,
        })
        assert.equal(sockets[0].url, 'wss://share.example.com/ws/share/token/')
        sockets[0].onopen()
        sockets[0].onmessage({ data: JSON.stringify({ type: 'share_inline_artifacts', manifest: { revision: 11 } }) })
        assert.equal(reconnects, 1)
        assert.deepEqual(manifests, [{ revision: 11 }])
        dispose()
        assert.equal(sockets[0].closed, true)
        sockets[0].onopen()
        sockets[0].onmessage({ data: JSON.stringify({ type: 'share_inline_artifacts', manifest: { revision: 12 } }) })
        assert.equal(reconnects, 1)
        assert.deepEqual(manifests, [{ revision: 11 }])
    } finally { globalThis.WebSocket = oldSocket; globalThis.location = oldLocation }
})
