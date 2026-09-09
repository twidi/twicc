// frontend/src/utils/paletteDrillDown.js
//
// Query splitting for the command palette's drill-down search: one query
// filters two levels at once, without a separator. For a given parent
// command, the longest prefix of the query that fuzzy-matches the parent's
// label is the `head`; whatever follows (trimmed) is the `tail`, which the
// palette applies to the parent's sub-items.
//
// Properties the palette relies on:
//   - A child never surfaces on its own: the head must consume at least
//     MIN_HEAD_LENGTH characters, so a query that only really matches the
//     child (e.g. "twicc" hitting the "t" of "Go to Session…") is rejected.
//   - The boundary is monotone while typing: a prefix that stops matching
//     never matches again by growing, so the head is frozen from that point
//     and every further character lands in the tail. Results never flip
//     between interpretations.
//   - Segments are trimmed: "sess twicc" and "sesstwicc" split the same.

import { fuzzyMatch } from './fuzzyMatch.js'

export const MIN_HEAD_LENGTH = 2

/**
 * Split a query against one parent label.
 *
 * @param {string} query
 * @param {string} parentLabel
 * @returns {?{ head: string, tail: string, headMatch: { match: boolean, score: number, ranges: number[][] } }}
 *   `null` when no prefix of at least MIN_HEAD_LENGTH characters matches the
 *   parent. `tail` is `''` when the parent consumes the whole query.
 */
export function splitDrillDownQuery(query, parentLabel) {
    for (let k = query.length; k >= MIN_HEAD_LENGTH; k--) {
        const head = query.slice(0, k).trim()
        if (head.length < MIN_HEAD_LENGTH) continue
        const headMatch = fuzzyMatch(head, parentLabel)
        if (!headMatch.match) continue
        return { head, tail: query.slice(k).trim(), headMatch }
    }
    return null
}
