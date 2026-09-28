// Chat reveal (visual refresh step 5a): the Vue side of utils/chatReveal.js.
// Design: docs/plans/2026-09-28-chat-entrances-skeleton-design.md §7.2.
//
// The busy source is watched with flush 'sync': the controller must see a busy input that
// flips false then true within one task as it happens, so its 0ms settle can ignore it.

import { onScopeDispose, readonly, ref, watch } from 'vue'
import { createChatReveal } from '../utils/chatReveal.js'

/**
 * @param {() => boolean} isBusy
 * @returns {{ hidden, skeletonShown, startedAt, phaseId, restartClock }} readonly refs
 *          mirroring the controller's state, and restartClock().
 */
export function useChatReveal(isBusy, env = globalThis) {
    const hidden = ref(false)
    const skeletonShown = ref(false)
    const startedAt = ref(null)
    const phaseId = ref(0)

    // Bound wrappers: a bare env.performance.now reference throws when called.
    const controller = createChatReveal({
        now: () => env.performance.now(),
        setTimeout: (fn, ms) => env.setTimeout(fn, ms),
        clearTimeout: (id) => env.clearTimeout(id),
        onChange: (state) => {
            hidden.value = state.hidden
            skeletonShown.value = state.skeletonShown
            startedAt.value = state.startedAt
            phaseId.value = state.phaseId
        },
    })

    controller.setBusy(isBusy())
    watch(isBusy, (busy) => controller.setBusy(busy), { flush: 'sync' })
    onScopeDispose(() => controller.dispose())

    return {
        hidden: readonly(hidden),
        skeletonShown: readonly(skeletonShown),
        startedAt: readonly(startedAt),
        phaseId: readonly(phaseId),
        restartClock: () => controller.restartClock(),
    }
}
