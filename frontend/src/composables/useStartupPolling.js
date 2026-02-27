// frontend/src/composables/useStartupPolling.js

import { watch, onUnmounted } from 'vue'
import { useDataStore } from '../stores/data'

/**
 * Poll a callback at a fixed interval while the backend startup is in progress.
 *
 * Starts polling when isStartupInProgress becomes true and stops as soon as
 * it turns false (or the component is unmounted). The callback is NOT called
 * immediately — only after the first interval elapses, so it complements the
 * initial fetch that each consumer already performs on mount.
 *
 * @param {() => void} callback - Function to call on each tick (may be async)
 * @param {number} [interval=15000] - Polling interval in milliseconds
 */
export function useStartupPolling(callback, interval = 15000) {
    const store = useDataStore()
    let timer = null

    function start() {
        if (timer) return
        timer = setInterval(callback, interval)
    }

    function stop() {
        if (timer) {
            clearInterval(timer)
            timer = null
        }
    }

    watch(
        () => store.isStartupInProgress,
        (inProgress) => {
            if (inProgress) {
                start()
            } else {
                stop()
            }
        },
        { immediate: true },
    )

    onUnmounted(stop)
}
