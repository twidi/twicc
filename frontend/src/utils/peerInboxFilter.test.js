import test from 'node:test'
import assert from 'node:assert/strict'

import {
    buildPeerInboxSearchUrl,
    peerInboxFiltersActive,
    peerInboxSelectablePeers,
    peerInboxSelectableProjects,
    peerInboxVisibleMessages,
    peerInboxView,
} from './peerInboxFilter.js'

function message(id, overrides = {}) {
    return {
        id,
        peer_id: 'peer-a',
        direction: 'in',
        status: 'pending',
        ...overrides,
    }
}

test('partitions received messages from history and hides empty sections', () => {
    const received = message(1)
    const delivered = message(2, { status: 'delivered' })
    const outbound = message(3, { direction: 'out' })

    assert.deepEqual(peerInboxView([received, delivered, outbound], false), {
        received: [received],
        history: [delivered, outbound],
        emptyMessage: null,
    })
})

test('distinguishes an empty inbox from an empty filtered result', () => {
    assert.deepEqual(peerInboxView([], false), {
        received: [],
        history: [],
        emptyMessage: 'No peer messages yet.',
    })
    assert.deepEqual(peerInboxView([], true), {
        received: [],
        history: [],
        emptyMessage: 'No messages match your filters.',
    })
})

test('treats a nonempty peer or trimmed query as an active filter', () => {
    assert.equal(peerInboxFiltersActive('', ''), false)
    assert.equal(peerInboxFiltersActive('', '   '), false)
    assert.equal(peerInboxFiltersActive('peer-a', ''), true)
    assert.equal(peerInboxFiltersActive('', ' needle '), true)
    assert.equal(peerInboxFiltersActive('', '', ''), false)
    assert.equal(peerInboxFiltersActive('', '', '-home-me-repo'), true)
})

test('offers established peers and any peer that owns message history', () => {
    const peers = [
        { id: 'active', state: 'active' },
        { id: 'broken', state: 'broken' },
        { id: 'revoked', state: 'revoked' },
        { id: 'pending-empty', state: 'pending_received' },
        { id: 'pending-with-history', state: 'pending_sent' },
    ]
    const messages = [
        message(1, { peer_id: 'pending-with-history' }),
    ]

    assert.deepEqual(
        peerInboxSelectablePeers(peers, messages).map(peer => peer.id),
        ['active', 'broken', 'revoked', 'pending-with-history'],
    )
})

test('hides revoked messages from the default inbox', () => {
    const messages = [
        message(1, { peer_id: 'active' }),
        message(2, { peer_id: 'revoked' }),
    ]
    const peers = [
        { id: 'active', state: 'active' },
        { id: 'revoked', state: 'revoked' },
    ]

    assert.deepEqual(peerInboxVisibleMessages(messages, peers), [messages[0]])
})

test('builds an encoded search URL with the 200-row history limit', () => {
    assert.equal(
        buildPeerInboxSearchUrl('peer/a', ' "exact words" '),
        '/api/peer-messages/?limit=200&peer_id=peer%2Fa&q=%22exact+words%22',
    )
    assert.equal(
        buildPeerInboxSearchUrl('', 'needle'),
        '/api/peer-messages/?limit=200&q=needle',
    )
    assert.equal(
        buildPeerInboxSearchUrl('', '', '-home-me-repo'),
        '/api/peer-messages/?limit=200&project_id=-home-me-repo',
    )
    assert.equal(
        buildPeerInboxSearchUrl('peer-a', 'needle', '-home-me-repo'),
        '/api/peer-messages/?limit=200&peer_id=peer-a&project_id=-home-me-repo&q=needle',
    )
})

test('offers only projects that own messages, themselves or through a worktree', () => {
    const projects = [
        { id: 'direct' },
        { id: 'via-worktree' },
        { id: 'silent' },
    ]
    const worktrees = {
        'via-worktree': [{ id: 'via-worktree-wt' }],
        silent: [{ id: 'silent-wt' }],
    }
    const withMessages = new Set(['direct', 'via-worktree-wt'])

    assert.deepEqual(
        peerInboxSelectableProjects(projects, withMessages, id => worktrees[id] || []).map(p => p.id),
        ['direct', 'via-worktree'],
    )
    assert.deepEqual(peerInboxSelectableProjects(projects, new Set(), () => []), [])
})
