import { i18n } from '../i18n/index.js'
import { formatClock } from './timeline/eventTime.js'

// frontend/src/utils/backgroundWork.js
//
// Wording and decisions for the work an agent leaves running after its turn
// (`processState.background_work_in_progress`, `processState.active_crons`).
//
// In USER_TURN the turn is over — the user can send — yet a background shell
// may still run, and a Claude Code cron may still
// be scheduled. These helpers keep that state visible without presenting it as
// work in progress: the process indicators, the tooltips, the bottom status
// line of a session, the stop confirmations. Pure (no store, no Vue) so the
// node:test suite covers them.

import { PROCESS_STATE, PROCESS_STATE_NAMES } from '../constants.js'

/**
 * "1 background shell" / "2 background shells".
 *
 * @param {number} count
 * @param {string} singular
 * @param {string} [pluralForm] - Defaults to `singular` + "s".
 * @returns {string}
 */
export function pluralize(count, singular, pluralForm = `${singular}s`) {
    const key = { 'background shell': 'session.background.shellCount', 'active cron': 'session.background.cronCount', 'subagent': 'session.background.subagentCount', 'shell': 'session.background.plainShellCount', 'monitor': 'session.background.monitorCount', 'workflow': 'session.background.workflowCount' }[singular]
    if (key) return i18n.global.t(key, { count }, count)
    return `${count} ${count === 1 ? singular : pluralForm}`
}

/**
 * Number of background shells still running behind the agent, whatever its
 * state. 0 when nothing runs (or the process is unknown).
 *
 * @param {Object|null} processState
 * @returns {number}
 */
export function backgroundShellCount(processState) {
    const shells = processState?.background_work_in_progress?.shells
    return Number.isFinite(shells) && shells > 0 ? shells : 0
}

/**
 * Number of Workflow runs still live behind the agent (Claude Code only). 0
 * when none runs (or the process is unknown). Read from the process state, not
 * from the persisted run rows: those outlive a dead process, still "running".
 *
 * @param {Object|null} processState
 * @returns {number}
 */
export function runningWorkflowCount(processState) {
    const workflows = processState?.background_work_in_progress?.workflows
    return Number.isFinite(workflows) && workflows > 0 ? workflows : 0
}

/**
 * Number of background shells that keep a USER_TURN session from being plain
 * idle. Outside USER_TURN the agent is working (or starting) anyway, so the
 * idle-state variants (terminal icon, bottom line) never apply: 0.
 *
 * @param {Object|null} processState
 * @returns {number}
 */
export function userTurnBackgroundShellCount(processState) {
    return processState?.state === PROCESS_STATE.USER_TURN ? backgroundShellCount(processState) : 0
}

/**
 * Kind of background work shown behind an unread session: a background shell,
 * else an active cron, else nothing. Only USER_TURN qualifies. A shell wins
 * over a cron.
 *
 * @param {Object|null} [processState]
 * @returns {'shell'|'cron'|null}
 */
export function unreadBackgroundKind(processState) {
    if (userTurnBackgroundShellCount(processState) > 0) return 'shell'
    if (processState?.state === PROCESS_STATE.USER_TURN && activeCronCount(processState) > 0) return 'cron'
    return null
}

/**
 * Number of active cron jobs (Claude Code only; other providers never carry any).
 *
 * @param {Object|null} processState
 * @returns {number}
 */
export function activeCronCount(processState) {
    return processState?.active_crons?.length || 0
}

/**
 * "1 background shell running" / "2 background shells running".
 *
 * @param {number} shellCount
 * @returns {string}
 */
export function backgroundShellsRunningPhrase(shellCount) {
    return i18n.global.t('session.background.shellsRunning', { count: shellCount }, shellCount)
}

/**
 * Tooltip of a session's process indicator:
 * "Claude state: User turn — 1 background shell running (2 active crons)".
 *
 * @param {string} providerLabel
 * @param {Object} processState
 * @returns {string}
 */
export function processStateTooltip(providerLabel, processState) {
    const state = processState?.state
    let text = i18n.global.t('session.background.state', { provider: providerLabel, state: PROCESS_STATE_NAMES[state] ?? state })
    const shells = userTurnBackgroundShellCount(processState)
    if (shells) text = i18n.global.t('session.background.stateWithShells', { state: text, shells: backgroundShellsRunningPhrase(shells) })
    const crons = activeCronCount(processState)
    if (crons) text = i18n.global.t('session.background.stateWithCrons', { state: text, crons: pluralize(crons, 'active cron') })
    return text
}

/**
 * Parenthesised suffix of an aggregated indicator tooltip (project, workspace):
 * " (1 background shell, 2 active crons)", or "" when there is neither.
 *
 * @param {Object} counts
 * @param {number} counts.shells - Background shells of USER_TURN sessions.
 * @param {number} counts.crons - Active crons.
 * @returns {string}
 */
export function aggregatedBackgroundSuffix({ shells = 0, crons = 0 } = {}) {
    const parts = []
    if (shells > 0) parts.push(pluralize(shells, 'background shell'))
    if (crons > 0) parts.push(pluralize(crons, 'active cron'))
    return parts.length ? i18n.global.t('session.background.aggregateSuffix', { items: new Intl.ListFormat(i18n.global.locale.value, { style: 'short', type: 'unit' }).format(parts) }) : ''
}

/**
 * Orchestration panel: the idle (USER_TURN) sessions that still run background
 * shells, appended to the activity breakdown — "2 working · 1 idle, 1 with
 * background shells". "" when there is none.
 *
 * @param {number} sessionCount - USER_TURN sessions with at least one shell.
 * @returns {string}
 */
export function idleWithShellsSuffix(sessionCount) {
    return sessionCount > 0 ? i18n.global.t('session.background.idleShellsSuffix', { count: sessionCount }) : ''
}

/**
 * Lines of the static status shown at the bottom of a USER_TURN session:
 *   - `{kind: 'shells', text: '1 background shell still running'}`
 *   - `{kind: 'crons', text: '2 active crons — next run at 14:05'}` (the
 *     earliest `next_fire` still in the future; the count alone when none is)
 * Empty outside USER_TURN, or when nothing runs in the background.
 *
 * @param {Object|null} processState
 * @param {number} nowSeconds - Current epoch time in seconds.
 * @returns {Array<{kind: string, text: string}>}
 */
export function buildBackgroundWorkStatusLines(processState, nowSeconds) {
    if (processState?.state !== PROCESS_STATE.USER_TURN) return []
    const lines = []
    const shells = backgroundShellCount(processState)
    if (shells) {
        lines.push({ kind: 'shells', textKey: 'session.background.shellsStillRunning', textParams: { count: shells }, textCount: shells, text: i18n.global.t('session.background.shellsStillRunning', { count: shells }, shells) })
    }
    const crons = activeCronCount(processState)
    if (crons) {
        let nextFire = null
        for (const cron of processState.active_crons) {
            const fire = cron?.next_fire
            if (Number.isFinite(fire) && fire > nowSeconds && (nextFire === null || fire < nextFire)) {
                nextFire = fire
            }
        }
        const label = pluralize(crons, 'active cron')
        lines.push({
            kind: 'crons',
            textKey: nextFire === null ? 'session.background.cronCount' : 'session.background.cronNextRun',
            textParams: { count: crons, time: nextFire === null ? '' : formatClock(nextFire * 1000) },
            textCount: crons,
            text: nextFire === null ? label : i18n.global.t('session.background.cronNextRun', { count: crons, time: formatClock(nextFire * 1000) }, crons),
        })
    }
    return lines
}

/**
 * Signature of the bottom status line, or null when there is none. Compared
 * before/after a process-state change to know whether the session's visual
 * items need a recompute; also carried on the visual item so the stabilizer
 * re-renders it when its text changes.
 *
 * @param {Object|null} processState
 * @param {number} nowSeconds
 * @returns {string|null}
 */
export function backgroundWorkStatusKey(processState, nowSeconds) {
    const lines = buildBackgroundWorkStatusLines(processState, nowSeconds)
    return lines.length ? JSON.stringify(lines) : null
}

/**
 * What stopping a live process would destroy, when it deserves a confirmation:
 * active crons (never restored on restart) and background shells (killed with
 * the process, whatever its state). Null when there is neither. The caller
 * checks first that the process is stoppable.
 *
 * @param {Object} processState
 * @returns {{cronCount: number, shellCount: number}|null}
 */
export function getStopConfirmation(processState) {
    const cronCount = activeCronCount(processState)
    const shellCount = backgroundShellCount(processState)
    if (!cronCount && !shellCount) return null
    return { cronCount, shellCount }
}

/**
 * Title of the stop confirmation dialog.
 *
 * @param {{cronCount: number, shellCount: number}} counts
 * @returns {string}
 */
export function stopConfirmationTitle({ cronCount = 0, shellCount = 0 } = {}) {
    if (cronCount && shellCount) return i18n.global.t('session.background.lost')
    if (shellCount) return i18n.global.t('session.background.shellsKilled')
    return i18n.global.t('session.background.cronsLost')
}

/**
 * "Stopping will kill 1 background shell."
 *
 * @param {number} shellCount
 * @returns {string}
 */
export function backgroundShellsKillSentence(shellCount) {
    return i18n.global.t('session.background.kill', { count: shellCount }, shellCount)
}

/**
 * Body of the batch stop/archive confirmation (session list multi-select).
 *
 * @param {Object} counts
 * @param {string} counts.mode - 'archive' | 'stop'
 * @param {number} counts.processCount
 * @param {number} counts.cronCount
 * @param {number} counts.shellCount
 * @returns {string}
 */
export function batchStopConfirmationMessage({ mode, processCount = 0, cronCount = 0, shellCount = 0 }) {
    const parts = []
    if (mode === 'archive' && processCount > 0) {
        parts.push(i18n.global.t('session.background.stopProcesses', { count: processCount }, processCount))
    }
    if (shellCount > 0) {
        parts.push(i18n.global.t('session.background.killShells', { count: shellCount }, shellCount))
    }
    if (cronCount > 0) {
        parts.push(i18n.global.t('session.background.cancelCrons', { count: cronCount }, cronCount))
    }
    return parts.join(' ')
}

/**
 * Archive label/tooltip for a session whose process runs:
 * "Archive session (it will stop the Claude process and kill 1 background shell)".
 *
 * @param {string} prefix - "Archive session", "Archive", …
 * @param {string} providerLabel
 * @param {number} shellCount - Background shells, whatever the state.
 * @returns {string}
 */
export function archiveStopLabel(prefix, providerLabel, shellCount) {
    return shellCount > 0
        ? i18n.global.t('session.background.archiveKill', { action: prefix, provider: providerLabel, count: shellCount }, shellCount)
        : i18n.global.t('session.background.archiveStop', { action: prefix, provider: providerLabel })
}

/**
 * Agent-settings popover text for a startup-settings change made in USER_TURN
 * while background shells run (Claude Code): the backend defers the restart,
 * which would kill them, until the last shell ends or the process is stopped.
 *
 * @param {Object} context
 * @param {string} context.label - Provider label.
 * @param {number} context.shellCount
 * @param {boolean} context.hasMessageText
 * @returns {string}
 */
export function startupSettingsDeferredText({ label, shellCount, hasMessageText, working = false }) {
    const key = `session.background.deferred${working ? 'Working' : ''}${hasMessageText ? 'Sending' : ''}`
    return i18n.global.t(key, { label, count: shellCount }, shellCount)
}
