import assert from 'node:assert/strict'
import test from 'node:test'

import { sessionLinkLocation, sessionRouteLocation } from './sessionRoute.js'

const target = { id: 's1', project_id: 'p-target' }

test('without options the base session route is kept', () => {
    const route = { name: 'session', params: { projectId: 'p-current' }, query: {} }
    assert.deepEqual(sessionRouteLocation(target, route), { name: 'session', params: { projectId: 'p-current', sessionId: 's1' } })
})

test('tab points to the tool tab of the session, in single-project mode', () => {
    const route = { name: 'session-files', params: { projectId: 'p-current' }, query: {} }
    assert.deepEqual(
        sessionRouteLocation(target, route, { tab: 'orchestration' }),
        { name: 'session-orchestration', params: { projectId: 'p-current', sessionId: 's1' } },
    )
})

test('tab in all-projects mode uses the target project and the projects- prefix', () => {
    const route = { name: 'projects-session', params: {}, query: {} }
    assert.deepEqual(
        sessionRouteLocation(target, route, { tab: 'orchestration' }),
        { name: 'projects-session-orchestration', params: { projectId: 'p-target', sessionId: 's1' } },
    )
})

test('the workspace query is carried with a tab', () => {
    const route = { name: 'session', params: { projectId: 'p' }, query: { workspace: 'w1' } }
    assert.deepEqual(sessionRouteLocation(target, route, { tab: 'orchestration' }).query, { workspace: 'w1' })
})

test('params adds the tab params, never over the frame', () => {
    const route = { name: 'session', params: { projectId: 'p-filter' }, query: {} }
    assert.deepEqual(
        sessionRouteLocation(target, route, { tab: 'artifacts', params: { rootKey: 'artifacts', filePath: 'a.md', projectId: 'x' } }),
        { name: 'session-artifacts', params: { rootKey: 'artifacts', filePath: 'a.md', projectId: 'p-filter', sessionId: 's1' } },
    )
})

// A resolved agent link: `/project/p-link/session/s1/workflows/wf_1?x=1#h` (the
// writer's frame is single-project mode on the session's own project).
const workflowLink = {
    name: 'session-workflows',
    params: { projectId: 'p-link', sessionId: 's1', runId: 'wf_1' },
    query: { x: '1' },
    hash: '#h',
}

test('a session link in all-projects mode keeps the projects- prefix and the workspace', () => {
    const route = { name: 'projects-session', params: { projectId: 'p-other', sessionId: 's0' }, query: { workspace: 'w1' } }
    assert.deepEqual(sessionLinkLocation(workflowLink, route), {
        name: 'projects-session-workflows',
        params: { projectId: 'p-link', sessionId: 's1', runId: 'wf_1' },
        query: { x: '1', workspace: 'w1' },
        hash: '#h',
    })
})

test('a session link in single-project mode keeps the current project filter', () => {
    const route = { name: 'session-files', params: { projectId: 'p-filter', sessionId: 's0' }, query: {} }
    assert.deepEqual(sessionLinkLocation(workflowLink, route), {
        name: 'session-workflows',
        params: { projectId: 'p-filter', sessionId: 's1', runId: 'wf_1' },
        query: { x: '1' },
        hash: '#h',
    })
})

test('the workspace written in the link never replaces the current frame', () => {
    const link = { name: 'projects-session', params: { projectId: 'p-link', sessionId: 's1' }, query: { workspace: 'w-link' } }
    const route = { name: 'session', params: { projectId: 'p-filter' }, query: {} }
    assert.deepEqual(sessionLinkLocation(link, route), {
        name: 'session',
        params: { projectId: 'p-filter', sessionId: 's1' },
    })
})

test('a subagent link keeps its subagent', () => {
    const link = { name: 'session-subagent', params: { projectId: 'p-link', sessionId: 's1', subagentId: 'a1' }, query: {} }
    const route = { name: 'projects-all', params: {}, query: {} }
    assert.deepEqual(sessionLinkLocation(link, route), {
        name: 'projects-session-subagent',
        params: { projectId: 'p-link', sessionId: 's1', subagentId: 'a1' },
    })
})

test('every session tool tab is covered', () => {
    const route = { name: 'projects-all', params: {}, query: {} }
    for (const tab of ['files', 'artifacts', 'git', 'terminal', 'orchestration', 'plan', 'tasks', 'workflows', 'browser']) {
        const link = { name: `session-${tab}`, params: { projectId: 'p', sessionId: 's1' }, query: {} }
        assert.equal(sessionLinkLocation(link, route).name, `projects-session-${tab}`)
    }
})

test('a non-session route is left alone', () => {
    const route = { name: 'session', params: { projectId: 'p' }, query: {} }
    for (const resolved of [
        null,
        { name: 'project-files', params: { projectId: 'p' }, query: {} },
        { name: 'projects-all', params: {}, query: {} },
        { name: undefined, params: {}, query: {} },
        { name: 'sessions-something', params: {}, query: {} },
    ]) {
        assert.equal(sessionLinkLocation(resolved, route), null, JSON.stringify(resolved))
    }
})
