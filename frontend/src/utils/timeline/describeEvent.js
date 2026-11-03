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
import { arrowPart, textPart, valuePart } from './parts.js'
import { describeSettingsChange } from './settingsDiff.js'
import { sessionMention } from './sessions.js'

const TITLE_MAX = 80
const TOOL_MAX = 40

/**
 * Wording of a `process.ended` reason: [text, severity, tag]. The tag is the cause when the text does not say it
 * (null otherwise). The reason set is open (see `describeProcessEnded`).
 */
export const PROCESS_ENDED_REASONS = {
    manual: ['Process stopped', 'neutral'],
    force: ['Process killed', 'neutral'],
    archived: ['Process stopped', 'neutral', 'session archived'],
    shutdown: ['Process stopped', 'neutral', 'TwiCC shutdown'],
    'apply-settings': ['Process restarted to apply settings', 'neutral'],
    'switch-hybrid': ['Process stopped', 'neutral', 'switch to hybrid'],
    timeout_user_turn: ['Process stopped', 'neutral', 'idle timeout'],
    'cli-exit': ['Hybrid CLI exited', 'neutral'],
    timeout_starting: ['Process stopped: start timed out', 'warning'],
    timeout_assistant_turn: ['Process stopped: no activity', 'warning'],
    cron_restart_timeout: ['Cron restart timed out', 'warning'],
    lost: ['Process lost', 'warning', 'found dead at boot'],
    auth_required: ['Process stopped: login required', 'danger'],
    'startup-failed': ['Process failed to start', 'danger'],
    error: ['Process crashed', 'danger'],
}

// The settings reasons whose wording already says the change was automatic (the prefixes of settingsDiff.js).
const AUTOMATIC_SETTINGS_REASONS = new Set(['model_retired', 'switch-hybrid'])

const PIN_SCOPES = { project: 'project', workspace: 'workspace', all: 'all projects' }

const FLAG_KINDS = {
    'archived.started': ['Archived', 'box-archive'],
    'archived.ended': ['Unarchived', 'box-open'],
    'hidden.started': ['Hidden', 'eye-slash'],
    'hidden.ended': ['Unhidden', 'eye'],
    'muted.started': ['Muted', 'bell-slash'],
    'muted.ended': ['Unmuted', 'bell'],
    'tree_detached.started': ['Detached from parent', 'sitemap'],
    'tree_detached.ended': ['Reattached to parent', 'sitemap'],
}

const SESSION_CREATED_BASE = {
    twicc: 'Session created',
    external: 'Session created from a terminal',
    sync: 'Session created',
}

/** The tag of an automatic row (author label `automatic`), and of an automatic title. */
const AUTOMATIC_TAG = 'automatic'

// `agent`: the provider wrote the title in its own files (a /rename in the CLI, Codex naming its thread); the tag
// names the provider, see titleSourceTag.
const TITLE_SOURCE_TAGS = { auto: AUTOMATIC_TAG }

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
    return tool ? [text(`${label} (`), valuePart(tool), text(')')] : [text(label)]
}

function describeSessionCreated(event, data, context, author) {
    const base = lookup(SESSION_CREATED_BASE, data.origin) ?? 'Session created'
    const parent = asString(data.spawned_by)
    // The parent appears once: an agent author label already names a session.
    if (!parent || author.kind === 'session') return { icon: 'plus', severity: 'neutral', parts: [text(base)] }
    const mention = sessionMention(parent, context.sessions, { isSubagent: context.isSubagent })
    const parts = base === 'Session created'
        ? [text(`${base} by `), { type: 'session', mention }]
        : [text(`${base} (spawned by `), { type: 'session', mention }, text(')')]
    return { icon: 'plus', severity: 'neutral', parts }
}

function titleSourceTag(event, data, context) {
    if (data.source !== 'agent') return lookup(TITLE_SOURCE_TAGS, data.source) ?? null
    const provider = event.provider ?? context.sessions?.[event.session_id]?.provider ?? null
    const product = context.labelsFor?.(provider)?.product ?? null
    return product ? `from ${product}` : 'from the provider'
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
    if (next === null) parts = [text('Title cleared')]
    else if (previous === null) parts = [text('Titled '), value(next, 'New title')]
    else parts = [text('Renamed '), value(previous, 'Old title'), arrowPart(), value(next, 'New title')]
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
    const label = data.cause === 'resume' ? 'Process resumed' : 'Process started'
    const tooltip = Number.isFinite(data.twicc_pid) ? [`Process id: ${data.twicc_pid}`] : []
    return { icon: 'play', severity: 'success', parts: [text(label)], tags: [data.hybrid === true ? 'hybrid CLI' : null], tooltip }
}

function describeProcessEnded(event, data, context) {
    const known = lookup(PROCESS_ENDED_REASONS, event.reason)
    const [label, severity, cause = null] = known ?? ['Process stopped', 'neutral']
    const tooltip = Number.isFinite(data.twicc_pid) ? [`Process id: ${data.twicc_pid}`] : []
    if (event.reason === 'lost' && asString(data.upper_bound)) {
        const bound = formatClock(parseOccurredAt(data.upper_bound), context.time)
        if (bound) tooltip.push(`Detected at boot ${bound}`)
    }
    const error = event.reason === 'error' ? asString(data.error) : null
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
        started: (tool) => withTool('Waiting for approval', tool),
        ended: (tool, reason) => withTool(reason === AUTO_DENIED_REASON ? 'Approval denied automatically' : 'Approval handled', tool),
    },
    ask_user_question: { started: () => [text('Question for you')], ended: () => [text('Question answered')] },
    hybrid_terminal: {
        started: (tool) => withTool('Waiting in the terminal', tool),
        ended: () => [text('Terminal input handled')],
    },
}
const AWAITING_OTHER = { started: (tool) => withTool('Waiting for input', tool), ended: () => [text('Input handled')] }

/** The tag of an auto-denied request: "no answer in 2 min", from the delay stored with the event (null when absent). */
function noAnswerTag(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return null
    return seconds % 60 === 0 ? `no answer in ${seconds / 60} min` : `no answer in ${seconds} s`
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
    if (phase === 'started') return { icon: 'thumbtack', severity: 'neutral', parts: [text('Pinned')], tags: [scope] }
    return { icon: 'thumbtack-slash', severity: 'neutral', parts: [text('Unpinned')], tags: [scope ? `was ${scope}` : null] }
}

function describeBackendStarted(event, data) {
    const version = asString(data.version)
    const label = data.machine_restarted === true ? 'TwiCC started after a machine restart' : 'TwiCC started'
    return {
        icon: 'server', severity: 'neutral', parts: [text(label)], tags: [version ? `v${version}` : null],
        tooltip: backendTooltip(data),
    }
}

function describeBackendEnded(event, data) {
    const unclean = data.clean === false
    return {
        icon: 'power-off', severity: unclean ? 'danger' : 'neutral',
        parts: [text(unclean ? 'TwiCC stopped unexpectedly' : 'TwiCC stopped')], tooltip: backendTooltip(data),
    }
}

function backendTooltip(data) {
    const lines = []
    if (Number.isFinite(data.pid)) lines.push(`Process id: ${data.pid}`)
    if (asString(data.version)) lines.push(`Version: ${data.version}`)
    if (asString(data.boot_id)) lines.push(`Boot id: ${data.boot_id}`)
    return lines
}

function describeKind(event, data, context, author) {
    switch (event.kind) {
        case 'session.created': return describeSessionCreated(event, data, context, author)
        case 'title.changed': return describeTitleChanged(event, data, context)
        case 'settings.changed': return describeSettings(event, data, context)
        case 'process.started': return describeProcessStarted(event, data)
        case 'process.ended': return describeProcessEnded(event, data, context)
        case 'user_message.sent': return { icon: 'comment', severity: 'neutral', ...plain('User message') }
        case 'assistant_message.sent': return { icon: 'robot', severity: 'neutral', ...plain('Assistant final message') }
        case 'turn.started': return { icon: 'robot', severity: 'neutral', ...plain('Turn started') }
        case 'turn.ended': {
            const badge = describeBackgroundWork(data.background_work)
            return { icon: 'circle-check', severity: 'success', parts: [text('Turn ended')], badge }
        }
        case 'awaiting_input.started': return describeAwaiting(event, data, 'started')
        case 'awaiting_input.ended': return describeAwaiting(event, data, 'ended')
        case 'pinned.started': return describePinned(event, data, 'started')
        case 'pinned.ended': return describePinned(event, data, 'ended')
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
    const caption = estimated ? 'Estimated: it happened at or before this time' : null
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
    if (event.precision) lines.push(`Precision: ${event.precision}`)
    if (time.caption) lines.push(time.caption)
    lines.push(...(described.tooltip ?? []))
    if (event.reason) lines.push(`Reason: ${event.reason}`)
    if (event.via) lines.push(`Via: ${event.via}`)
    if (event.actor_ref) lines.push(`Actor ref: ${event.actor_ref}`)
    if (event.actor_name) lines.push(`Actor name: ${event.actor_name}`)
    if (author.unknown) lines.push(`Actor: ${event.actor}`)
    // The source last: an automatic row says so with the tag of an automatic title, once, and only when its wording
    // does not already say it ("TwiCC shutdown", "Model upgraded (retired)").
    const tags = (described.tags ?? []).filter(Boolean)
    if (author.kind === 'automatic' && !described.saysAutomatic && !tags.includes(AUTOMATIC_TAG)) tags.push(AUTOMATIC_TAG)
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
