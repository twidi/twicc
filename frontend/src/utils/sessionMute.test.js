// frontend/src/utils/sessionMute.test.js
//
// Covers session notification mute persistence. A missing optimistic update,
// wrong PATCH request, response merge, or rollback must fail these tests.

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { applySessionMuteOnUserTurn } from './sessionMute.js'
import { i18n, loadLocaleMessages } from '../i18n/index.js'
import { resolveUiMessage } from '../i18n/uiMessages.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

function deferred() {
    let resolve
    const promise = new Promise(done => { resolve = done })
    return { promise, resolve }
}

test('applies the mute state immediately and merges the PATCH response', async () => {
    const sessions = {
        session_1: {
            id: 'session_1',
            project_id: 'project_1',
            mute_on_user_turn: false,
            title: 'Before response',
        },
    }
    let resolveRequest
    const request = new Promise(resolve => { resolveRequest = resolve })
    const apiFetch = async (url, options) => {
        assert.equal(url, '/api/projects/project_1/sessions/session_1/')
        assert.equal(options.method, 'PATCH')
        assert.deepEqual(options.headers, { 'Content-Type': 'application/json' })
        assert.deepEqual(JSON.parse(options.body), { mute_on_user_turn: true })
        await request
        return {
            ok: true,
            json: async () => ({ mute_on_user_turn: true, title: 'From server' }),
        }
    }

    const pending = applySessionMuteOnUserTurn(sessions, apiFetch, 'project_1', 'session_1', true)
    assert.equal(sessions.session_1.mute_on_user_turn, true)
    resolveRequest()
    await pending

    assert.deepEqual(sessions.session_1, {
        id: 'session_1',
        project_id: 'project_1',
        mute_on_user_turn: true,
        title: 'From server',
    })
})

test('restores the prior mute state when PATCH fails', async () => {
    const sessions = {
        session_1: {
            id: 'session_1',
            project_id: 'project_1',
            mute_on_user_turn: false,
        },
    }
    const apiFetch = async () => ({
        ok: false,
        json: async () => ({ error: 'Request failed' }),
    })

    await assert.rejects(
        applySessionMuteOnUserTurn(sessions, apiFetch, 'project_1', 'session_1', true),
        { message: 'Request failed' },
    )

    assert.equal(sessions.session_1.mute_on_user_turn, false)
})

test('serializes PATCHes while preserving the latest optimistic state', async () => {
    const sessions = {
        session_1: {
            id: 'session_1',
            mute_on_user_turn: false,
            title: 'Before responses',
        },
    }
    const gates = [deferred(), deferred()]
    const requestedValues = []
    const apiFetch = async (_url, options) => {
        const requestIndex = requestedValues.length
        const requestedValue = JSON.parse(options.body).mute_on_user_turn
        requestedValues.push(requestedValue)
        await gates[requestIndex].promise
        return {
            ok: true,
            json: async () => ({
                mute_on_user_turn: requestedValue,
                title: requestIndex === 0 ? 'First response' : 'Second response',
            }),
        }
    }

    const firstPending = applySessionMuteOnUserTurn(
        sessions, apiFetch, 'project_1', 'session_1', true,
    )
    const secondPending = applySessionMuteOnUserTurn(
        sessions, apiFetch, 'project_1', 'session_1', false,
    )

    assert.equal(sessions.session_1.mute_on_user_turn, false)
    assert.deepEqual(requestedValues, [true])

    gates[0].resolve()
    await firstPending
    await Promise.resolve()

    assert.deepEqual(requestedValues, [true, false])
    assert.equal(sessions.session_1.mute_on_user_turn, false)
    assert.equal(sessions.session_1.title, 'Before responses')

    gates[1].resolve()
    await secondPending

    assert.deepEqual(sessions.session_1, {
        id: 'session_1',
        mute_on_user_turn: false,
        title: 'Second response',
    })
})

test('does not roll back a newer optimistic state when an older PATCH fails', async () => {
    const sessions = {
        session_1: {
            id: 'session_1',
            mute_on_user_turn: false,
        },
    }
    const gates = [deferred(), deferred()]
    let requestCount = 0
    const apiFetch = async () => {
        const requestIndex = requestCount
        requestCount += 1
        await gates[requestIndex].promise
        if (requestIndex === 0) {
            return {
                ok: false,
                json: async () => ({ error: 'First request failed' }),
            }
        }
        return {
            ok: true,
            json: async () => ({ mute_on_user_turn: true }),
        }
    }

    const firstPending = applySessionMuteOnUserTurn(
        sessions, apiFetch, 'project_1', 'session_1', true,
    )
    const secondPending = applySessionMuteOnUserTurn(
        sessions, apiFetch, 'project_1', 'session_1', true,
    )

    assert.equal(requestCount, 1)
    gates[0].resolve()
    await assert.rejects(firstPending, { message: 'First request failed' })
    await Promise.resolve()

    assert.equal(requestCount, 2)
    assert.equal(sessions.session_1.mute_on_user_turn, true)

    gates[1].resolve()
    await secondPending
    assert.equal(sessions.session_1.mute_on_user_turn, true)
})

test('removes an optimistic mute key when the first PATCH fails', async () => {
    const sessions = {
        session_1: {
            id: 'session_1',
        },
    }
    const apiFetch = async () => ({
        ok: false,
        json: async () => ({ error: 'Request failed' }),
    })

    await assert.rejects(
        applySessionMuteOnUserTurn(sessions, apiFetch, 'project_1', 'session_1', true),
        { message: 'Request failed' },
    )

    assert.equal(Object.hasOwn(sessions.session_1, 'mute_on_user_turn'), false)
})

test('failed mute updates retain backend message identity through rollback and locale changes', async context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale })
    i18n.global.locale.value = 'en'
    const sessions = { session_1: { id: 'session_1', mute_on_user_turn: false } }
    const apiFetch = async () => ({ ok: false, json: async () => ({
        error: 'Session session_1 not found', code: 'session_not_found',
        ui_message: { key: 'backend.websocket.session_missing', params: { session_id: 'session_1' } },
    }) })
    let failure
    await assert.rejects(applySessionMuteOnUserTurn(sessions, apiFetch, 'project_1', 'session_1', true), error => {
        failure = error
        return error instanceof Error && error.code === 'session_not_found'
    })
    assert.equal(sessions.session_1.mute_on_user_turn, false)
    assert.equal(resolveUiMessage(failure.uiMessage), i18n.global.t('backend.websocket.session_missing', { session_id: 'session_1' }))
    i18n.global.locale.value = 'pt-BR'
    assert.equal(failure.message, i18n.global.t('backend.websocket.session_missing', { session_id: 'session_1' }))
    assert.equal(resolveUiMessage(failure.uiMessage), failure.message)
})

test('empty mute-error payload retains the local fallback across locale changes', async context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale })
    i18n.global.locale.value = 'en'
    let failure
    await assert.rejects(applySessionMuteOnUserTurn({}, async () => ({ ok: false, json: async () => ({}) }), 'p', 's', true), error => {
        failure = error
        return error.message === i18n.global.t('session.notifications.errors.update')
    })
    i18n.global.locale.value = 'pt-BR'
    assert.equal(failure.message, i18n.global.t('session.notifications.errors.update'))
})
