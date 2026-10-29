// The chat views on screen, per session: the store defers the visual-items recompute of a
// session that a view showed once and that none shows now (stores/data.js
// recomputeVisualItems), and of every such session while the page is hidden. A view brings
// its session up to date when it shows again (registerVisualItemsView).
//
// A session that never had a view recomputes as before: the first rows of a list come from
// the store, and so do every test and path that builds rows without a view.
//
// Dev only: editing this module under HMR starts an empty registry. Its stale marks are
// lost, so a deferred session catches up at its next recompute, not when it shows again.

export function createVisualItemsViews({ document = globalThis.document ?? null } = {}) {
    const views = new Map()     // sessionId -> Set of views showing it
    const viewed = new Set()    // sessions a view has shown
    const stale = new Set()     // sessions whose last recompute was deferred
    let undeferred = null       // the session a flush recomputes now
    let onVisible = null
    let listening = false

    const hidden = () => document?.hidden === true
    function handleVisibilityChange() {
        if (!hidden()) onVisible?.()
    }

    return {
        /**
         * Count `view` as showing `sessionId`. `flushShown` runs when the page shows again,
         * to recompute the deferred sessions a view shows (one listener for the page).
         */
        register(sessionId, view, flushShown) {
            if (!views.has(sessionId)) views.set(sessionId, new Set())
            views.get(sessionId).add(view)
            viewed.add(sessionId)
            onVisible = flushShown
            if (!listening && document) {
                document.addEventListener('visibilitychange', handleVisibilityChange)
                listening = true
            }
        },
        unregister(sessionId, view) {
            const shown = views.get(sessionId)
            if (!shown) return
            shown.delete(view)
            if (!shown.size) views.delete(sessionId)
        },
        viewsOf(sessionId) {
            return [...(views.get(sessionId) ?? [])]
        },
        /** Whether a recompute of this session may wait until a view shows it. */
        defers(sessionId) {
            return sessionId !== undeferred && viewed.has(sessionId) && (hidden() || !views.has(sessionId))
        },
        markStale(sessionId) { stale.add(sessionId) },
        clearStale(sessionId) { stale.delete(sessionId) },
        isStale(sessionId) { return stale.has(sessionId) },
        /** The deferred sessions a view shows. */
        staleShown() {
            return [...stale].filter(sessionId => views.has(sessionId))
        },
        /** Run `fn` with the recompute of `sessionId` never deferred. */
        undeferred(sessionId, fn) {
            const previous = undeferred
            undeferred = sessionId
            try {
                return fn()
            } finally {
                undeferred = previous
            }
        },
    }
}

export const visualItemsViews = createVisualItemsViews()
