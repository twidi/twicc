/**
 * Shared sidebar filter matching, used by the session list and the artifact
 * bookmark list.
 *
 * - `matchSubsequence`: case-insensitive subsequence (fuzzy) match — every
 *   character of the query appears in the text, in order, not necessarily
 *   consecutive.
 * - `matchQuery`: the dispatch used by sidebar filters. A query starting with
 *   `"` or `'` switches to case-insensitive literal-substring matching (an
 *   optional trailing matching quote is stripped, so both `"foo` and `"foo"`
 *   look for the substring `foo`); anything else uses `matchSubsequence`.
 *
 * The backend mirrors `matchQuery` in `match_text_query`
 * (`twicc/core/text_filter.py`) so the bulk-archive scope matches the sidebar
 * exactly. The peer inbox deliberately does NOT use this grammar: it searches
 * message bodies, where a subsequence match accepts nearly any short query. It
 * calls `match_all_terms_query` instead — every word must appear as a
 * substring, in any order.
 */

/**
 * Case-insensitive subsequence match.
 * @param {string} query - The search query
 * @param {string} text - The text to search in
 * @returns {boolean} True if query is a subsequence of text
 */
export function matchSubsequence(query, text) {
    const lowerQuery = query.toLowerCase()
    const lowerText = text.toLowerCase()

    let queryIndex = 0
    for (let i = 0; i < lowerText.length && queryIndex < lowerQuery.length; i++) {
        if (lowerText[i] === lowerQuery[queryIndex]) {
            queryIndex++
        }
    }
    return queryIndex === lowerQuery.length
}

/**
 * Resolve a sidebar filter query against a display string (see file header).
 * @param {string} query
 * @param {string} text
 * @returns {boolean}
 */
export function matchQuery(query, text) {
    const first = query[0]
    if (first === '"' || first === "'") {
        let needle = query.slice(1)
        if (needle.endsWith(first)) needle = needle.slice(0, -1)
        if (!needle) return true
        return text.toLowerCase().includes(needle.toLowerCase())
    }
    return matchSubsequence(query, text)
}
