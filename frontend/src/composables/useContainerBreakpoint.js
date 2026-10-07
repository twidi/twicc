/**
 * Composable that tracks whether a container element is below a given width breakpoint.
 *
 * Instead of using viewport-based media queries (window.matchMedia), this uses a
 * ResizeObserver on a container element — the CSS-container-query equivalent in JS.
 * This lets panels react to the actual available width (e.g. when a sidebar
 * opens/closes) rather than just the viewport width.
 *
 * @param {Object} options
 * @param {string} [options.containerSelector] - CSS selector for an ancestor container to observe
 *        (e.g. '.main-content'); the composable walks up from the component's root element using
 *        `closest()`. When omitted, the component's OWN root element is observed — a true container
 *        query on the panel itself, so it reacts to the width it is actually rendered at (a dock
 *        region or the center slot in the dockable layout) rather than a fixed outer container.
 * @param {number} [options.breakpointRem=40] - Width threshold in rem. `isBelowBreakpoint`
 *        is `true` when the container is narrower than this value. The rem is converted to pixels
 *        with the live root font size (the "font size" user setting), so the threshold follows it.
 * @returns {{ isBelowBreakpoint: import('vue').Ref<boolean> }}
 */
import { ref, onMounted, onBeforeUnmount, getCurrentInstance } from 'vue'

export function useContainerBreakpoint({ containerSelector = null, breakpointRem = 40 } = {}) {
    const isBelowBreakpoint = ref(false)
    let observer = null
    let fontObserver = null

    onMounted(() => {
        const el = getCurrentInstance()?.proxy?.$el
        // A selector → observe that ancestor; no selector → observe the root element itself.
        const container = containerSelector ? el?.closest?.(containerSelector) : el
        if (!container) {
            console.warn(
                `[useContainerBreakpoint] Container ${containerSelector ? `"${containerSelector}"` : '(self)'} not found from`,
                el
            )
            return
        }

        let width = container.getBoundingClientRect().width
        const update = () => {
            const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
            isBelowBreakpoint.value = width < breakpointRem * rootPx
        }
        observer = new ResizeObserver((entries) => {
            width = entries[0].contentRect.width
            update()
        })
        observer.observe(container)
        // A font-size change moves the threshold without necessarily resizing the container.
        fontObserver = new MutationObserver(update)
        fontObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['style'] })
    })

    onBeforeUnmount(() => {
        observer?.disconnect()
        fontObserver?.disconnect()
    })

    return { isBelowBreakpoint }
}
