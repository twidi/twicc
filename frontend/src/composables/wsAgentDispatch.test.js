import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { handleAgentEvent } from '../utils/agentLinkIndex.js'

// useWebSocket.js cannot be imported under Node (browser-only WS/Vue wiring at
// module scope, see its header) — slice the dispatcher source instead, as
// nestedAgentComponents.test.js does for the card.
const source = readFileSync(new URL('./useWebSocket.js', import.meta.url), 'utf8')
function block(start, end) { return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))) }

test('agent_link_created, agent_stopped, agent_interaction and agent_run_state all route through handleAgentEvent', () => {
    const code = block("case 'agent_link_created':", "case 'workflow_link_created':")
    for (const type of ['agent_link_created', 'agent_stopped', 'agent_interaction', 'agent_run_state']) {
        assert.match(code, new RegExp(`case '${type}':`))
    }
    assert.match(code, /handleAgentEvent\(store, msg\)/)

    let called = null
    const store = {
        markAgentStopped: (...args) => { called = ['markAgentStopped', args] },
        setAgentRunState: (...args) => { called = ['setAgentRunState', args] },
        setAgentInteraction: (...args) => { called = ['setAgentInteraction', args] },
        setAgentLink: (...args) => { called = ['setAgentLink', args] },
    }
    handleAgentEvent(store, { type: 'agent_link_created', parent_session_id: 'root', tool_use_id: 'spawn', agent_session_id: 'child' })
    assert.equal(called[0], 'setAgentLink')

    handleAgentEvent(store, { type: 'agent_stopped', agent_session_id: 'child', stopped_at: '2026-09-27T00:00:00Z', root_session_id: 'root' })
    assert.equal(called[0], 'markAgentStopped')

    handleAgentEvent(store, { type: 'agent_interaction', owner_session_id: 'root', tool_use_id: 'call', kind: 'wait' })
    assert.equal(called[0], 'setAgentInteraction')

    handleAgentEvent(store, { type: 'agent_run_state', agent_session_id: 'child', running: true })
    assert.equal(called[0], 'setAgentRunState')
})

test('tool_state no longer changes an agent state: only setToolState is called', () => {
    const code = block("case 'tool_state': {", "case 'active_processes':")
    assert.match(code, /setToolState/)
    assert.doesNotMatch(code, /getAgentLink/)
    assert.doesNotMatch(code, /markAgentStopped/)

    const calls = []
    const store = {
        setToolState: (...args) => calls.push(['setToolState', args]),
        getAgentLink: (...args) => { calls.push(['getAgentLink', args]); return { agentId: 'child', isBackground: false, rootSessionId: 'root' } },
        markAgentStopped: (...args) => calls.push(['markAgentStopped', args]),
    }
    const msg = { type: 'tool_state', session_id: 'root', tool_use_id: 'spawn', result_count: 1, completed_at: '2026-09-27T00:00:00Z', error: null, extra: null, tool_result_line_nums: [] }
    const run = new Function('store', 'msg', `switch (msg.type) { ${code} break }`)
    run(store, msg)

    assert.deepEqual(calls.map(c => c[0]), ['setToolState'])
})
