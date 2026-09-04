import test from 'node:test'
import assert from 'node:assert/strict'

import * as shareApiModule from './shareApi.js'

test('share API preserves the session-not-ready response', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = async () => new Response(
        JSON.stringify({ error: 'session_not_ready' }),
        { status: 409, headers: { 'content-type': 'application/json' } },
    )

    try {
        let error = null
        try {
            await shareApiModule.makeShareApi('/share/token').fetchItemsMetadata('subagent')
        } catch (caught) {
            error = caught
        }

        assert.equal(error?.status, 409)
        assert.equal(error?.code, 'session_not_ready')
        assert.equal(typeof shareApiModule.isSessionNotReadyError, 'function')
        assert.equal(shareApiModule.isSessionNotReadyError(error), true)
    } finally {
        globalThis.fetch = originalFetch
    }
})
