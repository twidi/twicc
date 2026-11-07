import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { shareCreatorBadge } from './shareCreatorBadge.js'

test('human or legacy provenance has no badge', () => {
    assert.equal(shareCreatorBadge({ kind: 'human_or_legacy', session: null }), null)
    assert.equal(shareCreatorBadge(null), null)
})

const HOME = { name: 'home', params: {}, query: {} }

test('visible agent creator links with its title', () => {
    assert.deepEqual(shareCreatorBadge({
        kind: 'agent',
        session: { id: 'agent-1', title: 'Builder', project_id: '-tmp-project' },
    }, HOME), {
        label: 'Builder',
        to: {
            name: 'session',
            params: { projectId: '-tmp-project', sessionId: 'agent-1' },
        },
    })
})

test('the creator link keeps the current frame', () => {
    const createdBy = { kind: 'agent', session: { id: 'agent-1', title: 'B', project_id: '-tmp-project' } }
    const allProjects = { name: 'projects-all', params: {}, query: { workspace: 'w1' } }
    assert.deepEqual(shareCreatorBadge(createdBy, allProjects).to, {
        name: 'projects-session',
        params: { projectId: '-tmp-project', sessionId: 'agent-1' },
        query: { workspace: 'w1' },
    })
    const projectFilter = { name: 'project', params: { projectId: '-other' }, query: {} }
    assert.deepEqual(shareCreatorBadge(createdBy, projectFilter).to, {
        name: 'session',
        params: { projectId: '-other', sessionId: 'agent-1' },
    })
})

test('untitled visible creator falls back to its session id', () => {
    assert.equal(shareCreatorBadge({
        kind: 'agent',
        session: { id: 'agent-1', title: '', project_id: '-tmp-project' },
    }, HOME).label, 'agent-1')
})

test('hidden agent creator has the exact non-link badge', () => {
    assert.deepEqual(shareCreatorBadge({ kind: 'agent', session: null }), {
        label: 'Agent-created (hidden session)',
        to: null,
    })
})

test('ShareListPanel consumes the tested helper', () => {
    const source = readFileSync(
        new URL('../components/share/ShareListPanel.vue', import.meta.url), 'utf8',
    )
    assert.match(source, /import \{ shareCreatorBadge \} from '\.\.\/\.\.\/utils\/shareCreatorBadge'/)
    assert.match(source, /return shareCreatorBadge\(share\.created_by, route\)/)
    assert.match(source, /creatorBadge\(s\)\.label/)
    assert.match(source, /creatorBadge\(s\)\.to/)
})
