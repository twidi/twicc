import test from 'node:test'
import assert from 'node:assert/strict'

import {
    applyUnavailableRefusal, captureRefusalTarget, getSessionUnavailableReason, noteUnavailableReasonUpdate,
} from './sessionAvailability.js'

function getterOf(sessions) {
    return id => sessions[id]
}

test('a session without a reason is available', () => {
    const get = getterOf({ s: { id: 's', unavailable_reason: null } })
    assert.equal(getSessionUnavailableReason(get, 's'), null)
})

test('any reason blocks the session, whatever its value', () => {
    for (const reason of ['rollout_missing', 'codex_migration_failed:x', 'a_future_reason']) {
        const get = getterOf({ s: { id: 's', unavailable_reason: reason } })
        assert.equal(getSessionUnavailableReason(get, 's'), reason)
    }
})

test('a subagent follows its parent', () => {
    const get = getterOf({
        p: { id: 'p', unavailable_reason: 'rollout_missing' },
        a: { id: 'a', unavailable_reason: null },
    })
    assert.equal(getSessionUnavailableReason(get, 'a', 'p'), 'rollout_missing')
    assert.equal(getSessionUnavailableReason(get, 'a'), null)
})

test('an unknown session is not blocked', () => {
    assert.equal(getSessionUnavailableReason(getterOf({}), 'missing', 'also-missing'), null)
})

const jsonResponse = (body, status) => new Response(JSON.stringify(body), { status })
const refused = (reason = 'rollout_missing', carrier) => jsonResponse({ error: 'x', code: 'session_unavailable',
    unavailable_reason: reason, ...(carrier ? { unavailable_session_id: carrier } : {}) }, 409)
const targetOf = (sessions, id) => captureRefusalTarget(getterOf(sessions), id)

test('a session_unavailable refusal stores its reason on the session and returns it', async () => {
    const sessions = { s: { id: 's', unavailable_reason: null } }
    const target = targetOf(sessions, 's')
    const response = refused()
    assert.equal(await applyUnavailableRefusal(response, target), 'rollout_missing')
    assert.equal(sessions.s.unavailable_reason, 'rollout_missing')
    assert.equal(response.bodyUsed, false, 'the body stays readable for the caller')
})

test('a refusal keeps a reason the session already has', async () => {
    const sessions = { s: { id: 's', unavailable_reason: 'history:claude-rewind' } }
    assert.equal(await applyUnavailableRefusal(refused(), targetOf(sessions, 's')), 'rollout_missing')
    assert.equal(sessions.s.unavailable_reason, 'history:claude-rewind')
})

test('a refusal for a session the store does not hold still returns its reason', async () => {
    assert.equal(await applyUnavailableRefusal(refused(), targetOf({}, 'missing')), 'rollout_missing')
})

test('a refused subagent gets the reason on the row the backend names as its carrier', async () => {
    const parentCarries = { p: { id: 'p', unavailable_reason: null }, a: { id: 'a', unavailable_reason: null } }
    assert.equal(await applyUnavailableRefusal(refused('rollout_missing', 'p'), targetOf(parentCarries, 'a')), 'rollout_missing')
    assert.equal(parentCarries.p.unavailable_reason, 'rollout_missing')
    assert.equal(parentCarries.a.unavailable_reason, null)
    assert.equal(getSessionUnavailableReason(getterOf(parentCarries), 'a', 'p'), 'rollout_missing')

    // A Codex subagent can carry its own reason: its parent stays available.
    const ownReason = { p: { id: 'p', unavailable_reason: null }, a: { id: 'a', unavailable_reason: null } }
    await applyUnavailableRefusal(refused('rollout_missing', 'a'), targetOf(ownReason, 'a'))
    assert.equal(ownReason.a.unavailable_reason, 'rollout_missing')
    assert.equal(ownReason.p.unavailable_reason, null)
})

test('without a carrier the store holds, the reason goes on the targeted row', async () => {
    for (const carrier of [undefined, 'not-in-store']) {
        const sessions = { p: { id: 'p', unavailable_reason: null }, a: { id: 'a', unavailable_reason: null } }
        await applyUnavailableRefusal(refused('rollout_missing', carrier), targetOf(sessions, 'a'))
        assert.equal(sessions.a.unavailable_reason, 'rollout_missing')
        assert.equal(sessions.p.unavailable_reason, null)
    }
})

test('an update that sets a reason during the read wins: nothing is written, the refusal still counts', async () => {
    const sessions = { s: { id: 's', unavailable_reason: null } }
    const target = targetOf(sessions, 's')
    // The session_updated carrying the reason arrives while the request is in flight.
    sessions.s.unavailable_reason = 'history:claude-rewind'
    noteUnavailableReasonUpdate(sessions.s)
    assert.equal(await applyUnavailableRefusal(refused('rollout_missing'), target), 'history:claude-rewind')
    assert.equal(sessions.s.unavailable_reason, 'history:claude-rewind')
})

test('an update that clears the reason during the read wins: the refusal is a plain failure', async () => {
    const sessions = { s: { id: 's', unavailable_reason: null } }
    const target = targetOf(sessions, 's')
    noteUnavailableReasonUpdate(sessions.s)
    assert.equal(await applyUnavailableRefusal(refused(), target), null)
    assert.equal(sessions.s.unavailable_reason, null)
    // A read started after that update applies again.
    assert.equal(await applyUnavailableRefusal(refused(), targetOf(sessions, 's')), 'rollout_missing')
    assert.equal(sessions.s.unavailable_reason, 'rollout_missing')
})

test('an update of another row during the read does not block the refusal', async () => {
    const sessions = { s: { id: 's', unavailable_reason: null }, o: { id: 'o', unavailable_reason: null } }
    const target = targetOf(sessions, 's')
    noteUnavailableReasonUpdate(sessions.o)
    assert.equal(await applyUnavailableRefusal(refused(), target), 'rollout_missing')
    assert.equal(sessions.s.unavailable_reason, 'rollout_missing')
})

test('any other failed response is not a refusal and changes nothing', async () => {
    const cases = [
        jsonResponse({ code: 'other', unavailable_reason: 'rollout_missing' }, 409),
        jsonResponse({ code: 'session_unavailable' }, 409),
        new Response('not json', { status: 409 }),
        jsonResponse({ code: 'session_unavailable', unavailable_reason: 'rollout_missing' }, 500),
        { ok: false, status: 409 },
    ]
    for (const response of cases) {
        const sessions = { s: { id: 's', unavailable_reason: null } }
        assert.equal(await applyUnavailableRefusal(response, targetOf(sessions, 's')), null)
        assert.equal(sessions.s.unavailable_reason, null)
    }
})
