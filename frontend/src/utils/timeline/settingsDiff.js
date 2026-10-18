// frontend/src/utils/timeline/settingsDiff.js
//
// Wording of a `settings.changed` row (display design section 6.2). Pure: the provider labels
// come in through an adapter, so the module never imports a provider helper (those pull in
// stores and the DOM).

import { ARROW } from './eventTime.js'
import { arrowPart, mergeTextParts, textPart, valuePart } from './parts.js'

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

/** One change: `[field name, old value, new value]`. */
function formatChange(field, [oldValue, newValue], labels) {
    const name = labels && KNOWN_FIELDS.has(field) ? labels.field(field) : field
    return [name, formatValue(field, oldValue, labels), formatValue(field, newValue, labels)]
}

const asText = ([name, before, after]) => `${name}: ${before}${ARROW}${after}`

/**
 * @param {*} changes - `data.changes`: `{field: [old, new]}`.
 * @param {string} reason - The row's `reason` column.
 * @param {SettingsLabels|null} labels
 * @returns {{text: string, full: string, parts: Array<Object>, severity: 'neutral'|'warning'}} `text` and `full` are
 *   the plain-text forms (tooltip); `parts` is what the row shows (parts.js), the arrow as its own part.
 */
export function describeSettingsChange(changes, reason, labels) {
    const entries = changes && typeof changes === 'object' && !Array.isArray(changes)
        ? Object.entries(changes).filter(([, pair]) => Array.isArray(pair) && pair.length === 2)
        : []
    if (!entries.length) {
        return { text: 'Settings changed', full: 'Settings changed', parts: [textPart('Settings changed')], severity: 'neutral' }
    }
    const prefix = Object.hasOwn(REASON_PREFIXES, reason) ? REASON_PREFIXES[reason] : ''
    const lines = entries.map(([field, pair]) => formatChange(field, pair, labels))
    const shownLines = lines.slice(0, MAX_SHOWN_CHANGES)
    const hidden = lines.length - MAX_SHOWN_CHANGES
    const more = hidden > 0 ? `, +${hidden} more` : ''
    const parts = [textPart(prefix)]
    // The field name is prose; the old and the new value are values, like the titles of a rename.
    shownLines.forEach(([name, before, after], index) => {
        parts.push(textPart(`${index ? ', ' : ''}${name}: `), valuePart(before), arrowPart(), valuePart(after))
    })
    parts.push(textPart(more))
    const permission = changes.permission_mode
    const opens = Array.isArray(permission) && OPEN_PERMISSION_MODES.has(permission[1])
    return {
        text: `${prefix}${shownLines.map(asText).join(', ')}${more}`,
        full: `${prefix}${lines.map(asText).join(', ')}`,
        parts: mergeTextParts(parts).filter((part) => part.type !== 'text' || part.text !== ''),
        severity: opens ? 'warning' : 'neutral',
    }
}
