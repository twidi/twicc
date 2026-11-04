// Open/closed state of the collapsible `<details>` blocks of rendered markdown.
//
// The state must outlive the DOM: the block a user toggled is re-rendered on
// every streaming frame, and when the stream ends the streaming block is
// replaced by the final message — a different MarkdownContent instance. So the
// state lives here, at module level, and is keyed by what the block *is*, not
// where it sits:
//
//   scope       hash of the markdown blocks before the one holding the details.
//               Those blocks are complete as soon as the details block starts,
//               and the final message repeats them verbatim, so the scope is
//               the same while streaming and after. It keeps two messages that
//               happen to use the same title apart.
//   title       the summary text.
//   occurrence  rank among the details of that block sharing the same title.
//
// Only a user toggle writes an entry; a block never toggled follows its source
// (`<details open>` or not).

import { hashString } from './hash.js'

const MAX_ENTRIES = 500

// Insertion-ordered: the oldest entry is evicted first. A re-set moves an entry
// to the end.
const openByKey = new Map()

/** Scope of each block: the hash of every block before it, in order. */
export function detailsScopes(blockHashes) {
    let scope = ''
    return blockHashes.map((hash) => {
        const current = scope
        scope = hashString(`${scope}\n${hash ?? ''}`)
        return current
    })
}

/** Key of one details element. */
export function detailsKey(scope, title, occurrence) {
    return JSON.stringify([scope, title, occurrence])
}

export function rememberDetailsOpen(key, open) {
    openByKey.delete(key)
    openByKey.set(key, open)
    if (openByKey.size > MAX_ENTRIES) openByKey.delete(openByKey.keys().next().value)
}

/** The remembered state, or undefined when the user never toggled it. */
export function recalledDetailsOpen(key) {
    return openByKey.get(key)
}

/** Test hook. */
export function resetDetailsStateForTests() {
    openByKey.clear()
}

const DETAILS_SELECTOR = 'wa-details.md-details'

function titleOf(details) {
    return details.querySelector(':scope > [slot="summary"]')?.textContent ?? ''
}

/**
 * Walk the details of one block in document order, handing each one its key.
 * `root` is the block's element.
 */
export function forEachDetails(root, scope, callback) {
    const seen = new Map()
    for (const details of root.querySelectorAll(DETAILS_SELECTOR)) {
        const title = titleOf(details)
        const occurrence = seen.get(title) ?? 0
        seen.set(title, occurrence + 1)
        callback(details, detailsKey(scope, title, occurrence))
    }
}

/** Impose the remembered state on the details of a detached block. */
export function applyRememberedDetailsIn(root, scope) {
    forEachDetails(root, scope, (details, key) => {
        const open = recalledDetailsOpen(key)
        if (open === undefined) return
        details.toggleAttribute('open', open)
    })
}

/**
 * Remember a toggle. `details` is the element that fired wa-show / wa-hide,
 * `block` the `.markdown-block` holding it (its `dataset.detailsScope` is the scope).
 */
export function rememberDetailsToggle(block, details, open) {
    forEachDetails(block, block.dataset.detailsScope ?? '', (candidate, key) => {
        if (candidate === details) rememberDetailsOpen(key, open)
    })
}
