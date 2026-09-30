// Home card cascade (visual refresh step 7b): the Vue side of utils/homeCardCascade.js.
// Design: docs/plans/2026-09-30-home-motion-design.md §3.1.
//
// HomeView provides a coordinator; each WorkspaceCard / ProjectCard hands it its root element
// once mounted. The cards mounted by one render form a batch, flushed once in a nextTick
// queued by the batch's first card (after every onMounted of the flush, before the paint):
// the batch is sorted in document order and the cards on screen get .home-card-entering and
// their --home-card-index. A card rendered outside the home finds no coordinator: no entrance.
//
// The class goes away on a timer, once the card and its sparkline have ended: Vue moves a
// card's node when the list reorders, and a moved node that kept the class would replay its
// entrance. --home-card-index stays, inert without the class.

import { inject, nextTick, onBeforeUnmount, onMounted, provide } from 'vue'
import { homeCardEndMs, planHomeCardCascade } from '../utils/homeCardCascade.js'

export const HOME_CARD_CASCADE_KEY = Symbol('homeCardCascade')

/** Called once in HomeView's setup: provides the coordinator, cleared on unmount. */
export function provideHomeCardCascade() {
    let batch = []
    const timers = new Set()

    function flush() {
        const elements = batch.filter((el) => el.isConnected)
        batch = []
        elements.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1))
        // The home scrolls the page, not an inner box.
        const plan = planHomeCardCascade(
            elements.map((el) => el.getBoundingClientRect()),
            { top: 0, bottom: window.innerHeight },
        )
        elements.forEach((el, i) => {
            const index = plan[i]
            if (index === null) return
            el.style.setProperty('--home-card-index', index)
            el.classList.add('home-card-entering')
            const timer = setTimeout(() => {
                timers.delete(timer)
                el.classList.remove('home-card-entering')
            }, homeCardEndMs(index))
            timers.add(timer)
        })
    }

    function enter(el) {
        if (!batch.length) nextTick(flush)
        batch.push(el)
    }

    provide(HOME_CARD_CASCADE_KEY, { enter })

    onBeforeUnmount(() => {
        for (const timer of timers) clearTimeout(timer)
        timers.clear()
        batch = []
    })
}

/** Called in a card's setup with the ref of its root element. */
export function useHomeCardEntrance(elRef) {
    const coordinator = inject(HOME_CARD_CASCADE_KEY, null)
    if (!coordinator) return
    onMounted(() => {
        if (elRef.value) coordinator.enter(elRef.value)
    })
}
