import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, reactive } from 'vue'
import { agentLinkState, setAgentLink, rootAgentToolLine, markAgentIdle, beginAgentFetch, applyAgentSnapshot } from './agentLinkIndex.js'

const source = readFileSync(new URL('../components/session/detail/items/ToolUseContent.vue', import.meta.url), 'utf8')
function block(start, end) { return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))) }

test('actual card computeds keep nested pulse after launcher idle and stop it at root cutoff', () => {
    const props = reactive({ sessionId: 'launcher', parentSessionId: 'root', timestamp: '2026-09-07T01:00:00Z' })
    const dataStore = reactive({ sessions: { root: {}, launcher: { cutoff: Date.parse('2026-09-07T01:01:00Z') } }, getSession: () => null })
    const agentLink = reactive({ value: { isBackground: true } })
    const code = block('const isStaleAgentUse', '// Unix timestamp') + block('const agentReportedIdle', '// Whether the pre-ack')
    const run = new Function('computed', 'props', 'dataStore', 'getSessionCutoffMs', 'rootSessionId', 'agentId', 'agentLink', 'toolHelpers', 'transcriptFrozen', 'isTask', 'toolState', `${code}; return isAgentRunning`)
    const running = run(computed, props, dataStore, s => s?.cutoff || 0, { value: 'root' }, { value: 'child' }, agentLink, { value: { agentRunEndsOnSubagentIdle: () => false } }, { value: false }, { value: true }, { value: { resultCount: 1 } })
    assert.equal(running.value, true)
    dataStore.sessions.root.cutoff = Date.parse('2026-09-07T01:01:00Z')
    assert.equal(running.value, false)
    dataStore.sessions.root.cutoff = 0
    agentLink.value.stoppedAt = '2026-09-07T01:02:00Z'
    assert.equal(running.value, false)
})
test('actual provided comment context updates when nested owner links arrive', () => {
    const state = reactive(agentLinkState())
    const props = reactive({ sessionId: 'child', parentSessionId: 'root', projectId: 'p', toolId: 'edit', lineNum: 7 })
    const dataStore = { getRootAgentToolUseLineNum: (root, child) => rootAgentToolLine(state, root, child) }
    let context
    const code = block('const rootSessionId', '// Line number') + block('const parentToolUseLineNum', '// Provide') + block("provide('codeCommentToolContext'", '\n\n')
    new Function('computed', 'reactive', 'props', 'dataStore', 'provide', code)(computed, reactive, props, dataStore, (_key, value) => { context = value })
    assert.equal(context.subagentToolLineNum, null)
    setAgentLink(state, 'root', 'spawn', { agentId: 'launcher', rootSessionId: 'root', toolUseLineNum: 115 })
    setAgentLink(state, 'launcher', 'nested', { agentId: 'child', rootSessionId: 'root', toolUseLineNum: 60 })
    assert.equal(context.subagentToolLineNum, 115)
})
test('actual navigation sends the root route for nested children', () => {
    let target
    const code = block('function navigateToSubagent()', '// --- Workflow link')
    new Function('agentId', 'openSubagent', 'router', 'sessionRouteLocation', 'props', 'rootSessionId', 'route', `${code}; navigateToSubagent()`)(
        { value: 'child' }, null, { push: value => { target = value } }, (session, _route, extra) => ({ session, extra }), { projectId: 'p', sessionId: 'launcher' }, { value: 'root' }, {},
    )
    assert.equal(target.session.id, 'root')
    assert.equal(target.extra.subagentId, 'child')
})

test('actual idle computed trusts Codex and observes null wake-up over cached idle', () => {
    const child = reactive({ last_stopped_at: '2026-09-07T01:01:00Z' })
    const provider = reactive({ trusted: true })
    const link = reactive({ value: { agentStoppedAt: '2026-09-07T01:01:00Z' } })
    const code = block('const agentReportedIdle', 'const isAgentRunning')
    const idle = new Function('computed', 'agentId', 'agentLink', 'toolHelpers', 'dataStore', `${code}; return agentReportedIdle`)(computed,
        { value: 'child' }, link, { value: { agentRunEndsOnSubagentIdle: () => provider.trusted } }, { getSession: () => child })
    assert.equal(idle.value, true)
    child.last_stopped_at = null
    assert.equal(idle.value, false)
    child.last_stopped_at = '2026-09-07T01:01:00Z'
    provider.trusted = false
    assert.equal(idle.value, false)
    link.value.stoppedAt = '2026-09-07T01:02:00Z'
    assert.equal(idle.value, true)
})

test('actual card pulse retains first-link idle and wake events across an old snapshot', () => {
    for (const trusted of [false, true]) {
        for (const idle of [null, '2026-09-07T01:01:00Z']) {
            const state = reactive(agentLinkState())
            const token = beginAgentFetch(state, 'root')
            markAgentIdle(state, 'child', idle)
            applyAgentSnapshot(state, 'root', [{ agent_id: 'child', owner_session_id: 'launcher',
                tool_use_id: 'spawn', running: false, is_background: true,
                agent_stopped_at: '2026-09-07T01:00:00Z' }], token)
            const code = block('const agentReportedIdle', '// Whether the pre-ack')
            const running = new Function('computed', 'dataStore', 'agentId', 'agentLink', 'toolHelpers',
                'transcriptFrozen', 'isTask', 'toolState', 'isStaleAgentUse', `${code}; return isAgentRunning`)(
                computed, { getSession: () => null }, { value: 'child' }, computed(() => state.agentLinkIndex.child),
                { value: { agentRunEndsOnSubagentIdle: () => trusted } }, { value: false }, { value: true },
                { value: { resultCount: 1 } }, { value: false })
            assert.equal(running.value, !(trusted && idle))
        }
    }
})
