// frontend/src/utils/timeline/authorLabel.js
//
// The "by ..." label that follows a row's wording (display design section 6.3). Chosen from `actor`
// and `via` TOGETHER, never from a NULL `via` alone: NULL means "unknown" (old rows) or "automatic".

import { truncateText } from './eventTime.js'
import { sessionMention } from './sessions.js'

/** Kinds whose wording already says it was automatic. */
const SELF_EXPLAINING = /^(backend\.|session\.created$)/

const VIA_SUFFIX = { cli: '(CLI)', mcp_internal: '(MCP)', rpc: '(API)' }

const NO_LABEL = Object.freeze({ kind: 'none', parts: [], unknown: false })
/** No "by ..." text: the row shows an "automatic" tag (describeEvent.js) unless its wording already says it. */
const AUTOMATIC = Object.freeze({ kind: 'automatic', parts: [], unknown: false })

function text(value) {
    return { type: 'text', text: value }
}

/**
 * @typedef {Object} AuthorLabel
 * @property {'none'|'text'|'session'|'automatic'} kind - `session` when the label names a session; `automatic`
 *   when the row is automatic: no parts, the row shows a tag instead.
 * @property {Array<{type: 'text', text: string}|{type: 'session', mention: Object}>} parts
 * @property {boolean} unknown - An actor/via combination the table does not know: no label, and the
 *   tooltip must show the raw values.
 */

/**
 * @param {Object} event
 * @param {Object} context
 * @param {Object<string, Object>} context.sessions - The side table.
 * @param {(id: string) => boolean} [context.isSubagent]
 * @returns {AuthorLabel}
 */
export function describeAuthor(event, { sessions, isSubagent }) {
    // A `lost` row is dated by estimation and a creation row read from the session file is `derived`; both
    // have the `system` actor: no label on any row whose precision is not exact. An unknown precision counts as exact.
    if (event.precision === 'estimated' || event.precision === 'derived') return NO_LABEL
    const { actor, via } = event
    const withVia = (suffix) => (suffix ? ` ${suffix}` : '')
    if (actor === 'user') {
        if (via === 'ui' || via === null || via === undefined) return NO_LABEL
        if (typeof via === 'string' && Object.hasOwn(VIA_SUFFIX, via)) return { kind: 'text', parts: [text(`by you${withVia(VIA_SUFFIX[via])}`)], unknown: false }
    } else if (actor === 'agent') {
        if (via === null || via === undefined) return NO_LABEL
        if (via === 'cli' || via === 'mcp_internal') {
            const suffix = withVia(VIA_SUFFIX[via])
            if (!event.actor_ref) return { kind: 'text', parts: [text(`by an agent${suffix}`)], unknown: false }
            const mention = sessionMention(event.actor_ref, sessions, { isSubagent })
            return { kind: 'session', parts: [text('by '), { type: 'session', mention }, text(suffix)], unknown: false }
        }
    } else if (actor === 'external') {
        if (via === 'mcp_external') {
            const name = event.actor_name ? truncateText(event.actor_name, 80).text : null
            const label = name ? `by ${name} (external MCP)` : 'by an external MCP client'
            return { kind: 'text', parts: [text(label)], unknown: false }
        }
    } else if (actor === 'system') {
        if (via === null || via === undefined) {
            if (SELF_EXPLAINING.test(event.kind)) return NO_LABEL
            return AUTOMATIC
        }
    }
    return { kind: 'none', parts: [], unknown: true }
}

/**
 * The key of a row's author, the same for two rows whose "by ..." label names the same author: null when the row
 * shows no label. Consecutive rows of one key form an author run (display: the label once, under the run's last row).
 *
 * @param {Object} event
 * @returns {string|null}
 */
export function authorRunKey(event) {
    const label = describeAuthor(event, { sessions: {} })
    if (!label.parts.length) return null
    return [event.actor, event.via, event.actor_ref, event.actor_name].map((value) => value ?? '').join('\u0000')
}

/**
 * The place of a row in its author run, from the keys of its neighbors in the same block (null past the block's
 * edges): null for a row without label, `single` for a run of one row, else `first`, `middle` or `last`.
 *
 * @param {string|null} previousKey
 * @param {string|null} key
 * @param {string|null} nextKey
 * @returns {null|'single'|'first'|'middle'|'last'}
 */
export function authorRunPosition(previousKey, key, nextKey) {
    if (key === null) return null
    const withPrevious = previousKey === key
    const withNext = nextKey === key
    if (withPrevious && withNext) return 'middle'
    if (withPrevious) return 'last'
    if (withNext) return 'first'
    return 'single'
}
