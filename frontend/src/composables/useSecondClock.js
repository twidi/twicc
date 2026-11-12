// frontend/src/composables/useSecondClock.js
//
// A clock shared by every component that shows a live duration: ONE timer ticks each second while at least one
// of them is active, and none runs otherwise. Used by the running-turn line of the Timeline.

import { ref, watch } from 'vue'

const now = ref(Date.now())
let subscribers = 0
let timer = null

function subscribe() {
    subscribers += 1
    if (subscribers === 1) {
        now.value = Date.now()
        timer = setInterval(() => { now.value = Date.now() }, 1000)
    }
    return () => {
        subscribers -= 1
        if (subscribers === 0) {
            clearInterval(timer)
            timer = null
        }
    }
}

/**
 * @param {() => boolean} isActive - Whether this component needs the clock now. Read reactively.
 * @returns {import('vue').Ref<number>} The current time in ms, updated each second while some component is active.
 */
export function useSecondClock(isActive) {
    watch(isActive, (active, _previous, onCleanup) => {
        if (active) onCleanup(subscribe())
    }, { immediate: true })
    return now
}
