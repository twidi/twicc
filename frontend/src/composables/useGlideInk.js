// Gliding indicators (visual refresh step 4c): the Vue side of utils/glideInk.js.
// Design: docs/plans/2026-09-28-gliding-indicators-design.md §4.4.
//
// Creates the controller when the container element appears (lists rendered under v-if
// included), recreates it when the element changes, and re-places the ink after Vue has
// patched the DOM for a change of `sources`. No onMounted: it runs in any effect scope.

import { onScopeDispose, watch } from 'vue'
import { createGlideInk } from '../utils/glideInk.js'

export function useGlideInk({
    container,       // Ref<Element | null>
    target,          // Ref<Element | null>, defaults to container
    flushTarget,     // Ref<Element | null> (the ink element for lists)
    flushPseudo = null,
    getActive,       // () => Element | null
    getActiveOffset, // (active) => { x, y }, optional (px subtracted from the measure)
    getItems,        // () => Element[], optional
    sources = [],    // watch sources that move the active item
    resetKey,        // () => any, optional
    env,             // optional (tests)
}) {
    let controller = null

    function stop() {
        controller?.destroy()
        controller = null
    }

    watch(container, (element) => {
        stop()
        if (!element) return
        controller = createGlideInk({
            container: element,
            target: target?.value ?? element,
            flushTarget: flushTarget?.value ?? undefined,
            flushPseudo,
            getActive,
            getActiveOffset,
            getItems,
            getResetKey: resetKey,
            env,
        })
    }, { immediate: true, flush: 'post' })

    // Post flush: the new active element exists and carries its classes.
    watch(sources, () => controller?.update(), { flush: 'post' })

    onScopeDispose(stop)

    return { update: () => controller?.update() }
}
