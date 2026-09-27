/**
 * The pure rules of an agent card (design §8.3): a spawn card (`Agent`, Codex
 * `spawn_agent`) or a control card (a call that targets an agent: Claude
 * `SendMessage` / `TaskStop` / `TaskOutput`, Codex `followup_task` /
 * `send_message` / `interrupt_agent`).
 *
 * `ToolUseContent.vue` feeds these rules from the store (or the share shim)
 * and hands `needsMoreRows` to the result fetch pipeline as the polling
 * predicate of an agent card. Every time is an ISO string compared as parsed
 * milliseconds.
 *
 * Imports only import-free modules, so `node --test` loads it directly.
 */
import { runKey } from './agentLinkIndex.js'
import { getAgentDisplay } from './agentLabel.js'

// Parsed milliseconds of an ISO time, or null when absent or unparsable.
function parseMs(value) {
    if (!value) return null
    const ms = Date.parse(value)
    return Number.isNaN(ms) ? null : ms
}

/**
 * Whether a run started before a cutoff. A null start counts as before any
 * cutoff > 0; a 0 cutoff applies none.
 */
export function startedBeforeCutoff(startedAt, cutoffMs) {
    if (!(cutoffMs > 0)) return false
    const startedMs = parseMs(startedAt)
    return startedMs === null || startedMs < cutoffMs
}

/**
 * Whether a call was made before a cutoff. Unlike a run start, a null call
 * time never counts as "before"; a 0 cutoff applies none.
 */
function callBeforeCutoff(callAt, cutoffMs) {
    if (!(cutoffMs > 0)) return false
    const callMs = parseMs(callAt)
    return callMs !== null && callMs < cutoffMs
}

/**
 * The root whose cutoff applies to an agent card (`treeCutoff`): the root of
 * the agent's run state, else of the card's own session when it is an agent,
 * else the shared session in the share viewer, else the card's root (app
 * only — in the share drawer the card's root is the agent itself).
 */
export function treeRootId({ runStateRoot, linkRoot, cardRootId, sharedSessionId }) {
    if (runStateRoot) return runStateRoot
    if (linkRoot) return linkRoot
    if (sharedSessionId != null) return sharedSessionId
    return cardRootId
}

/**
 * Whether the card's own run is still open: the card's call is a run (a spawn
 * or a run-opening interaction), its entry in `runState.runs` is open, and it
 * did not start before the cutoff of the run state's root. False while run
 * states are unavailable (share viewer) and on a frozen transcript.
 */
export function ownRunOpen({
    runState, ownerSessionId, toolUseId, isRunCall, cutoffMs, runStatesAvailable, transcriptFrozen,
}) {
    if (!isRunCall || !runStatesAvailable || transcriptFrozen) return false
    const run = runState?.runs?.[runKey(ownerSessionId, toolUseId)]
    return !!run?.open && !startedBeforeCutoff(run.startedAt, cutoffMs)
}

/**
 * Whether an agent card's call has no result yet and can still get one.
 *
 * The owner gate applies to a call made by a subagent (`ownerIsSubagent`):
 * with no run state available, the owner counts as not running; with a run
 * state entry for the owner, the owner must run and have an open run started
 * no later than the call (a null start or a null call time passes); with no
 * entry (e.g. a Claude workflow agent tab), the gate is skipped.
 */
export function pendingCall({
    count, callError, transcriptFrozen, callAt, treeCutoffMs,
    ownerIsSubagent, runStatesAvailable, ownerRunState, ownerRunning,
}) {
    if (count !== 0 || callError || transcriptFrozen) return false
    if (callBeforeCutoff(callAt, treeCutoffMs)) return false
    if (!ownerIsSubagent) return true
    if (!runStatesAvailable) return false
    if (!ownerRunState) return true
    if (!ownerRunning) return false
    const callMs = parseMs(callAt)
    return Object.values(ownerRunState.runs ?? {}).some((run) => {
        if (!run.open) return false
        const startedMs = parseMs(run.startedAt)
        return startedMs === null || callMs === null || startedMs <= callMs
    })
}

/**
 * Whether a spawn card holding its ack still waits for its run to show up in
 * the run state (the ack's `tool_state` can land before the agent link, and
 * the link before the run state).
 */
export function spawnAwaitingRun({
    isTask, count, hasOwnRunEntry, callError, runStatesAvailable,
    transcriptFrozen, callAt, treeCutoffMs, helperRunning,
}) {
    return !!isTask && count > 0 && !hasOwnRunEntry && !callError && !!runStatesAvailable
        && !transcriptFrozen && !callBeforeCutoff(callAt, treeCutoffMs) && !!helperRunning
}

/**
 * Whether a spawn card shows its "agent starting" spinner: no agent link yet,
 * the helper still running, and — with no result yet — a pending call, or —
 * with one — no error on the call.
 */
export function spawnPending({
    isTask, agentId, transcriptFrozen, callAt, treeCutoffMs, helperRunning, count, pendingCall, callError,
}) {
    if (transcriptFrozen || !isTask || agentId) return false
    if (callBeforeCutoff(callAt, treeCutoffMs) || !helperRunning) return false
    return count === 0 ? !!pendingCall : !callError
}

/**
 * The polling predicate of an agent card: a result no request has seen yet,
 * an open own run short of its expected rows (only once the call has a
 * result), a pending call, or a spawn waiting for its run.
 */
export function needsMoreRows({
    countChanged, count, ownRunOpen, rowCount, expectedCount, pendingCall, spawnAwaitingRun,
}) {
    return !!countChanged
        || (count > 0 && !!ownRunOpen && rowCount < expectedCount)
        || !!pendingCall
        || !!spawnAwaitingRun
}

/** The name a control card shows for its agent: its name, or `Agent "<short id>"`. */
export function controlCardAgentName(agentId, dataStore) {
    const { name, isFallback } = getAgentDisplay(agentId, dataStore)
    return isFallback ? `Agent "${name}"` : name
}
