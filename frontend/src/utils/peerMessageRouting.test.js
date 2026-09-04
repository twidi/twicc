import test from 'node:test'
import assert from 'node:assert/strict'

import { peerMessageRouting, peerRoutingSessionTitle, peerRoutingText } from './peerMessageRouting.js'

const labels = { '-repo': 'repo', '-repo-wt': 'repo › wt' }
const label = id => labels[id] || id

test('the own local session wins, per direction', () => {
    const inbound = {
        direction: 'in',
        delivered_to_session: { id: 's1', title: 'Landed here', project_id: '-repo' },
        effective_session: { id: 's9', title: 'Elsewhere', project_id: '-other' },
    }
    assert.deepEqual(peerMessageRouting(inbound), {
        sessionId: 's1', sessionTitle: 'Landed here', projectId: '-repo', fromConversation: false,
    })
    const outbound = { direction: 'out', origin_session: { id: 's2', title: '', project_id: null } }
    assert.deepEqual(peerMessageRouting(outbound), {
        sessionId: 's2', sessionTitle: 'Untitled session', projectId: null, fromConversation: false,
    })
})

test('falls back to the thread session, then to a bare project, then to nothing', () => {
    const viaThread = {
        direction: 'in',
        effective_session: { id: 's3', title: 'Backend update', project_id: '-repo' },
        effective_project: { id: '-repo', source: 'conversation' },
    }
    assert.deepEqual(peerMessageRouting(viaThread), {
        sessionId: 's3', sessionTitle: 'Backend update', projectId: '-repo', fromConversation: true,
    })
    const attached = { direction: 'out', effective_project: { id: '-repo', source: 'attached' } }
    assert.deepEqual(peerMessageRouting(attached), {
        sessionId: null, sessionTitle: '', projectId: '-repo', fromConversation: false,
    })
    const inherited = { direction: 'out', effective_project: { id: '-repo', source: 'conversation' } }
    assert.equal(peerMessageRouting(inherited).fromConversation, true)
    assert.equal(peerMessageRouting({ direction: 'in' }), null)
    assert.equal(peerMessageRouting(null), null)
})

test('session titles flatten and cut at 40 characters', () => {
    const long = 'A'.repeat(45)
    assert.equal(peerRoutingSessionTitle({ sessionTitle: long }), `${'A'.repeat(40)}…`)
    assert.equal(peerRoutingSessionTitle({ sessionTitle: '  two\n lines ' }), 'two lines')
    assert.equal(peerRoutingSessionTitle(null), '')
})

test('renders one plain-text line for notifications', () => {
    assert.equal(
        peerRoutingText({ sessionId: 's', sessionTitle: 'Backend update', projectId: '-repo-wt' }, label),
        'session “Backend update” in repo › wt',
    )
    assert.equal(peerRoutingText({ sessionId: null, sessionTitle: '', projectId: '-repo' }, label), 'in repo')
    assert.equal(peerRoutingText({ sessionId: 's', sessionTitle: 'Solo', projectId: null }, label), 'session “Solo”')
    assert.equal(peerRoutingText(null, label), '')
})
