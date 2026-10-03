import assert from 'node:assert/strict'
import test from 'node:test'

import { descendantActiveProcessStates, hasRunningAgent, visibleOrchestrationIndicators } from './orchestrationActivity.js'
import { processActivityDisplayMode, summarizeProcessActivity } from './processActivity.js'

// root ─┬─ manager ─┬─ worker-a
//       │           └─ worker-b
//       └─ sibling ─── cousin
function tree() {
    return {
        root: { state: 'assistant_turn', spawn_ancestors: [] },
        manager: { state: 'assistant_turn', spawn_ancestors: ['root'] },
        'worker-a': { state: 'assistant_turn', spawn_ancestors: ['manager', 'root'] },
        'worker-b': { state: 'user_turn', spawn_ancestors: ['manager', 'root'] },
        sibling: { state: 'starting', spawn_ancestors: ['root'] },
        cousin: { state: 'assistant_turn', spawn_ancestors: ['sibling', 'root'] },
        unrelated: { state: 'assistant_turn', spawn_ancestors: [] },
    }
}

function ids(processStates, sessions, sessionId) {
    const byState = new Map(Object.entries(processStates).map(([id, ps]) => [ps, id]))
    return descendantActiveProcessStates(processStates, sessions, sessionId).map(ps => byState.get(ps)).sort()
}

test('the root sees every descendant at any depth, never itself', () => {
    assert.deepEqual(ids(tree(), {}, 'root'), ['cousin', 'manager', 'sibling', 'worker-a'])
})

test('a mid-level session sees only its own sub-hierarchy', () => {
    // Not its parent (root), not its sibling branch (sibling, cousin).
    assert.deepEqual(ids(tree(), {}, 'manager'), ['worker-a'])
})

test('a leaf sees nothing', () => {
    assert.deepEqual(ids(tree(), {}, 'worker-a'), [])
})

test('user_turn sessions are left out', () => {
    const states = tree()
    states['worker-a'].state = 'user_turn'
    assert.deepEqual(ids(states, {}, 'manager'), [])
})

test('hidden sessions are left out, their visible descendants are not', () => {
    const sessions = { manager: { hidden: true } }
    assert.deepEqual(ids(tree(), sessions, 'root'), ['cousin', 'sibling', 'worker-a'])
})

test('synthetic subagent states and ephemeral runs are left out', () => {
    const states = tree()
    states.agent = { state: 'assistant_turn', synthetic: true, spawn_ancestors: ['root'] }
    states.eph = { state: 'assistant_turn', extra: { ephemeral: true }, spawn_ancestors: ['root'] }
    assert.deepEqual(ids(states, {}, 'root'), ['cousin', 'manager', 'sibling', 'worker-a'])
})

test('an entry without a chain (older payload, unspawned) is never a descendant', () => {
    const states = { child: { state: 'assistant_turn' } }
    assert.deepEqual(ids(states, {}, 'root'), [])
})

test('hasRunningAgent walks the whole tree', () => {
    const nodes = [
        { id: 'a', children: [{ id: 'a1', children: [{ id: 'a11', children: [] }] }] },
        { id: 'b', children: [] },
    ]
    assert.equal(hasRunningAgent(nodes, () => false), false)
    assert.equal(hasRunningAgent(nodes, id => id === 'a11'), true)
    assert.equal(hasRunningAgent(nodes, id => id === 'b'), true)
    assert.equal(hasRunningAgent([], () => true), false)
})

const SHELL = { subagents: 0, shells: 1, monitors: 0, scheduled_wakeup_at: null, goal: false }

test('a user_turn session that still runs a background shell is kept', () => {
    const states = tree()
    states['worker-b'].background_work_in_progress = SHELL
    assert.deepEqual(ids(states, {}, 'manager'), ['worker-a', 'worker-b'])
})

test('a user_turn session with only crons stays out', () => {
    const states = tree()
    states['worker-b'].active_crons = [{ id: 'c1' }]
    assert.deepEqual(ids(states, {}, 'manager'), ['worker-a'])
})

test('a hidden session with a shell stays out', () => {
    const states = tree()
    states['worker-b'].background_work_in_progress = SHELL
    assert.deepEqual(ids(states, { 'worker-b': { hidden: true } }, 'manager'), ['worker-a'])
})

function modeFor(states, sessionId) {
    return processActivityDisplayMode(summarizeProcessActivity(descendantActiveProcessStates(states, {}, sessionId)))
}

test('badge: a lone shell in user_turn shows the terminal', () => {
    const states = { shell: { state: 'user_turn', background_work_in_progress: SHELL, spawn_ancestors: ['root'] } }
    assert.equal(modeFor(states, 'root'), 'background_shells')
})

test('badge: working robot and pending hand outrank the shell', () => {
    const shell = { state: 'user_turn', background_work_in_progress: SHELL, spawn_ancestors: ['root'] }
    const working = { state: 'assistant_turn', spawn_ancestors: ['root'] }
    const asking = { state: 'assistant_turn', pending_requests: [{ request_id: 'r' }], spawn_ancestors: ['root'] }
    assert.equal(modeFor({ shell, working }, 'root'), 'assistant_turn')
    assert.equal(modeFor({ shell, working, asking }, 'root'), 'pending_request')
})

test('badge: an idle session without shell adds nothing', () => {
    const states = { idle: { state: 'user_turn', spawn_ancestors: ['root'] } }
    assert.equal(modeFor(states, 'root'), null)
})

test('visibleOrchestrationIndicators: both by default, one when restricted, none without activity', () => {
    const activity = { sessions: { processCount: 1 }, subagentsRunning: true }
    assert.deepEqual(visibleOrchestrationIndicators(activity), { sessions: activity.sessions, subagents: true })
    assert.deepEqual(visibleOrchestrationIndicators(activity, 'sessions'), { sessions: activity.sessions, subagents: false })
    assert.deepEqual(visibleOrchestrationIndicators(activity, 'subagents'), { sessions: null, subagents: true })
    assert.deepEqual(visibleOrchestrationIndicators(null), { sessions: null, subagents: false })
    assert.deepEqual(visibleOrchestrationIndicators({ sessions: null, subagentsRunning: false }, 'sessions'), { sessions: null, subagents: false })
})
