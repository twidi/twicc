import { i18n } from '../../i18n/index.js'

// frontend/src/utils/timeline/describeEvent.js
//
// The view-model of one timeline event row: icon, severity, wording, tags, badge, author label, time and
// tooltip (display design sections 5.1, 6 and 14). Pure: a Vue component only shows what this returns.
//
// Every field of every kind has a fallback (design 6.1.3): a malformed or future payload never breaks a row.
// Every string that comes from data stays a plain string here; the components render it as TEXT, never as HTML.

import { describeAuthor } from './authorLabel.js'
import { describeBackgroundWork } from './backgroundBadge.js'
import { ESTIMATED_MARK, formatClock, formatFullTime, parseOccurredAt, splitFirstLine, truncateText } from './eventTime.js'
import { formatFullDateTime } from '../date.js'
import { arrowPart, messageParts, textPart, valuePart } from './parts.js'
import { describeSettingsChange } from './settingsDiff.js'
import { sessionMention } from './sessions.js'

const TITLE_MAX = 80
const TOOL_MAX = 40

/**
 * Wording of a `process.ended` reason: [text, severity, tag]. The tag is the cause when the text does not say it
 * (null otherwise). The reason set is open (see `describeProcessEnded`).
 */
export const PROCESS_ENDED_REASONS = {
    get manual() { return [i18n.global.t('session.timeline.processStopped'), 'neutral'] },
    get force() { return [i18n.global.t('session.timeline.processKilled'), 'neutral'] },
    get archived() { return [i18n.global.t('session.timeline.processStopped'), 'neutral', i18n.global.t('session.timeline.sessionArchived')] },
    get shutdown() { return [i18n.global.t('session.timeline.processStopped'), 'neutral', i18n.global.t('session.timeline.shutdown')] },
    get 'apply-settings'() { return [i18n.global.t('session.timeline.processSettings'), 'neutral'] },
    get 'switch-hybrid'() { return [i18n.global.t('session.timeline.processStopped'), 'neutral', i18n.global.t('session.timeline.switchHybrid')] },
    get timeout_user_turn() { return [i18n.global.t('session.timeline.processStopped'), 'neutral', i18n.global.t('session.timeline.idleTimeout')] },
    get 'cli-exit'() { return [i18n.global.t('session.timeline.cliExited'), 'neutral'] },
    get timeout_starting() { return [i18n.global.t('session.timeline.startTimeout'), 'warning'] },
    get timeout_assistant_turn() { return [i18n.global.t('session.timeline.noActivity'), 'warning'] },
    get cron_restart_timeout() { return [i18n.global.t('session.timeline.cronTimeout'), 'warning'] },
    get lost() { return [i18n.global.t('session.timeline.processLost'), 'warning', i18n.global.t('session.timeline.deadAtBoot')] },
    get auth_required() { return [i18n.global.t('session.timeline.loginRequired'), 'danger'] },
    get 'startup-failed'() { return [i18n.global.t('session.timeline.startFailed'), 'danger'] },
    get 'agent-start-failed'() { return [i18n.global.t('session.timeline.startFailed'), 'danger'] },
    get error() { return [i18n.global.t('session.timeline.crashed'), 'danger'] },
}

// The settings reasons whose wording already says the change was automatic (the prefixes of settingsDiff.js).
const AUTOMATIC_SETTINGS_REASONS = new Set(['model_retired', 'switch-hybrid'])

const PIN_SCOPES = { get project() { return i18n.global.t('session.timeline.scopeProject') }, get workspace() { return i18n.global.t('session.timeline.scopeWorkspace') }, get all() { return i18n.global.t('session.timeline.scopeAll') } }

const FLAG_KINDS = {
    get 'archived.started'() { return [i18n.global.t('session.timeline.archived'), 'box-archive'] },
    get 'archived.ended'() { return [i18n.global.t('session.timeline.unarchived'), 'box-open'] },
    get 'hidden.started'() { return [i18n.global.t('session.timeline.hidden'), 'eye-slash'] },
    get 'hidden.ended'() { return [i18n.global.t('session.timeline.unhidden'), 'eye'] },
    get 'muted.started'() { return [i18n.global.t('session.timeline.muted'), 'bell-slash'] },
    get 'muted.ended'() { return [i18n.global.t('session.timeline.unmuted'), 'bell'] },
    get 'tree_detached.started'() { return [i18n.global.t('session.timeline.detached'), 'sitemap'] },
    get 'tree_detached.ended'() { return [i18n.global.t('session.timeline.reattached'), 'sitemap'] },
}

const SESSION_CREATED_BASE = {
    get twicc() { return i18n.global.t('session.timeline.sessionCreated') },
    get external() { return i18n.global.t('session.timeline.sessionCreatedTerminal') },
    get sync() { return i18n.global.t('session.timeline.sessionCreated') },
}

/** The tag of an automatic row (author label `automatic`), and of an automatic title. */
const automaticTag = () => i18n.global.t('session.timeline.automatic')

// `agent`: the provider wrote the title in its own files (a /rename in the CLI, Codex naming its thread); the tag
// names the provider, see titleSourceTag.
const TITLE_SOURCE_TAGS = { get auto() { return automaticTag() } }

const text = textPart

/** `map[key]` for an own key only: a reason named "constructor" must not read the prototype. */
function lookup(map, key) {
    return typeof key === 'string' && Object.hasOwn(map, key) ? map[key] : undefined
}

function plain(value) {
    return { parts: [text(value)] }
}

function asString(value) {
    return typeof value === 'string' && value !== '' ? value : null
}

function toolName(data) {
    const name = asString(data.tool_name)
    return name ? truncateText(name, TOOL_MAX).text : null
}

/** The wording of a request, the tool name as a value (like a title) in parentheses: "Waiting for approval (Bash)". */
function withTool(label, tool) {
    return tool ? messageParts('session.timeline.withTool', { label: text(label), tool: valuePart(tool) }) : [text(label)]
}

function describeSessionCreated(event, data, context, author) {
    const base = lookup(SESSION_CREATED_BASE, data.origin) ?? i18n.global.t('session.timeline.sessionCreated')
    const parent = asString(data.spawned_by)
    // The parent appears once: an agent author label already names a session.
    if (!parent || author.kind === 'session') return { icon: 'plus', severity: 'neutral', parts: [text(base)] }
    const mention = sessionMention(parent, context.sessions, { isSubagent: context.isSubagent })
    const parts = data.origin !== 'external'
        ? messageParts('session.timeline.createdBy', { session: { type: 'session', mention } })
        : messageParts('session.timeline.createdTerminalBy', { session: { type: 'session', mention } })
    return { icon: 'plus', severity: 'neutral', parts }
}

function titleSourceTag(event, data, context) {
    if (data.source !== 'agent') return lookup(TITLE_SOURCE_TAGS, data.source) ?? null
    const provider = event.provider ?? context.sessions?.[event.session_id]?.provider ?? null
    const product = context.labelsFor?.(provider)?.product ?? null
    return product ? i18n.global.t('session.timeline.fromProduct', { product }) : i18n.global.t('session.timeline.fromProvider')
}

function describeTitleChanged(event, data, context) {
    const next = asString(data.new)
    const previous = asString(data.old)
    const tooltip = []
    // A title is a value, not prose: its own part (no quotes), cut near TITLE_MAX with the full text in the tooltip.
    const value = (title, label) => {
        const cut = truncateText(title, TITLE_MAX)
        if (cut.truncated) tooltip.push(`${label}: ${title}`)
        return valuePart(cut.text)
    }
    let parts
    if (next === null) parts = [text(i18n.global.t('session.timeline.titleCleared'))]
    else if (previous === null) parts = messageParts('session.timeline.titled', { title: value(next, i18n.global.t('session.timeline.newTitle')) })
    else parts = messageParts('session.timeline.renamed', { old: value(previous, i18n.global.t('session.timeline.oldTitle')), arrow: arrowPart(), new: value(next, i18n.global.t('session.timeline.newTitle')) })
    return {
        icon: 'pencil', severity: 'neutral', parts, tags: [titleSourceTag(event, data, context)], tooltip,
    }
}

function describeSettings(event, data, context) {
    const provider = event.provider ?? context.sessions?.[event.session_id]?.provider ?? null
    const change = describeSettingsChange(data.changes, event.reason, context.labelsFor?.(provider) ?? null)
    return {
        icon: 'sliders', severity: change.severity, parts: change.parts,
        tooltip: change.full !== change.text ? [change.full] : [],
        saysAutomatic: AUTOMATIC_SETTINGS_REASONS.has(event.reason),
    }
}

function describeProcessStarted(event, data) {
    const label = data.cause === 'resume' ? i18n.global.t('session.timeline.processResumed') : i18n.global.t('session.timeline.processStarted')
    const tooltip = Number.isFinite(data.twicc_pid) ? [i18n.global.t('session.timeline.processId', { pid: data.twicc_pid })] : []
    return { icon: 'play', severity: 'success', parts: [text(label)], tags: [data.hybrid === true ? i18n.global.t('session.timeline.hybridCli') : null], tooltip }
}

function describeProcessEnded(event, data, context) {
    const known = lookup(PROCESS_ENDED_REASONS, event.reason)
    const [label, severity, cause = null] = known ?? [i18n.global.t('session.timeline.processStopped'), 'neutral']
    const tooltip = Number.isFinite(data.twicc_pid) ? [i18n.global.t('session.timeline.processId', { pid: data.twicc_pid })] : []
    if (event.reason === 'lost' && asString(data.upper_bound)) {
        const bound = formatClock(parseOccurredAt(data.upper_bound), context.time)
        if (bound) tooltip.push(i18n.global.t('session.timeline.detectedBoot', { time: bound }))
    }
    const error = event.reason === 'error' ? asString(resolveUiMessage(remoteUiMessage(data, data.error))) : null
    return {
        icon: 'power-off', severity, parts: [text(label)], tags: [cause], tooltip,
        detail: error ? splitFirstLine(error) : null,
        // A known reason says who stopped it: an automatic end is exactly one that is not deliberate (manual, force,
        // archived; twicc/agent/timeline_hooks.py `_end_origin`). An unknown reason keeps the tag: it is the only hint.
        saysAutomatic: known !== undefined,
    }
}

/** The `reason` of an `awaiting_input.ended` row that TwiCC closed by denying the request after its delay. */
const AUTO_DENIED_REASON = 'auto_denied'

const AWAITING = {
    tool_approval: {
        started: (tool) => withTool(i18n.global.t('session.timeline.waitingApproval'), tool),
        ended: (tool, reason) => withTool(reason === AUTO_DENIED_REASON ? i18n.global.t('session.timeline.deniedAutomatically') : i18n.global.t('session.timeline.approvalHandled'), tool),
    },
    ask_user_question: { started: () => [text(i18n.global.t('session.timeline.question'))], ended: () => [text(i18n.global.t('session.timeline.answered'))] },
    hybrid_terminal: {
        started: (tool) => withTool(i18n.global.t('session.timeline.waitingTerminal'), tool),
        ended: () => [text(i18n.global.t('session.timeline.terminalHandled'))],
    },
}
const AWAITING_OTHER = { started: (tool) => withTool(i18n.global.t('session.timeline.waitingInput'), tool), ended: () => [text(i18n.global.t('session.timeline.inputHandled'))] }

/** The tag of an auto-denied request: "no answer in 2 min", from the delay stored with the event (null when absent). */
function noAnswerTag(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return null
    return seconds % 60 === 0 ? i18n.global.t('session.timeline.noAnswerMinutes', { count: seconds / 60 }) : i18n.global.t('session.timeline.noAnswerSeconds', { count: seconds })
}

function describeAwaiting(event, data, phase) {
    const wording = (lookup(AWAITING, data.request_type) ?? AWAITING_OTHER)[phase]
    const autoDenied = phase === 'ended' && event.reason === AUTO_DENIED_REASON
    return {
        icon: phase === 'started' ? 'hand' : autoDenied ? 'clock' : 'circle-check',
        severity: phase === 'started' || autoDenied ? 'warning' : 'neutral',
        parts: wording(toolName(data), event.reason),
        tags: [autoDenied ? noAnswerTag(data.auto_deny_after) : null],
        saysAutomatic: autoDenied,
    }
}

function describePinned(event, data, phase) {
    const scope = lookup(PIN_SCOPES, data.scope) ?? null
    if (phase === 'started') return { icon: 'thumbtack', severity: 'neutral', parts: [text(i18n.global.t('session.timeline.pinned'))], tags: [scope] }
    return { icon: 'thumbtack-slash', severity: 'neutral', parts: [text(i18n.global.t('session.timeline.unpinned'))], tags: [scope ? i18n.global.t('session.timeline.wasScope', { scope }) : null] }
}

const NAME_MAX = 60

/**
 * Wording of a `workflow.ended` status: [text, severity, icon]. The CLI's terminal statuses, plus the two TwiCC
 * infers (twicc/core/services/timeline_workflows.py): `paused` (resumed later with no end of its own) and
 * `interrupted` (its process ended; the row's `reason` says why). The set is open: an unknown status keeps its name.
 */
export const WORKFLOW_ENDED_STATUSES = {
    get completed() { return [i18n.global.t('session.timeline.workflowCompleted'), 'success', 'circle-check'] },
    get failed() { return [i18n.global.t('session.timeline.workflowFailed'), 'danger', 'circle-xmark'] },
    get killed() { return [i18n.global.t('session.timeline.workflowStopped'), 'neutral', 'circle-stop'] },
    get stopped() { return [i18n.global.t('session.timeline.workflowStopped'), 'neutral', 'circle-stop'] },
    get cancelled() { return [i18n.global.t('session.timeline.workflowCancelled'), 'neutral', 'circle-stop'] },
    get canceled() { return [i18n.global.t('session.timeline.workflowCancelled'), 'neutral', 'circle-stop'] },
    get paused() { return [i18n.global.t('session.timeline.workflowPaused'), 'neutral', 'circle-pause'] },
    get interrupted() { return [i18n.global.t('session.timeline.workflowInterrupted'), 'warning', 'circle-stop'] },
}

/** "Workflow started " + the run's name as a value (its script's meta.name), cut near NAME_MAX. */
function withWorkflowName(label, data, tooltip) {
    const name = asString(data.name)
    if (!name) return [text(label)]
    const cut = truncateText(name, NAME_MAX)
    if (cut.truncated) tooltip.push(i18n.global.t('session.timeline.workflowName', { name }))
    return [text(`${label} `), valuePart(cut.text)]
}

function workflowTooltip(data) {
    const lines = []
    if (asString(data.run_id)) lines.push(i18n.global.t('session.timeline.workflowRunId', { id: data.run_id }))
    if (asString(data.task_id)) lines.push(i18n.global.t('session.timeline.workflowTaskId', { id: data.task_id }))
    return lines
}

function describeWorkflowStarted(event, data) {
    const tooltip = workflowTooltip(data)
    const label = data.cause === 'resume' ? i18n.global.t('session.timeline.workflowResumed') : i18n.global.t('session.timeline.workflowStarted')
    return { icon: 'sitemap', severity: 'neutral', parts: withWorkflowName(label, data, tooltip), tooltip }
}

function describeWorkflowEnded(event, data) {
    const tooltip = workflowTooltip(data)
    const status = asString(data.status)
    const [label, severity, icon] = lookup(WORKFLOW_ENDED_STATUSES, status)
        ?? [status ? i18n.global.t('session.timeline.workflowEndedStatus', { status }) : i18n.global.t('session.timeline.workflowEnded'), 'neutral', 'sitemap']
    // An interruption names what ended the process, like `process.ended` does.
    let cause = null
    if (status === 'interrupted') {
        const [label, , tag] = lookup(PROCESS_ENDED_REASONS, event.reason) ?? []
        // German capitalizes nouns: its labels keep their case in the tag.
        const locale = i18n.global.locale.value
        const lower = (value) => (locale === 'de' ? value : value.toLocaleLowerCase(locale))
        cause = tag ?? lower(label ?? i18n.global.t('session.timeline.processStopped'))
    }
    // The run ends on its own: its wording says what happened, an "automatic" tag would add nothing.
    return { icon, severity, parts: withWorkflowName(label, data, tooltip), tags: [cause], tooltip, saysAutomatic: true }
}

function describeBackendStarted(event, data) {
    const version = asString(data.version)
    const label = data.machine_restarted === true ? i18n.global.t('session.timeline.backendMachineRestart') : i18n.global.t('session.timeline.backendStarted')
    return {
        icon: 'server', severity: 'neutral', parts: [text(label)], tags: [version ? `v${version}` : null],
        tooltip: backendTooltip(data),
    }
}

function describeBackendEnded(event, data) {
    const unclean = data.clean === false
    return {
        icon: 'power-off', severity: unclean ? 'danger' : 'neutral',
        parts: [text(unclean ? i18n.global.t('session.timeline.backendUnexpectedStop') : i18n.global.t('session.timeline.backendStopped'))], tooltip: backendTooltip(data),
    }
}

function backendTooltip(data) {
    const lines = []
    if (Number.isFinite(data.pid)) lines.push(i18n.global.t('session.timeline.processId', { pid: data.pid }))
    if (asString(data.version)) lines.push(i18n.global.t('session.timeline.version', { value: data.version }))
    if (asString(data.boot_id)) lines.push(i18n.global.t('session.timeline.bootId', { value: data.boot_id }))
    return lines
}

function describeKind(event, data, context, author) {
    switch (event.kind) {
        case 'session.created': return describeSessionCreated(event, data, context, author)
        case 'title.changed': return describeTitleChanged(event, data, context)
        case 'settings.changed': return describeSettings(event, data, context)
        case 'process.started': return describeProcessStarted(event, data)
        case 'process.ended': return describeProcessEnded(event, data, context)
        case 'user_message.sent': return { icon: 'comment', severity: 'neutral', ...plain(i18n.global.t('session.timeline.userMessage')) }
        case 'assistant_message.sent': return { icon: 'robot', severity: 'neutral', ...plain(i18n.global.t('session.timeline.assistantMessage')) }
        case 'turn.started': return { icon: 'robot', severity: 'neutral', ...plain(i18n.global.t('session.timeline.turnStarted')) }
        case 'turn.ended': {
            const badge = describeBackgroundWork(data.background_work)
            return { icon: 'circle-check', severity: 'success', parts: [text(i18n.global.t('session.timeline.turnEnded'))], badge }
        }
        case 'awaiting_input.started': return describeAwaiting(event, data, 'started')
        case 'awaiting_input.ended': return describeAwaiting(event, data, 'ended')
        case 'pinned.started': return describePinned(event, data, 'started')
        case 'pinned.ended': return describePinned(event, data, 'ended')
        case 'workflow.started': return describeWorkflowStarted(event, data)
        case 'workflow.ended': return describeWorkflowEnded(event, data)
        case 'backend.started': return describeBackendStarted(event, data)
        case 'backend.ended': return describeBackendEnded(event, data)
        default: break
    }
    const flag = lookup(FLAG_KINDS, event.kind)
    if (flag) return { icon: flag[1], severity: 'neutral', ...plain(flag[0]) }
    // A future kind on an older frontend: its raw name, a generic icon, no detail.
    return { icon: 'circle-info', severity: 'neutral', ...plain(String(event.kind)) }
}

function describeTime(event, context) {
    const ms = parseOccurredAt(event.occurred_at)
    // A `derived` time is the session's `created_at`, read from its file: it is shown like an exact one.
    const estimated = event.precision === 'estimated'
    const clock = formatClock(ms, context.time)
    const caption = estimated ? i18n.global.t('session.timeline.estimated') : null
    return {
        text: estimated ? `${ESTIMATED_MARK}${clock}` : clock,
        estimated,
        caption,
        full: formatFullTime(ms, context.time),
        // The tooltip of the time, in the format of the chat's message timestamps ("YYYY-MM-DD HH:MM:SS", local time).
        stamp: Number.isFinite(ms) ? formatFullDateTime(ms) : '',
    }
}

/**
 * @typedef {Object} EventViewModel
 * @property {number} id
 * @property {string} kind
 * @property {string} icon - Font Awesome Free solid name.
 * @property {'neutral'|'success'|'warning'|'danger'} severity
 * @property {Array<Object>} parts - Wording segments (parts.js): `text`, `value`, `arrow`, `session`.
 * @property {string[]} tags - Small tags at the end of the wording, in order: facts of the event ("v1.97.0",
 *   "TwiCC shutdown", "hybrid CLI", "project"), then its source ("automatic", "from Codex").
 * @property {{text: string, title: string}|null} badge - Background-work badge of `turn.ended`.
 * @property {{first: string, rest: string}|null} detail - Monospace first line of an error, with the rest behind an expand control.
 * @property {import('./authorLabel.js').AuthorLabel} author
 * @property {{text: string, estimated: boolean, caption: string|null, full: string}} time
 * @property {string} tooltip - Technical details, one per line.
 */

/**
 * @param {Object} event - One event of the API.
 * @param {Object} context
 * @param {Object<string, Object>} context.sessions - The side table.
 * @param {(provider: string|null) => (Object|null)} [context.labelsFor] - Settings labels of a provider.
 * @param {(id: string) => boolean} [context.isSubagent]
 * @param {{locale?: string, timeZone?: string}} [context.time] - Tests pin them.
 * @returns {EventViewModel}
 */
export function describeEvent(event, context) {
    const data = event.data && typeof event.data === 'object' && !Array.isArray(event.data) ? event.data : {}
    const author = describeAuthor(event, context)
    const described = describeKind(event, data, context, author)
    const time = describeTime(event, context)
    const lines = [time.full]
    if (event.precision) lines.push(i18n.global.t('session.timeline.precision', { value: event.precision }))
    if (time.caption) lines.push(time.caption)
    lines.push(...(described.tooltip ?? []))
    if (event.reason) lines.push(i18n.global.t('session.timeline.reason', { value: event.reason }))
    if (event.via) lines.push(i18n.global.t('session.timeline.via', { value: event.via }))
    if (event.actor_ref) lines.push(i18n.global.t('session.timeline.actorRef', { value: event.actor_ref }))
    if (event.actor_name) lines.push(i18n.global.t('session.timeline.actorName', { value: event.actor_name }))
    if (author.unknown) lines.push(i18n.global.t('session.timeline.actor', { value: event.actor }))
    // The source last: an automatic row says so with the tag of an automatic title, once, and only when its wording
    // does not already say it ("TwiCC shutdown", "Model upgraded (retired)").
    const tags = (described.tags ?? []).filter(Boolean)
    if (author.kind === 'automatic' && !described.saysAutomatic && !tags.includes(automaticTag())) tags.push(automaticTag())
    return {
        id: event.id,
        kind: event.kind,
        icon: described.icon,
        severity: described.severity,
        parts: described.parts,
        tags,
        badge: described.badge ?? null,
        detail: described.detail ?? null,
        author,
        time,
        tooltip: lines.join('\n'),
    }
}
import { remoteUiMessage } from '../../i18n/remoteMessages.js'
import { resolveUiMessage } from '../../i18n/uiMessages.js'
