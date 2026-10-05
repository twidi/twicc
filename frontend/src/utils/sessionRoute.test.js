import assert from 'node:assert/strict'
import test from 'node:test'

import { sessionRouteLocation } from './sessionRoute.js'

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
