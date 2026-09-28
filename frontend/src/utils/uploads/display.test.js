import { test } from 'node:test'
import assert from 'node:assert/strict'
import { entryActions, takePickedFiles } from './display.js'

const TAB = 'tab-1'
const NOW = 10_000_000
const ctx = { tabId: TAB, now: NOW }

function entry(fields = {}) {
    return { local: true, localState: null, server: null, lastSeenAt: NOW, ...fields }
}
function record(fields = {}) {
    return { id: 'id', client_id: 'other:1', state: 'active', size: 100, offset: 10, error: null, ...fields }
}

test('entryActions: every state text and button', () => {
    assert.deepEqual(entryActions(entry({ localState: 'queued' }), ctx), { text: 'Queued', error: null, buttons: ['cancel'] })
    assert.deepEqual(entryActions(entry({ localState: 'creating' }), ctx), { text: null, error: null, buttons: ['cancel'] })
    assert.deepEqual(entryActions(entry({ localState: 'sending', server: record() }), ctx), { text: null, error: null, buttons: ['cancel'] })
    assert.deepEqual(entryActions(entry({ localState: 'paused', pauseReason: 'network' }), ctx),
        { text: 'Paused', error: null, buttons: ['retry', 'cancel'] })
    assert.deepEqual(entryActions(entry({ localState: 'cancelling', server: record() }), ctx),
        { text: 'Cancelling', error: null, buttons: [] })
    assert.deepEqual(entryActions(entry({ server: record({ state: 'finalizing' }) }), ctx),
        { text: 'Finalizing', error: null, buttons: [] })
    assert.deepEqual(entryActions(entry({ local: false, server: record({ state: 'finalizing', client_id: `${TAB}:x` }) }), ctx),
        { text: 'Finalizing', error: null, buttons: [] })
    // Stalled (this tab lost the File): Interrupted + Resume.
    assert.deepEqual(entryActions(entry({ local: false, server: record({ client_id: `${TAB}:x` }) }), ctx),
        { text: 'Interrupted', error: null, buttons: ['resume', 'cancel'] })
    // Live upload of another tab: progress only.
    assert.deepEqual(entryActions(entry({ local: false, server: record() }), ctx), { text: null, error: null, buttons: ['cancel'] })
})

test('entryActions: a non-local complete entry offers Retry, with or without error', () => {
    const complete = { client_id: `${TAB}:x`, offset: 100, size: 100 }
    assert.deepEqual(entryActions(entry({ local: false, server: record(complete) }), ctx),
        { text: null, error: null, buttons: ['retryFinalization', 'cancel'] })
    assert.deepEqual(entryActions(entry({ local: false, server: record({ ...complete, error: 'disk full' }) }), ctx),
        { text: null, error: 'disk full', buttons: ['retryFinalization', 'cancel'] })
})

test('takePickedFiles copies the files before the input reset', () => {
    const picked = ['a', 'b']
    const input = {
        _files: picked.slice(),
        get files() { return this._files },
        set value(v) { if (v === '') this._files.length = 0 },
        get value() { return '' },
    }
    const files = takePickedFiles(input)
    assert.deepEqual(files, ['a', 'b'])
    assert.equal(input.files.length, 0)
})
