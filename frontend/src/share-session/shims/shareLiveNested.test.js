import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { connectShareLive } from './shareLive.js'
import { agentLinkState, setAgentLink, markAgentStopped, beginAgentFetch, applyAgentSnapshot } from '../../utils/agentLinkIndex.js'

test('live share routes nested launch and queue-only completion before delayed REST', () => {
    const previousSocket = globalThis.WebSocket, previousLocation = globalThis.location
    let socket
    globalThis.location = { origin: 'https://share.example.test' }
    globalThis.WebSocket = class { constructor() { socket = this } close() {} }
    const state = agentLinkState()
    const token = beginAgentFetch(state, 'root')
    try {
        const disconnect = connectShareLive({ tokenPath: '/share/token/', sessionId: 'root',
            onAgentLink: link => setAgentLink(state, link.owner_session_id, link.tool_use_id, { agentId: link.agent_id, rootSessionId: 'root' }),
            onAgentStopped: msg => markAgentStopped(state, msg.agent_session_id, msg.stopped_at, msg.root_session_id),
        })
        socket.onmessage({ data: JSON.stringify({ type: 'share_agent_link', link: { agent_id: 'child', owner_session_id: 'launcher', tool_use_id: 't' } }) })
        socket.onmessage({ data: JSON.stringify({ type: 'share_agent_stopped', agent_session_id: 'child', root_session_id: 'root', stopped_at: '2026-09-07T01:01:00Z' }) })
        applyAgentSnapshot(state, 'root', [{ agent_id: 'child', owner_session_id: 'launcher', tool_use_id: 't', running: true }], token)
        assert.equal(state.agentLinks.launcher.t.running, false)
        assert.equal(state.agentLinks.launcher.t.stoppedAt, '2026-09-07T01:01:00Z')
        disconnect()
    } finally {
        globalThis.WebSocket = previousSocket
        globalThis.location = previousLocation
    }
})

test('live share dispatches Codex idle and null wake-up without manufacturing a completion', async () => {
    const { markAgentIdle } = await import('../../utils/agentLinkIndex.js')
    const previousSocket = globalThis.WebSocket, previousLocation = globalThis.location
    let socket
    globalThis.location = { origin: 'https://share.example.test' }
    globalThis.WebSocket = class { constructor() { socket = this } close() {} }
    const state = agentLinkState()
    setAgentLink(state, 'launcher', 't', { agentId: 'child', rootSessionId: 'root' })
    try {
        const disconnect = connectShareLive({ tokenPath: '/share/token/', sessionId: 'root',
            onAgentIdle: msg => markAgentIdle(state, msg.agent_session_id, msg.agent_stopped_at),
        })
        const emit = at => socket.onmessage({ data: JSON.stringify({ type: 'share_agent_idle', agent_session_id: 'child', root_session_id: 'root', agent_stopped_at: at }) })
        emit('2026-09-07T01:01:00Z')
        assert.equal(state.agentLinks.launcher.t.agentStoppedAt, '2026-09-07T01:01:00Z')
        assert.equal(state.agentLinks.launcher.t.stoppedAt, null)
        emit(null)
        assert.equal(state.agentLinks.launcher.t.agentStoppedAt, null)
        assert.equal(state.agentLinks.launcher.t.stoppedAt, null)
        disconnect()
    } finally {
        globalThis.WebSocket = previousSocket
        globalThis.location = previousLocation
    }
})

test('live share duplicate link preserves cold snapshot state through the real shim action', () => {
    const source = readFileSync(new URL('./dataStoreShim.js', import.meta.url), 'utf8')
    const action = source.slice(source.indexOf('        addAgentLink(root, link)'), source.indexOf('        // Live: root session'))
    const state = Object.assign(agentLinkState(), new Function('setAgentLink', `return { ${action} }`)(setAgentLink))
    const stopped = '2026-09-07T01:01:00Z'
    const link = { agent_id: 'child', owner_session_id: 'launcher', tool_use_id: 't', running: false, agent_stopped_at: stopped }
    applyAgentSnapshot(state, 'root', [link], beginAgentFetch(state, 'root'))
    const previousSocket = globalThis.WebSocket, previousLocation = globalThis.location
    let socket
    globalThis.location = { origin: 'https://share.example.test' }
    globalThis.WebSocket = class { constructor() { socket = this } close() {} }
    try {
        const disconnect = connectShareLive({ tokenPath: '/share/token/', sessionId: 'root',
            onAgentLink: entry => state.addAgentLink('root', entry),
        })
        socket.onmessage({ data: JSON.stringify({ type: 'share_agent_link', link: { ...link, running: undefined, agent_stopped_at: null } }) })
        assert.equal(state.agentLinks.launcher.t.agentStoppedAt, stopped)
        assert.equal(state.agentLinks.launcher.t.running, false)
        disconnect()
    } finally {
        globalThis.WebSocket = previousSocket
        globalThis.location = previousLocation
    }
})
