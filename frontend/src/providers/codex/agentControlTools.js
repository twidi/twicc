/**
 * Pure rules for Codex's multi-agent v2 control tools
 * (`collaboration__followup_task` / `collaboration__send_message` /
 * `collaboration__interrupt_agent`): the header label, the expected result
 * count of a control card, and the ciphertext `message` masking, design
 * §8.3 ("Control cards: Header", "Expected result count", "Codex input
 * rendering").
 *
 * `codex/toolHelpers.js` cannot be imported under Node (extensionless
 * imports, `.vue` components), so these rules live in this import-free
 * module and the helper delegates.
 *
 * Imports nothing, so `node --test` loads it directly.
 */

export const FOLLOWUP_TASK_TOOL_NAME = 'collaboration__followup_task'
export const SEND_MESSAGE_TOOL_NAME = 'collaboration__send_message'
export const INTERRUPT_AGENT_TOOL_NAME = 'collaboration__interrupt_agent'

// Control tool name -> header label, shown only on a control card (an
// interaction is set).
const HEADER_LABEL_BY_TOOL = {
    [FOLLOWUP_TASK_TOOL_NAME]: 'Follow-up task',
    [SEND_MESSAGE_TOOL_NAME]: 'Send message',
    [INTERRUPT_AGENT_TOOL_NAME]: 'Interrupt agent',
}

// The two collaboration tools whose ``message`` argument is Codex
// ciphertext, never a rendered field.
const ENCRYPTED_MESSAGE_TOOLS = new Set([FOLLOWUP_TASK_TOOL_NAME, SEND_MESSAGE_TOOL_NAME])

/**
 * The header label of a control card, or null when the call is not a
 * control card (no interaction) or its name is not a control tool.
 */
export function agentControlHeaderLabel(name, agentInteraction) {
    if (!agentInteraction) return null
    return HEADER_LABEL_BY_TOOL[name] ?? null
}

/**
 * The expected result count of a control card: `followup_task` that opens a
 * run expects the ack and the run's end signal (2); every other control
 * call expects 1. Null when the call is not a control card, or its name is
 * not a control tool — the helper keeps its own rule then.
 */
export function agentControlExpectedCount(name, agentInteraction) {
    if (!agentInteraction || !(name in HEADER_LABEL_BY_TOOL)) return null
    return name === FOLLOWUP_TASK_TOOL_NAME && agentInteraction.opensRun ? 2 : 1
}

/**
 * Mask the ciphertext `message` argument of `followup_task` / `send_message`
 * so the card never shows Codex's encrypted payload. Returns a copy — the
 * original input is never mutated — or the input unchanged for any other
 * name or an input with no `message` field. Name-based: applies whether or
 * not the card is a control card yet.
 */
export function maskEncryptedMessage(name, input) {
    if (!input || !ENCRYPTED_MESSAGE_TOOLS.has(name) || !('message' in input)) return input
    return { ...input, message: 'encrypted message' }
}
