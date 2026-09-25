import test from 'node:test'
import assert from 'node:assert/strict'

import { getProcessStateNotificationEffects, getUserTurnNotificationText } from './processStateNotifications.js'


const options = {
    isViewingSession: false,
    userTurnToastEnabled: true,
    userTurnBrowserEnabled: true,
    pendingRequestBrowserEnabled: true,
}


test('a missing mute key keeps every user-turn notification effect enabled', () => {
    const effects = getProcessStateNotificationEffects(
        { state: 'user_turn', pending_requests: [] },
        { state: 'assistant_turn', pending_requests: [] },
        options,
    )

    assert.equal(effects.showUserTurnToast, true)
    assert.equal(effects.playUserTurnSound, true)
    assert.equal(effects.sendUserTurnBrowser, true)
})


test('mute suppresses user-turn effects but preserves pending-request effects', () => {
    const effects = getProcessStateNotificationEffects(
        {
            state: 'user_turn',
            mute_on_user_turn: true,
            pending_requests: [{ request_id: 'request-1' }],
        },
        { state: 'assistant_turn', pending_requests: [] },
        options,
    )

    assert.equal(effects.showUserTurnToast, false)
    assert.equal(effects.playUserTurnSound, false)
    assert.equal(effects.sendUserTurnBrowser, false)
    assert.equal(effects.showPendingRequestToast, true)
    assert.equal(effects.playPendingRequestSound, true)
    assert.equal(effects.sendPendingRequestBrowser, true)
})


test('mute does not suppress read tracking for a viewed session', () => {
    const effects = getProcessStateNotificationEffects(
        { state: 'user_turn', mute_on_user_turn: true },
        { state: 'assistant_turn' },
        { ...options, isViewingSession: true },
    )

    assert.equal(effects.markViewed, true)
})


test('the toast switch gates only the user-turn toast', () => {
    const effects = getProcessStateNotificationEffects(
        {
            state: 'user_turn',
            pending_requests: [{ request_id: 'request-1' }],
        },
        { state: 'assistant_turn', pending_requests: [] },
        { ...options, userTurnToastEnabled: false },
    )

    assert.equal(effects.showUserTurnToast, false)
    assert.equal(effects.playUserTurnSound, true)
    assert.equal(effects.sendUserTurnBrowser, true)
    assert.equal(effects.showPendingRequestToast, true)
})


test('user-turn notification text: unchanged without background shells', () => {
    assert.deepEqual(
        getUserTurnNotificationText({ providerLabel: 'Claude' }),
        { title: 'Claude finished working', detail: null },
    )
    assert.deepEqual(
        getUserTurnNotificationText({ providerLabel: 'Codex', backgroundShells: 0 }),
        { title: 'Codex finished working', detail: null },
    )
})


test('user-turn notification text: a running shell means the turn, not the work, finished', () => {
    assert.deepEqual(
        getUserTurnNotificationText({ providerLabel: 'Claude', backgroundShells: 1 }),
        { title: 'Claude finished its turn', detail: '1 background shell still running' },
    )
    assert.deepEqual(
        getUserTurnNotificationText({ providerLabel: 'Codex', backgroundShells: 3 }),
        { title: 'Codex finished its turn', detail: '3 background shells still running' },
    )
})


test('user-turn notification text: ephemeral sessions keep their title', () => {
    assert.deepEqual(
        getUserTurnNotificationText({ providerLabel: 'Claude', backgroundShells: 1, ephemeral: true }),
        { title: 'Ephemeral session finished', detail: '1 background shell still running' },
    )
})
