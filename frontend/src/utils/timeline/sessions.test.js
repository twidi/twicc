import assert from 'node:assert/strict'
import test from 'node:test'

import { groupHeadIdentity, sessionMention } from './sessions.js'

const entry = (fields = {}) => ({
    title: 'My session', project_id: 'proj', provider: 'codex', hidden: false, archived: false, ...fields,
})

test('a session with an entry is a link with its title and provider; an archived one too, with no state field', () => {
    const mention = sessionMention('s1', { s1: entry({ archived: true }) })
    assert.deepEqual(mention, { id: 's1', title: 'My session', provider: 'codex', projectId: 'proj', link: true })
})

test('a hidden session is plain text, even when archived too; the mention carries no state field', () => {
    const mention = sessionMention('s1', { s1: entry({ hidden: true, archived: true }) })
    assert.equal(mention.link, false)
    assert.ok(!('hidden' in mention) && !('archived' in mention))
})

test('a session with no entry is plain text: the full id for a head, 8 characters elsewhere', () => {
    const id = 'abcdefghijklmnop'
    const head = sessionMention(id, {}, { fullId: true, fallbackProvider: 'claude_code' })
    assert.equal(head.title, id)
    assert.equal(head.link, false)
    assert.equal(head.provider, 'claude_code')
    assert.equal(sessionMention(id, {}).title, 'abcdefgh')
})

test('an untitled session falls back to its id', () => {
    assert.equal(sessionMention('s1', { s1: entry({ title: null }) }, { fullId: true }).title, 's1')
    assert.equal(sessionMention('s1', { s1: entry({ title: '' }) }).title, 's1')
})

test('a known subagent is plain text', () => {
    const mention = sessionMention('s1', { s1: entry() }, { isSubagent: (id) => id === 's1' })
    assert.equal(mention.link, false)
})

test('an entry without a project cannot be opened', () => {
    assert.equal(sessionMention('s1', { s1: entry({ project_id: null }) }).link, false)
})

test('the entry provider wins over the fallback', () => {
    assert.equal(sessionMention('s1', { s1: entry() }, { fallbackProvider: 'claude_code' }).provider, 'codex')
})

const event = (fields = {}) => ({ id: 1, session_id: 's1', project_id: 'p1', provider: 'codex', data: {}, ...fields })

test('head identity: provider fallback order is entry, event column, payload, none', () => {
    const noEntry = (e) => groupHeadIdentity(e, {}, { kind: 'global' }).mention.provider
    assert.equal(noEntry(event()), 'codex')
    assert.equal(noEntry(event({ provider: null, data: { provider: 'claude_code' } })), 'claude_code')
    assert.equal(noEntry(event({ provider: null })), null)
    assert.equal(groupHeadIdentity(event(), { s1: entry({ provider: 'claude_code' }) }, { kind: 'global' }).mention.provider, 'claude_code')
})

test('head identity: the event project wins, the side-table project is the fallback', () => {
    assert.equal(groupHeadIdentity(event(), { s1: entry() }, { kind: 'global' }).projectId, 'p1')
    assert.equal(groupHeadIdentity(event({ project_id: null }), { s1: entry() }, { kind: 'global' }).projectId, 'proj')
    assert.equal(groupHeadIdentity(event({ project_id: null }), {}, { kind: 'global' }).projectId, null)
})

test('the project name shows on global and workspace pages, never on a worktree page', () => {
    const show = (scope, projectId = 'p1') => groupHeadIdentity(event({ project_id: projectId }), {}, scope).showProject
    assert.equal(show({ kind: 'global' }), true)
    assert.equal(show({ kind: 'workspace' }), true)
    assert.equal(show({ kind: 'worktree', projectId: 'p1' }), false)
    assert.equal(show({ kind: 'worktree', projectId: 'other' }), false)
    assert.equal(show({ kind: 'global' }, null), false)
})

test('on a project page only the rows of its worktrees carry the name', () => {
    const scope = { kind: 'project', projectId: 'main' }
    assert.equal(groupHeadIdentity(event({ project_id: 'main' }), {}, scope).showProject, false)
    assert.equal(groupHeadIdentity(event({ project_id: 'main-wt' }), {}, scope).showProject, true)
})
