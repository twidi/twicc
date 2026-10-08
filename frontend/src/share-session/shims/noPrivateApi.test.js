import test from 'node:test'
import assert from 'node:assert/strict'
import { apiFetch } from './noPrivateApi.js'

test('public private-api cut rejects without contacting a private endpoint', async () => {
    const original = globalThis.fetch
    let requests = 0
    globalThis.fetch = async () => { requests++; return new Response() }
    try {
        await assert.rejects(apiFetch('/api/sessions/root/'), /Private API unavailable/)
        assert.equal(requests, 0)
    } finally { globalThis.fetch = original }
})
