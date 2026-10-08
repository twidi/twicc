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

test('inline API uses token routes, encoded identities, and abortable retry', async () => {
    const originalFetch = globalThis.fetch
    const calls = []
    globalThis.fetch = async (url, options) => { calls.push([url, options]); return Response.json({ enabled: true, revision: 1, artifacts: [] }) }
    try {
        const api = shareApiModule.makeShareApi('/share/token/')
        const signal = new AbortController().signal
        await api.fetchInlineManifest({ signal })
        await api.retryInlineArtifact('root/a', 'widget', { signal })
        assert.equal(calls[0][0], '/share/token/api/inline-artifacts/')
        assert.equal(calls[0][1].signal, signal)
        assert.equal(calls[1][0], '/share/token/api/inline-artifacts/root%2Fa/widget/retry/')
        assert.equal(calls[1][1].method, 'POST')
        assert.equal(calls[1][1].signal, signal)
    } finally { globalThis.fetch = originalFetch }
})
