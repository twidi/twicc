// frontend/src/utils/timeline/settingsDiff.js
//
// Wording of a `settings.changed` row (display design section 6.2). Pure: the provider labels
// come in through an adapter, so the module never imports a provider helper (those pull in
// stores and the DOM).

import { ARROW } from './eventTime.js'

/** The eight fields of the closed agent-settings bundle (the only keys a payload can carry). */
const KNOWN_FIELDS = new Set([
    'selected_model', 'permission_mode', 'effort', 'thinking_enabled', 'claude_in_chrome',
    'fast_mode', 'context_max', 'question_widget',
])

const REASON_PREFIXES = {
    model_retired: 'Model upgraded (retired): ',
    set_mode: 'Permission mode set: ',
    'switch-hybrid': 'Hybrid switch: ',
}

/** Permission modes that run without asking: the row is a warning. */
const OPEN_PERMISSION_MODES = new Set(['bypassPermissions', 'yolo'])

/** At most this many changes are written in the row; the tooltip lists them all. */
export const MAX_SHOWN_CHANGES = 3

/**
 * What the provider can say about a field. `null` (unknown provider) means raw values.
 *
 * @typedef {Object} SettingsLabels
 * @property {(field: string) => string} field
 * @property {(field: string, value: *) => (string|null)} choice
 * @property {(model: string) => string} model
 */

function formatValue(field, value, labels) {
    if (value === null || value === undefined) return 'default'
    if (typeof value === 'boolean') return value ? 'on' : 'off'
    if (!labels || !KNOWN_FIELDS.has(field)) return String(value)
    if (field === 'selected_model') return labels.model(value) || String(value)
    return labels.choice(field, value) ?? String(value)
}

function formatChange(field, [oldValue, newValue], labels) {
    const name = labels && KNOWN_FIELDS.has(field) ? labels.field(field) : field
    return `${name}: ${formatValue(field, oldValue, labels)}${ARROW}${formatValue(field, newValue, labels)}`
}

/**
 * @param {*} changes - `data.changes`: `{field: [old, new]}`.
 * @param {string} reason - The row's `reason` column.
 * @param {SettingsLabels|null} labels
 * @returns {{text: string, full: string, severity: 'neutral'|'warning'}}
 */
export function describeSettingsChange(changes, reason, labels) {
    const entries = changes && typeof changes === 'object' && !Array.isArray(changes)
        ? Object.entries(changes).filter(([, pair]) => Array.isArray(pair) && pair.length === 2)
        : []
    if (!entries.length) return { text: 'Settings changed', full: 'Settings changed', severity: 'neutral' }
    const prefix = Object.hasOwn(REASON_PREFIXES, reason) ? REASON_PREFIXES[reason] : ''
    const lines = entries.map(([field, pair]) => formatChange(field, pair, labels))
    const shown = lines.slice(0, MAX_SHOWN_CHANGES).join(', ')
    const hidden = lines.length - MAX_SHOWN_CHANGES
    const permission = changes.permission_mode
    const opens = Array.isArray(permission) && OPEN_PERMISSION_MODES.has(permission[1])
    return {
        text: `${prefix}${shown}${hidden > 0 ? `, +${hidden} more` : ''}`,
        full: `${prefix}${lines.join(', ')}`,
        severity: opens ? 'warning' : 'neutral',
    }
}
