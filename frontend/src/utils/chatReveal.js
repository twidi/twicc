// Chat reveal controller (visual refresh step 5a): is the chat hidden, and is the skeleton
// showing. No Vue, no DOM; time and timers are injected. The Vue side is
// composables/useChatReveal.js.
// Design: docs/plans/2026-09-28-chat-entrances-skeleton-design.md §7.2.
//
// The chat hides synchronously when work starts and shows again only in a later
// macrotask (the 0ms settle), so a busy input that flips false then true within one task
// never reveals it. A phase shorter than CHAT_SKELETON_DELAY_MS never shows the skeleton;
// a longer one shows it at least CHAT_SKELETON_MIN_VISIBLE_MS. `hidden` returns to false
// only through finish(), exactly once per phase.

export const CHAT_SKELETON_DELAY_MS = 300        // nothing shows before this
export const CHAT_SKELETON_MIN_VISIBLE_MS = 300  // once shown, it stays at least this long

/**
 * @param {{ now: () => number, setTimeout, clearTimeout, onChange: (state) => void }} deps
 * @returns {{ setBusy(busy: boolean), restartClock(), dispose(),
 *             state: { hidden: boolean, skeletonShown: boolean, startedAt: number|null,
 *                      phaseId: number } }}
 */
export function createChatReveal({ now, setTimeout, clearTimeout, onChange }) {
    const state = { hidden: false, skeletonShown: false, startedAt: null, phaseId: 0 }
    let busy = false
    // One slot per timer kind: starting a timer cancels the pending one of the same kind.
    const timers = { shown: null, settle: null, finish: null }

    function cancel(kind) {
        if (timers[kind] === null) return
        clearTimeout(timers[kind])
        timers[kind] = null
    }

    function start(kind, callback, ms) {
        cancel(kind)
        timers[kind] = setTimeout(() => {
            timers[kind] = null
            callback()
        }, ms)
    }

    const changed = () => onChange(state)

    function startShownTimer() {
        start('shown', () => {
            state.skeletonShown = true
            changed()
        }, CHAT_SKELETON_DELAY_MS)
    }

    function finish() {
        cancel('shown')
        state.hidden = false
        state.skeletonShown = false
        state.startedAt = null
        changed()
    }

    function settle() {
        if (busy) return
        if (!state.skeletonShown) {
            finish()
            return
        }
        start('finish', finish,
            Math.max(0, state.startedAt + CHAT_SKELETON_DELAY_MS + CHAT_SKELETON_MIN_VISIBLE_MS - now()))
    }

    function setBusy(value) {
        const next = !!value
        if (next === busy) return
        busy = next
        if (busy) {
            cancel('settle')
            cancel('finish')
            let didChange = !state.hidden
            state.hidden = true
            if (state.startedAt === null) {
                state.startedAt = now()
                state.phaseId += 1
                state.skeletonShown = false
                startShownTimer()
                didChange = true
            }
            if (didChange) changed()
            return
        }
        if (!state.hidden) return
        start('settle', settle, 0)
    }

    /** Restart the phase's clock: the skeleton's place was not visible until now. */
    function restartClock() {
        if (!state.hidden) return
        cancel('finish')
        state.startedAt = now()
        state.phaseId += 1
        state.skeletonShown = false
        startShownTimer()
        if (!busy) start('settle', settle, 0)
        changed()
    }

    function dispose() {
        cancel('shown')
        cancel('settle')
        cancel('finish')
    }

    return { setBusy, restartClock, dispose, state }
}
