import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { useMcpStore } from './mcp.js'

function setup() { setActivePinia(createPinia()); return useMcpStore() }
function response(data, ok = true) { return { ok, json: async () => data } }

test('a fresh owner snapshot removes completed requests', async () => {
    const store = setup()
    store.requests = [{ id: 'pending' }]
    store.request = async () => response({ connections: [{ id: 'grant' }], requests: [], config: {} })
    await store.refresh()
    assert.deepEqual(store.requests, [])
    assert.equal(store.connections[0].id, 'grant')
})

test('background refresh preserves a consent error', async () => {
    const store = setup()
    store.request = async () => response({ error: 'Verification code does not match.' }, false)
    assert.equal(await store.act('approve', { id: 'pending', code: 'bad' }), false)
    store.request = async () => response({ connections: [], requests: [{ id: 'pending' }], config: {} })
    await store.refresh()
    assert.equal(store.error, 'Verification code does not match.')
})

test('owner actions send the required CSRF header and refresh after success', async () => {
    const store = setup()
    const calls = []
    store.request = async options => {
        calls.push(options)
        return response(options ? { ok: true } : { connections: [], requests: [], config: {} })
    }
    assert.equal(await store.act('revoke', { id: 'grant' }), true)
    assert.equal(calls[0].headers['X-TwiCC-MCP-Owner'], '1')
    assert.deepEqual(JSON.parse(calls[0].body), { action: 'revoke', id: 'grant' })
    assert.equal(calls.length, 2)
})

test('concurrent callers wait for the same initial configuration', async () => {
    const store = setup()
    let resolve
    let calls = 0
    store.request = () => { calls++; return new Promise(done => { resolve = done }) }
    const first = store.refresh()
    const second = store.refresh()
    resolve(response({ connections: [], requests: [], config: { externalMcpEnabled: true } }))
    await Promise.all([first, second])
    assert.equal(calls, 1)
    assert.equal(store.config.externalMcpEnabled, true)
})

test('a lapsed request leaves the pending list without a round trip', () => {
    const store = setup()
    const now = Date.now()
    store.requests = [
        { id: 'fresh', expires_at: new Date(now + 60_000).toISOString() },
        { id: 'lapsed', expires_at: new Date(now - 1_000).toISOString() },
    ]
    store.clock = now
    assert.deepEqual(store.pendingRequests.map(row => row.id), ['fresh'])
})

test('an unreadable deadline keeps the request visible', () => {
    const store = setup()
    store.requests = [{ id: 'undated' }, { id: 'garbage', expires_at: 'not a date' }]
    store.clock = Date.now()
    assert.deepEqual(store.pendingRequests.map(row => row.id), ['undated', 'garbage'])
})
