import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { computed, reactive } from 'vue'
import { agentLinkState, setAgentLink, rootAgentToolLine } from './agentLinkIndex.js'
import { controlCardAgentName } from './agentCardState.js'

const source = readFileSync(new URL('../components/session/detail/items/ToolUseContent.vue', import.meta.url), 'utf8')
function block(start, end) { return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start))) }
const template = source.slice(source.indexOf('<template>'))

test('actual card running indicator reads only the store and the frozen flag', () => {
    // A spawn that predates a root restart still shows the robot when the store
    // says the agent runs (a resumed agent, design §8.3): no stale gate.
    const store = reactive({ running: true })
    const frozen = reactive({ value: false })
    const code = block('const isAgentRunning', 'const isAgentSpawnPending')
    const running = new Function('computed', 'dataStore', 'agentId', 'transcriptFrozen', `${code}; return isAgentRunning`)(
        computed, { isAgentRunning: id => id === 'child' && store.running }, { value: 'child' }, frozen)
    assert.equal(running.value, true)
    store.running = false
    assert.equal(running.value, false)
    store.running = true
    frozen.value = true
    assert.equal(running.value, false)
    for (const removed of ['isStaleAgentUse', 'agentReportedIdle', 'agentRunEndsOnSubagentIdle', 'agentLink?.isBackground']) {
        assert.equal(source.includes(removed), false, `${removed} is gone`)
    }
})

test('actual Stop button: a running background agent, never in the share viewer', () => {
    const code = block('const canStopAgent', 'const controlAgentName')
    const showStop = fetchToolResult => new Function('computed', 'fetchToolResult', 'providerHelpers', 'isAgentRunning', 'agentRunState',
        `${code}; return showStopAgent`)(computed, fetchToolResult, { value: { canStopSubagent: () => true } },
        { value: true }, { value: { runBackground: true } }).value
    assert.equal(showStop(async () => ({ results: [] })), false, 'share viewer: fetchToolResult is provided')
    assert.equal(showStop(null), true)
    const foreground = new Function('computed', 'fetchToolResult', 'providerHelpers', 'isAgentRunning', 'agentRunState',
        `${code}; return showStopAgent`)(computed, null, { value: { canStopSubagent: () => true } },
        { value: true }, { value: { runBackground: false } }).value
    assert.equal(foreground, false, 'a foreground run')
    assert.equal(template.match(/v-if="showStopAgent"/g)?.length, 2, 'the button and its tooltip')
})

test('actual header: a spawn card keeps its provider summary; a control card shows the agent name', () => {
    const code = block('const controlAgentName', '// --- End of agent card state')
    const name = isControl => new Function('computed', 'isControlCard', 'agentId', 'dataStore', 'controlCardAgentName',
        `${code}; return controlAgentName`)(computed, { value: isControl }, { value: 'f00dcafe12345678' },
        { getAgentLinkInfo: () => null, getSession: () => null }, controlCardAgentName).value
    assert.equal(name(false), null, 'spawn and other cards: no agent name')
    assert.equal(name(true), 'Agent "f00dcafe"')
    const nameBranch = template.indexOf('v-if="controlAgentName"')
    const summaryBranch = template.indexOf('v-else-if="summaryRendering"')
    assert.ok(nameBranch > 0 && summaryBranch > nameBranch, 'the agent name replaces summaryRendering')
    assert.ok(template.includes('<template v-if="isAgentCard">'), 'the agent widget shows on every agent card')
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
