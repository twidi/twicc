import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
    SLOT_LIMIT, STALLED_AFTER_MS, computeStatusByOrigin, findEntryForRecord, isStalled, rule7Action,
    selectNextToPromote,
} from './rules.js'

const TAB = 'tab-1'
const NOW = 10_000_000
const origin = { panel: 'files', key: 'session:s' }

function entry(fields = {}) {
    return {
        key: 'k', clientId: null, local: false, localState: null, pauseReason: null, size: 100,
        sentBytes: 0, lastSeenAt: NOW, origin, server: null, ...fields,
    }
}
function record(fields = {}) {
    return { id: 'id1', client_id: 'other:1', state: 'active', version: 1, size: 100, offset: 0, error: null, ...fields }
}

test('stalled: non-local active entry of this tab id is stalled at once', () => {
    const e = entry({ server: record({ client_id: `${TAB}:abc` }), lastSeenAt: NOW })
    assert.equal(isStalled(e, { tabId: TAB, now: NOW }), true)
})

test('stalled: after 180 s without a record; never when local or finalizing', () => {
    const e = entry({ server: record(), lastSeenAt: NOW - STALLED_AFTER_MS })
    assert.equal(isStalled(e, { tabId: TAB, now: NOW }), false)
    assert.equal(isStalled(e, { tabId: TAB, now: NOW + 1 }), true)
    assert.equal(isStalled({ ...e, local: true }, { tabId: TAB, now: NOW + 1 }), false)
    assert.equal(isStalled({ ...e, server: record({ state: 'finalizing' }) }, { tabId: TAB, now: NOW + 1 }), false)
})

test('statusByOrigin: count, percent, sentBytes while sending, queued with and without offset', () => {
    const entries = [
        entry({ key: 'a', local: true, localState: 'sending', sentBytes: 50, server: record({ offset: 10 }) }),
        entry({ key: 'b', local: true, localState: 'queued', server: null }),
        entry({ key: 'c', local: true, localState: 'queued', server: record({ offset: 30 }) }),
        entry({ key: 'd', origin: { panel: 'artifacts', key: 'session:s' }, server: record({ offset: 25 }) }),
    ]
    const status = computeStatusByOrigin(entries, { tabId: TAB, now: NOW })
    assert.deepEqual(status['files|session:s'], { count: 3, percent: 26, allStalled: false })
    assert.deepEqual(status['artifacts|session:s'], { count: 1, percent: 25, allStalled: false })
})

test('statusByOrigin: zero sizes give 100; all stalled or paused', () => {
    const entries = [
        entry({ key: 'a', size: 0, local: true, localState: 'paused', pauseReason: 'network' }),
        entry({ key: 'b', size: 0, server: record({ size: 0, client_id: `${TAB}:x` }) }),
    ]
    assert.deepEqual(computeStatusByOrigin(entries, { tabId: TAB, now: NOW })['files|session:s'],
        { count: 2, percent: 100, allStalled: true })
})

test('statusByOrigin: an entry that becomes local is no longer stalled', () => {
    const e = entry({ server: record({ client_id: `${TAB}:x` }) })
    assert.equal(computeStatusByOrigin([e], { tabId: TAB, now: NOW })['files|session:s'].allStalled, true)
    e.local = true
    e.localState = 'queued'
    assert.equal(computeStatusByOrigin([e], { tabId: TAB, now: NOW })['files|session:s'].allStalled, false)
})

test('pump selection: oldest queued first, slot count derived from the entries', () => {
    const entries = [
        entry({ key: 'a', localState: 'creating' }),
        entry({ key: 'b', localState: 'sending' }),
        entry({ key: 'c', localState: 'queued' }),
        entry({ key: 'd', localState: 'queued' }),
    ]
    assert.equal(selectNextToPromote(entries).key, 'c')
    entries[2].localState = 'sending'
    assert.equal(SLOT_LIMIT, 3)
    assert.equal(selectNextToPromote(entries), null)
})

test('pump selection: nothing while a network pause exists; an error pause does not gate', () => {
    const queued = entry({ key: 'q', localState: 'queued' })
    assert.equal(selectNextToPromote([entry({ localState: 'paused', pauseReason: 'network' }), queued]), null)
    assert.equal(selectNextToPromote([entry({ localState: 'paused', pauseReason: 'error' }), queued]), queued)
})

test('findEntryForRecord: by clientId only when empty or same upload, else by id', () => {
    const empty = entry({ key: 'c1', clientId: 'c1' })
    assert.equal(findEntryForRecord([empty], record({ client_id: 'c1' })), empty)
    const holding = entry({ key: 'c1', clientId: 'c1', server: record({ id: 'other' }) })
    assert.equal(findEntryForRecord([holding], record({ client_id: 'c1' })), null)
    const byId = entry({ key: 'id1', server: record() })
    assert.equal(findEntryForRecord([holding, byId], record({ client_id: 'c1' })), byId)
})

test('rule 7: error → pause; short offset after finalizing → requeue; else nothing', () => {
    const e = entry({ local: true, localState: null })
    assert.equal(rule7Action(e, record(), record({ error: 'disk full', offset: 100 })), 'pause-error')
    assert.equal(rule7Action(e, record({ state: 'finalizing', offset: 100 }), record({ offset: 40 })), 'requeue')
    assert.equal(rule7Action(e, record({ offset: 40 }), record({ offset: 50 })), null)
    assert.equal(rule7Action({ ...e, local: false }, record({ state: 'finalizing' }), record({ offset: 40 })), null)
    assert.equal(rule7Action({ ...e, localState: 'sending' }, record({ state: 'finalizing' }), record({ offset: 40 })), null)
})
