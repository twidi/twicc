/**
 * Pure rules for Claude Code's agent control tools (`SendMessage` /
 * `TaskStop` / `TaskOutput`): the header label and expected result count of
 * a control card, design §8.3 ("Control cards: Header", "Expected result
 * count").
 *
 * `claude_code/toolHelpers.js` cannot be imported under Node (extensionless
 * imports, `.vue` components), so these rules live in this import-free
 * module and the helper delegates.
 *
 * Imports nothing, so `node --test` loads it directly.
 */

// Control tool name -> header label, shown only on a control card (an
// interaction is set). 71 of 75 `TaskStop` calls and all 28 `TaskOutput`
// calls target shells and must keep today's label, so this map is never
// consulted without an interaction.
const HEADER_LABEL_BY_TOOL = {
    SendMessage: 'Send message',
    TaskStop: 'Stop agent',
    TaskOutput: 'Agent output',
}

/**
 * The header label of a control card, or null when the call is not a
 * control card (no interaction) or its name is not a control tool.
 */
export function agentControlHeaderLabel(name, agentInteraction) {
    if (!agentInteraction) return null
    return HEADER_LABEL_BY_TOOL[name] ?? null
}

/**
 * The expected result count of a control card: `SendMessage` that opens a
 * run expects the ack and the run's end signal (2); every other control
 * call expects 1. Null when the call is not a control card, or its name is
 * not a control tool — the helper keeps its own rule then.
 */
export function agentControlExpectedCount(name, agentInteraction) {
    if (!agentInteraction || !(name in HEADER_LABEL_BY_TOOL)) return null
    return name === 'SendMessage' && agentInteraction.opensRun ? 2 : 1
}
