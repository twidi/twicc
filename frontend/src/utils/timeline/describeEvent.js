// frontend/src/utils/timeline/describeEvent.js
//
// The view-model of one timeline event row: icon, severity, wording, tags, badge, author label, time and
// tooltip (display design sections 5.1, 6 and 14). Pure: a Vue component only shows what this returns.
//
// Every field of every kind has a fallback (design 6.1.3): a malformed or future payload never breaks a row.
// Every string that comes from data stays a plain string here; the components render it as TEXT, never as HTML.

import { describeAuthor } from './authorLabel.js'
import { describeBackgroundWork } from './backgroundBadge.js'
import { ARROW, formatClock, formatFullTime, parseOccurredAt, splitFirstLine, truncateText } from './eventTime.js'
import { describeSettingsChange } from './settingsDiff.js'
import { sessionMention } from './sessions.js'

const TITLE_MAX = 80
const TOOL_MAX = 40

/** Wording of a `process.ended` reason: [text, severity]. The reason set is open (see `describeProcessEnded`). */
export const PROCESS_ENDED_REASONS = {
    manual: ['Process stopped', 'neutral'],
    force: ['Process killed', 'neutral'],
    archived: ['Process stopped (session archived)', 'neutral'],
    shutdown: ['Process stopped (TwiCC shutdown)', 'neutral'],
    'apply-settings': ['Process restarted to apply settings', 'neutral'],
    'switch-hybrid': ['Process stopped (switch to hybrid)', 'neutral'],
    timeout_user_turn: ['Process stopped (idle timeout)', 'neutral'],
    'cli-exit': ['Hybrid CLI exited', 'neutral'],
    timeout_starting: ['Process stopped: start timed out', 'warning'],
    timeout_assistant_turn: ['Process stopped: no activity', 'warning'],
    cron_restart_timeout: ['Cron restart timed out', 'warning'],
    lost: ['Process lost (found dead at boot)', 'warning'],
    auth_required: ['Process stopped: login required', 'danger'],
    'startup-failed': ['Process failed to start', 'danger'],
    error: ['Process crashed', 'danger'],
}

const PIN_SCOPES = { project: 'project', workspace: 'workspace', all: 'all projects' }

const FLAG_KINDS = {
    'archived.started': ['Archived', 'box-archive'],
    'archived.ended': ['Unarchived', 'box-open'],
    'hidden.started': ['Hidden', 'eye-slash'],
    'hidden.ended': ['Unhidden', 'eye'],
    'muted.started': ['Muted', 'bell-slash'],
    'muted.ended': ['Unmuted', 'bell'],
}

const SESSION_CREATED_BASE = {
    twicc: 'Session created',
    external: 'Session created from a terminal',
    sync: 'Session imported',
}

const TITLE_SOURCE_TAGS = { auto: 'automatic', agent: 'by the agent' }

function text(value) {
    return { type: 'text', text: value }
}

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

function withTool(label, tool) {
    return tool ? `${label} (${tool})` : label
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

function describeTitleChanged(event, data) {
    const next = asString(data.new)
    const previous = asString(data.old)
    const tooltip = []
    const quote = (value, label) => {
        const cut = truncateText(value, TITLE_MAX)
        if (cut.truncated) tooltip.push(`${label}: ${value}`)
        return `"${cut.text}"`
    }
    let wording
    if (next === null) wording = 'Title cleared'
    else if (previous === null) wording = `Titled ${quote(next, 'New title')}`
    else wording = `Renamed ${quote(previous, 'Old title')}${ARROW}${quote(next, 'New title')}`
    return {
        icon: 'pencil', severity: 'neutral', parts: [text(wording)], tag: lookup(TITLE_SOURCE_TAGS, data.source) ?? null, tooltip,
    }
}

function describeSettings(event, data, context) {
    const provider = event.provider ?? context.sessions?.[event.session_id]?.provider ?? null
    const change = describeSettingsChange(data.changes, event.reason, context.labelsFor?.(provider) ?? null)
    return {
        icon: 'sliders', severity: change.severity, parts: [text(change.text)],
        tooltip: change.full !== change.text ? [change.full] : [],
    }
}

function describeProcessStarted(event, data) {
    const label = data.cause === 'resume' ? 'Process resumed' : 'Process started'
    const tooltip = Number.isFinite(data.twicc_pid) ? [`Process id: ${data.twicc_pid}`] : []
    return { icon: 'play', severity: 'success', parts: [text(data.hybrid === true ? `${label} (hybrid CLI)` : label)], tooltip }
}

function describeProcessEnded(event, data, context) {
    const known = lookup(PROCESS_ENDED_REASONS, event.reason)
    const [label, severity] = known ?? ['Process stopped', 'neutral']
    const tooltip = Number.isFinite(data.twicc_pid) ? [`Process id: ${data.twicc_pid}`] : []
    if (event.reason === 'lost' && asString(data.upper_bound)) {
        const bound = formatClock(parseOccurredAt(data.upper_bound), context.time)
        if (bound) tooltip.push(`Detected at boot ${bound}`)
    }
    const error = event.reason === 'error' ? asString(data.error) : null
    return {
        icon: 'power-off', severity, parts: [text(label)], tooltip,
        detail: error ? splitFirstLine(error) : null,
    }
}

const AWAITING = {
    tool_approval: {
        started: (tool) => withTool('Waiting for approval', tool),
        ended: (tool) => withTool('Approval handled', tool),
    },
    ask_user_question: { started: () => 'Question for you', ended: () => 'Question answered' },
    hybrid_terminal: {
        started: (tool) => withTool('Waiting in the terminal', tool),
        ended: () => 'Terminal input handled',
    },
}
const AWAITING_OTHER = { started: (tool) => withTool('Waiting for input', tool), ended: () => 'Input handled' }

function describeAwaiting(event, data, phase) {
    const wording = (lookup(AWAITING, data.request_type) ?? AWAITING_OTHER)[phase]
    return {
        icon: phase === 'started' ? 'hand' : 'circle-check',
        severity: phase === 'started' ? 'warning' : 'neutral',
        parts: [text(wording(toolName(data)))],
    }
}

function describePinned(event, data, phase) {
    const scope = lookup(PIN_SCOPES, data.scope) ?? null
    if (phase === 'started') {
        return { icon: 'thumbtack', severity: 'neutral', parts: [text(scope ? `Pinned (${scope})` : 'Pinned')] }
    }
    return { icon: 'thumbtack-slash', severity: 'neutral', parts: [text(scope ? `Unpinned (was ${scope})` : 'Unpinned')] }
}

function describeBackendStarted(event, data) {
    const version = asString(data.version)
    let label = version ? `TwiCC started v${version}` : 'TwiCC started'
    if (data.machine_restarted === true) label += ' after a machine restart'
    return { icon: 'server', severity: 'neutral', parts: [text(label)], tooltip: backendTooltip(data) }
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
        case 'title.changed': return describeTitleChanged(event, data)
        case 'settings.changed': return describeSettings(event, data, context)
        case 'process.started': return describeProcessStarted(event, data)
        case 'process.ended': return describeProcessEnded(event, data, context)
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
    const estimated = event.precision === 'estimated' || event.precision === 'derived'
    const clock = formatClock(ms, context.time)
    const caption = event.precision === 'estimated'
        ? 'Estimated: it happened at or before this time'
        : event.precision === 'derived' ? "Derived from the session's creation time" : null
    return {
        text: estimated ? `~${clock}` : clock,
        estimated,
        caption,
        full: formatFullTime(ms, context.time),
    }
}

/**
 * @typedef {Object} EventViewModel
 * @property {number} id
 * @property {string} kind
 * @property {string} icon - Font Awesome Free solid name.
 * @property {'neutral'|'success'|'warning'|'danger'} severity
 * @property {Array<Object>} parts - Wording segments: `{type: 'text', text}` or `{type: 'session', mention}`.
 * @property {string|null} tag - Small muted tag after the wording ("automatic", "by the agent").
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
    return {
        id: event.id,
        kind: event.kind,
        icon: described.icon,
        severity: described.severity,
        parts: described.parts,
        tag: described.tag ?? null,
        badge: described.badge ?? null,
        detail: described.detail ?? null,
        author,
        time,
        tooltip: lines.join('\n'),
    }
}
