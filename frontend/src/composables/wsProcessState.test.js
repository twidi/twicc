import assert from 'node:assert/strict'
import test from 'node:test'

import { applyBackgroundWork, dropsProcessStateOnRemoval, shouldNotifyProcessState } from './wsProcessState.js'

const work = { subagents: 0, shells: 1, monitors: 0, scheduled_wakeup_at: null, goal: false }

test('equivalent background snapshots preserve the stored object', () => {
    const states = { s1: { state: 'user_turn', background_work_in_progress: work } }
    applyBackgroundWork(states, { session_id: 's1', background_work_in_progress: { ...work } })
    assert.strictEqual(states.s1.background_work_in_progress, work)
})

test('patches the known process in place, keeping its live tools', () => {
    const tools = [{ id: 'tool-1' }]
    const processStates = { s1: { state: 'user_turn', tools, background_work_in_progress: null } }

    assert.equal(applyBackgroundWork(processStates, { session_id: 's1', background_work_in_progress: work }), true)
    assert.deepEqual(processStates.s1.background_work_in_progress, work)
    assert.equal(processStates.s1.tools, tools)
})

test('nothing running clears the value', () => {
    const processStates = { s1: { state: 'user_turn', background_work_in_progress: work } }

    applyBackgroundWork(processStates, { session_id: 's1', background_work_in_progress: null })
    assert.equal(processStates.s1.background_work_in_progress, null)
})

test('an unknown session is left alone', () => {
    const processStates = {}

    assert.equal(applyBackgroundWork(processStates, { session_id: 's1', background_work_in_progress: work }), false)
    assert.deepEqual(processStates, {})
})

test('a shell count change in user_turn asks for a recompute, an unchanged one does not', () => {
    const processStates = { s1: { state: 'user_turn', background_work_in_progress: work } }

    assert.equal(applyBackgroundWork(processStates, { session_id: 's1', background_work_in_progress: { ...work } }), false)
    assert.equal(applyBackgroundWork(processStates, { session_id: 's1', background_work_in_progress: { ...work, shells: 2 } }), true)
    assert.equal(applyBackgroundWork(processStates, { session_id: 's1', background_work_in_progress: null }), true)
    assert.equal(processStates.s1.background_work_in_progress, null)
})

test('outside user_turn, or for work other than shells, nothing to recompute', () => {
    const processStates = { s1: { state: 'assistant_turn', background_work_in_progress: null } }
    assert.equal(applyBackgroundWork(processStates, { session_id: 's1', background_work_in_progress: work }), false)
    assert.deepEqual(processStates.s1.background_work_in_progress, work)

    const idle = { s2: { state: 'user_turn', background_work_in_progress: null } }
    assert.equal(
        applyBackgroundWork(idle, { session_id: 's2', background_work_in_progress: { ...work, shells: 0, subagents: 1 } }),
        false,
    )
})

test('a session leaving the store takes its real process state along', () => {
    const processStates = {
        real: { state: 'assistant_turn' },
        agent: { state: 'assistant_turn', synthetic: true },
    }
    assert.equal(dropsProcessStateOnRemoval(processStates, 'real'), true)
    // A subagent's synthetic state belongs to the run model (unloadSession cleans it).
    assert.equal(dropsProcessStateOnRemoval(processStates, 'agent'), false)
    assert.equal(dropsProcessStateOnRemoval(processStates, 'unknown'), false)
})

test('a resync restores a state without notifying; a regular message notifies', () => {
    assert.equal(shouldNotifyProcessState({ state: 'user_turn', resync: true }), false)
    assert.equal(shouldNotifyProcessState({ state: 'user_turn' }), true)
})
