// frontend/src/utils/uploads/rules.js
// Pure rules over upload entries (spec §6.2–§6.4, §6.11): terminal states,
// the stalled rule, the per-origin aggregate, the pump selection, the entry
// lookup of `applyServerRecord` and its rule 7.

import { isClientIdOfTab } from './ids.js'

export const TERMINAL_STATES = Object.freeze(['completed', 'failed', 'cancelled'])

/** At most this many entries are `creating` or `sending`. */
export const SLOT_LIMIT = 3

/** A non-local `active` entry without a record for this long is stalled. */
export const STALLED_AFTER_MS = 180_000

/** Local states that hold a slot. */
const SLOT_STATES = ['creating', 'sending']

/**
 * @param {string|null|undefined} state - a server record state
 * @returns {boolean}
 */
export function isTerminal(state) {
    return TERMINAL_STATES.includes(state)
}

/**
 * Key of an origin in `statusByOrigin`: `"<panel>|<key>"`.
 *
 * @param {{panel: string, key: string}} origin
 * @returns {string}
 */
export function originKey(origin) {
    return `${origin.panel}|${origin.key}`
}

/**
 * True when the entry holds a slot (`creating` or `sending`).
 *
 * @param {object} entry
 * @returns {boolean}
 */
export function holdsSlot(entry) {
    return SLOT_STATES.includes(entry.localState)
}

/**
 * The stalled rule (§6.2). An entry is stalled when it is not local, its
 * server state is `active`, and either this tab created it (it lost the
 * `File`: a reload) or no record came for 180 s.
 *
 * @param {object} entry
 * @param {{tabId: string, now: number}} ctx
 * @returns {boolean}
 */
export function isStalled(entry, { tabId, now }) {
    if (entry.local) return false
    if (entry.server?.state !== 'active') return false
    if (isClientIdOfTab(entry.server.client_id, tabId)) return true
    return entry.lastSeenAt == null || now - entry.lastSeenAt > STALLED_AFTER_MS
}

/**
 * Bytes done of one entry: local progress while `sending`, else the server
 * offset.
 *
 * @param {object} entry
 * @returns {number}
 */
export function bytesDone(entry) {
    if (entry.localState === 'sending') return entry.sentBytes ?? 0
    return entry.server?.offset ?? 0
}

/**
 * The per-origin aggregate of the tab labels (§6.11):
 * `"<panel>|<key>" → { count, percent, allStalled }`.
 *
 * @param {Iterable<object>} entries
 * @param {{tabId: string, now: number}} ctx
 * @returns {Record<string, {count: number, percent: number, allStalled: boolean}>}
 */
export function computeStatusByOrigin(entries, ctx) {
    const groups = new Map()
    for (const entry of entries) {
        const key = originKey(entry.origin)
        let group = groups.get(key)
        if (!group) {
            group = { count: 0, done: 0, size: 0, allStalled: true }
            groups.set(key, group)
        }
        group.count += 1
        group.done += bytesDone(entry)
        group.size += entry.size
        if (!(entry.localState === 'paused' || isStalled(entry, ctx))) group.allStalled = false
    }
    const result = {}
    for (const [key, group] of groups) {
        result[key] = {
            count: group.count,
            percent: group.size === 0 ? 100 : Math.floor((100 * group.done) / group.size),
            allStalled: group.allStalled,
        }
    }
    return result
}

/**
 * The pump selection (§6.4): the next `queued` entry (oldest first) to leave
 * the queue, or null. Nothing while an entry is `paused` for `network`, or
 * while every slot is taken. The slot count is derived from the entries.
 *
 * @param {Iterable<object>} entries - in insertion (age) order
 * @returns {object|null}
 */
export function selectNextToPromote(entries) {
    let slots = 0
    let next = null
    for (const entry of entries) {
        if (entry.localState === 'paused' && entry.pauseReason === 'network') return null
        if (holdsSlot(entry)) slots += 1
        else if (entry.localState === 'queued' && !next) next = entry
    }
    return slots < SLOT_LIMIT ? next : null
}

/**
 * Rule 3 of `applyServerRecord` (§6.3): the entry of a record. By `clientId`
 * only if that entry has no server record yet or holds this same upload; else
 * by server id. A record is never merged into an entry holding another upload.
 *
 * @param {Iterable<object>} entries
 * @param {object} record
 * @returns {object|null}
 */
export function findEntryForRecord(entries, record) {
    let byId = null
    for (const entry of entries) {
        if (
            entry.clientId &&
            entry.clientId === record.client_id &&
            (!entry.server || entry.server.id === record.id)
        ) {
            return entry
        }
        if (!byId && entry.server?.id === record.id) byId = entry
    }
    return byId
}

/**
 * Rule 7 of `applyServerRecord` (§6.3): what a local entry in
 * `localState: null` does with an `active` record.
 *
 * @param {object} entry - after the record was stored
 * @param {object|null} previous - the record stored before it
 * @param {object} record
 * @returns {'pause-error'|'requeue'|null}
 */
export function rule7Action(entry, previous, record) {
    if (!entry.local || entry.localState !== null || record.state !== 'active') return null
    if (record.error) return 'pause-error'
    if (record.offset < record.size && previous?.state === 'finalizing') return 'requeue'
    return null
}
