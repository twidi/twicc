import assert from 'node:assert/strict'
import test from 'node:test'

import {
    FOLLOWUP_TASK_TOOL_NAME,
    SEND_MESSAGE_TOOL_NAME,
    INTERRUPT_AGENT_TOOL_NAME,
    agentControlHeaderLabel,
    agentControlExpectedCount,
    maskEncryptedMessage,
} from './agentControlTools.js'

const interaction = { opensRun: false }
const resumedInteraction = { opensRun: true }

test('header label: null without an interaction, whatever the name', () => {
    assert.equal(agentControlHeaderLabel(FOLLOWUP_TASK_TOOL_NAME, null), null)
    assert.equal(agentControlHeaderLabel(SEND_MESSAGE_TOOL_NAME, null), null)
    assert.equal(agentControlHeaderLabel(INTERRUPT_AGENT_TOOL_NAME, null), null)
    assert.equal(agentControlHeaderLabel('exec_command', null), null)
})

test('header label: the three control tools, with an interaction', () => {
    assert.equal(agentControlHeaderLabel(FOLLOWUP_TASK_TOOL_NAME, interaction), 'Follow-up task')
    assert.equal(agentControlHeaderLabel(SEND_MESSAGE_TOOL_NAME, interaction), 'Send message')
    assert.equal(agentControlHeaderLabel(INTERRUPT_AGENT_TOOL_NAME, interaction), 'Interrupt agent')
})

test('header label: a non-control name stays null even with an interaction', () => {
    assert.equal(agentControlHeaderLabel('exec_command', interaction), null)
})

test('expected count: null without an interaction or on a non-control name', () => {
    assert.equal(agentControlExpectedCount(FOLLOWUP_TASK_TOOL_NAME, null), null)
    assert.equal(agentControlExpectedCount('exec_command', interaction), null)
})

test('expected count: an opened followup_task expects 2, a merged one 1', () => {
    assert.equal(agentControlExpectedCount(FOLLOWUP_TASK_TOOL_NAME, resumedInteraction), 2)
    assert.equal(agentControlExpectedCount(FOLLOWUP_TASK_TOOL_NAME, interaction), 1)
})

test('expected count: send_message / interrupt_agent always expect 1', () => {
    assert.equal(agentControlExpectedCount(SEND_MESSAGE_TOOL_NAME, resumedInteraction), 1)
    assert.equal(agentControlExpectedCount(INTERRUPT_AGENT_TOOL_NAME, resumedInteraction), 1)
})

test('maskEncryptedMessage: masks message on the two collaboration tools', () => {
    assert.deepEqual(maskEncryptedMessage(FOLLOWUP_TASK_TOOL_NAME, { message: 'cipher', agent_id: 'a1' }), {
        message: 'encrypted message',
        agent_id: 'a1',
    })
    assert.deepEqual(maskEncryptedMessage(SEND_MESSAGE_TOOL_NAME, { message: 'cipher' }), {
        message: 'encrypted message',
    })
})

test('maskEncryptedMessage: leaves other names and message-less inputs untouched', () => {
    const otherName = { message: 'cipher' }
    assert.equal(maskEncryptedMessage(INTERRUPT_AGENT_TOOL_NAME, otherName), otherName)
    const noMessage = { agent_id: 'a1' }
    assert.equal(maskEncryptedMessage(FOLLOWUP_TASK_TOOL_NAME, noMessage), noMessage)
    assert.equal(maskEncryptedMessage(FOLLOWUP_TASK_TOOL_NAME, null), null)
})

test('maskEncryptedMessage: never mutates the original object', () => {
    const original = { message: 'cipher' }
    const masked = maskEncryptedMessage(FOLLOWUP_TASK_TOOL_NAME, original)
    assert.notEqual(masked, original)
    assert.equal(original.message, 'cipher')
})
