import assert from 'node:assert/strict'
import test from 'node:test'

import {
    processActivityDisplayMode,
    processActivityTooltip,
    summarizeProcessActivity,
} from './processActivity.js'

function ps(state, { pending = 0, crons = 0, shells = 0 } = {}) {
    return {
        state,
        pending_requests: Array.from({ length: pending }, (_, i) => ({ request_id: `r${i}` })),
        active_crons: crons ? Array.from({ length: crons }, (_, i) => ({ id: `c${i}` })) : null,
        background_work_in_progress: shells
            ? { subagents: 0, shells, monitors: 0, scheduled_wakeup_at: null, goal: false }
            : null,
    }
}

function modeOf(states, unread = 0) {
    return processActivityDisplayMode(summarizeProcessActivity(states, unread))
}

test('nothing live and nothing unread shows no indicator', () => {
    assert.equal(modeOf([]), null)
})

test('the cascade: pending request beats everything', () => {
    assert.equal(modeOf([ps('assistant_turn', { pending: 1 }), ps('user_turn', { shells: 1 })], 3), 'pending_request')
})

test('the cascade: unread beats a working session', () => {
    assert.equal(modeOf([ps('assistant_turn')], 1), 'unread')
})

test('the cascade: unread alone, with no live process', () => {
    assert.equal(modeOf([], 2), 'unread')
})

test('the cascade: assistant_turn beats shells and crons', () => {
    assert.equal(modeOf([ps('assistant_turn'), ps('user_turn', { shells: 2, crons: 1 })]), 'assistant_turn')
})

test('the cascade: user_turn shells beat crons', () => {
    assert.equal(modeOf([ps('user_turn', { shells: 1 }), ps('user_turn', { crons: 1 })]), 'background_shells')
})

test('the cascade: crons beat a plain active process', () => {
    assert.equal(modeOf([ps('user_turn', { crons: 1 }), ps('user_turn')]), 'crons')
})

test('the cascade: a starting session alone is a plain active process', () => {
    assert.equal(modeOf([ps('starting')]), 'active_process')
})

test('shells only count for user_turn sessions', () => {
    const summary = summarizeProcessActivity([ps('assistant_turn', { shells: 3 }), ps('starting', { shells: 2 })])
    assert.equal(summary.backgroundShellCount, 0)
    assert.equal(processActivityDisplayMode(summary), 'assistant_turn')
})

test('counts add up across the set', () => {
    const summary = summarizeProcessActivity(
        [ps('assistant_turn', { pending: 2, crons: 1 }), ps('user_turn', { shells: 1, crons: 2 })],
        4,
    )
    assert.deepEqual(summary, {
        processCount: 2,
        pendingRequestCount: 2,
        hasAssistantTurn: true,
        activeCronCount: 3,
        backgroundShellCount: 1,
        unreadCount: 4,
    })
})

test('tooltip: pending request, single and plural', () => {
    const one = summarizeProcessActivity([ps('assistant_turn', { pending: 1 })])
    assert.equal(processActivityTooltip(one, 'pending_request'), 'Pending request · 1 active session')
    const many = summarizeProcessActivity([ps('assistant_turn', { pending: 2 }), ps('user_turn', { shells: 1 })])
    assert.equal(
        processActivityTooltip(many, 'pending_request'),
        '2 pending requests · 2 active sessions (1 background shell)',
    )
})

test('tooltip: unread, with and without live sessions', () => {
    assert.equal(processActivityTooltip(summarizeProcessActivity([], 1), 'unread'), '1 unread session')
    assert.equal(
        processActivityTooltip(summarizeProcessActivity([ps('user_turn', { crons: 2 })], 3), 'unread'),
        '3 unread sessions · 1 active session (2 active crons)',
    )
})

test('tooltip: process modes list the active sessions', () => {
    const summary = summarizeProcessActivity([ps('assistant_turn'), ps('user_turn', { shells: 2 })])
    assert.equal(processActivityTooltip(summary, 'assistant_turn'), '2 active sessions (2 background shells)')
})
