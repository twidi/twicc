import assert from 'node:assert/strict'
import test from 'node:test'

import { agentControlHeaderLabel, agentControlExpectedCount } from './agentControlTools.js'

const interaction = { opensRun: false }
const resumedInteraction = { opensRun: true }

test('header label: null without an interaction, whatever the name', () => {
    assert.equal(agentControlHeaderLabel('SendMessage', null), null)
    assert.equal(agentControlHeaderLabel('TaskStop', null), null)
    assert.equal(agentControlHeaderLabel('TaskOutput', null), null)
    assert.equal(agentControlHeaderLabel('Bash', null), null)
})

test('header label: the three control tools, with an interaction', () => {
    assert.equal(agentControlHeaderLabel('SendMessage', interaction), 'Send message')
    assert.equal(agentControlHeaderLabel('TaskStop', interaction), 'Stop agent')
    assert.equal(agentControlHeaderLabel('TaskOutput', interaction), 'Agent output')
})

test('header label: a non-control name stays null even with an interaction', () => {
    assert.equal(agentControlHeaderLabel('Bash', interaction), null)
})

test('expected count: null without an interaction or on a non-control name', () => {
    assert.equal(agentControlExpectedCount('SendMessage', null), null)
    assert.equal(agentControlExpectedCount('Bash', interaction), null)
})

test('expected count: a resumed SendMessage expects 2, a queued one 1', () => {
    assert.equal(agentControlExpectedCount('SendMessage', resumedInteraction), 2)
    assert.equal(agentControlExpectedCount('SendMessage', interaction), 1)
})

test('expected count: TaskStop / TaskOutput always expect 1', () => {
    assert.equal(agentControlExpectedCount('TaskStop', resumedInteraction), 1)
    assert.equal(agentControlExpectedCount('TaskOutput', resumedInteraction), 1)
})
