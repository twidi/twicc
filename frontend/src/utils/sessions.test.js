// frontend/src/utils/sessions.test.js
//
// Pins the three unread rules. The mute cases are the point: a muted session
// must never read as unread on any surface, and its read/unread actions must
// disappear.

import test from 'node:test'
import assert from 'node:assert/strict'

import { hasUnreadContent, isSessionUnread, canToggleSessionReadState } from './sessions.js'

function makeSession(overrides = {}) {
    return {
        id: 'session_1',
        last_new_content_at: '2026-09-19T10:00:00Z',
        last_viewed_at: '2026-09-19T09:00:00Z',
        ...overrides,
    }
}

test('hasUnreadContent is true when content arrived after the last view', () => {
    assert.equal(hasUnreadContent(makeSession()), true)
})

test('hasUnreadContent is true when the session was never viewed', () => {
    assert.equal(hasUnreadContent(makeSession({ last_viewed_at: null })), true)
})

test('hasUnreadContent is false without content', () => {
    assert.equal(hasUnreadContent(makeSession({ last_new_content_at: null })), false)
})

test('hasUnreadContent is false when the last view is newer', () => {
    assert.equal(hasUnreadContent(makeSession({ last_viewed_at: '2026-09-19T11:00:00Z' })), false)
})

test('hasUnreadContent is false for a muted session', () => {
    assert.equal(hasUnreadContent(makeSession({ mute_on_user_turn: true })), false)
})

test('hasUnreadContent is false for a missing session', () => {
    assert.equal(hasUnreadContent(null), false)
})

test('isSessionUnread is true for an idle session with new content', () => {
    assert.equal(isSessionUnread(makeSession(), null), true)
})

test('isSessionUnread is false for a muted session, whatever the process state', () => {
    const muted = makeSession({ mute_on_user_turn: true })
    assert.equal(isSessionUnread(muted, null), false)
    assert.equal(isSessionUnread(muted, { state: 'user_turn' }), false)
})

test('isSessionUnread keeps its pre-existing exclusions', () => {
    assert.equal(isSessionUnread(makeSession({ hidden: true }), null), false)
    assert.equal(isSessionUnread(makeSession({ archived: true }), null), false)
    assert.equal(isSessionUnread(makeSession({ draft: true }), null), false)
    assert.equal(isSessionUnread(makeSession({ ephemeral: true }), null), false)
    assert.equal(isSessionUnread(makeSession({ parent_session_id: 'p' }), null), false)
    assert.equal(isSessionUnread(makeSession(), { state: 'assistant_turn' }), false)
    assert.equal(isSessionUnread(makeSession(), { state: 'user_turn' }), true)
})

test('canToggleSessionReadState allows an idle plain session', () => {
    assert.equal(canToggleSessionReadState(makeSession(), null), true)
    assert.equal(canToggleSessionReadState(makeSession(), { state: 'user_turn' }), true)
})

test('canToggleSessionReadState is false for a muted session', () => {
    assert.equal(canToggleSessionReadState(makeSession({ mute_on_user_turn: true }), null), false)
})

test('canToggleSessionReadState keeps its pre-existing exclusions', () => {
    assert.equal(canToggleSessionReadState(makeSession({ draft: true }), null), false)
    assert.equal(canToggleSessionReadState(makeSession({ ephemeral: true }), null), false)
    assert.equal(canToggleSessionReadState(makeSession({ archived: true }), null), false)
    assert.equal(canToggleSessionReadState(makeSession(), { state: 'assistant_turn' }), false)
    assert.equal(canToggleSessionReadState(null, null), false)
})
