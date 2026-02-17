/**
 * Composable that tracks whether a container element is below a given width breakpoint.
 *
 * Instead of using viewport-based media queries (window.matchMedia), this uses a
 * ResizeObserver on a container element — the CSS-container-query equivalent in JS.
 * This lets panels react to the actual available width (e.g. when a sidebar
 * opens/closes) rather than just the viewport width.
 *
 * @param {Object} options
 * @param {string} options.containerSelector - CSS selector for the container to observe
 *        (e.g. '.main-content'). The composable walks up from the component's root
 *        element using `closest()`.
 * @param {number} [options.breakpoint=640] - Width threshold in pixels. `isBelowBreakpoint`
 *        is `true` when the container is narrower than this value.
 * @returns {{ isBelowBreakpoint: import('vue').Ref<boolean> }}
 */
import { ref, onMounted, onBeforeUnmount, getCurrentInstance } from 'vue'

export function useContainerBreakpoint({ containerSelector, breakpoint = 640 } = {}) {
    const isBelowBreakpoint = ref(false)
    let observer = null

    onMounted(() => {
        const el = getCurrentInstance()?.proxy?.$el
        const container = el?.closest?.(containerSelector)
        if (!container) {
            console.warn(
                `[useContainerBreakpoint] Container "${containerSelector}" not found from`,
                el
            )
            return
        }

        observer = new ResizeObserver((entries) => {
            const width = entries[0].contentRect.width
            isBelowBreakpoint.value = width < breakpoint
        })
        observer.observe(container)
    })

    onBeforeUnmount(() => {
        observer?.disconnect()
    })

    return { isBelowBreakpoint }
}
