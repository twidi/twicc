import { onScopeDispose, watch } from 'vue'

/** Keep list padding independent of native scrollbar visibility and platform width. */
export function useSidebarScrollbar(container, source, env = window) {
    let observer = null
    let element = null
    let wrapper = null

    function stop() {
        observer?.disconnect()
        observer = null
        element?.style.removeProperty('--sidebar-scrollbar-width')
        wrapper?.style.removeProperty(`--sidebar-${source}-scrollbar-width`)
        wrapper = null
        element = null
    }

    watch(container, (next) => {
        stop()
        if (!next) return
        element = next
        wrapper = next.closest('.project-view-wrapper')
        function measure(entries = []) {
            // Both sidebar scrollers have no border. offsetWidth includes the native
            // scrollbar; clientWidth includes padding but excludes that scrollbar.
            if (!next.offsetWidth) return // A hidden list keeps its last measurement.
            let width = next.offsetWidth - next.clientWidth
            const entry = entries.find(entry => entry.target === next)
            if (entry?.borderBoxSize?.[0] && entry?.contentBoxSize?.[0]) {
                // Observer sizes preserve fractional CSS pixels at browser zoom levels.
                // Integer clientWidth/offsetWidth only seed the initial measurement.
                const style = env.getComputedStyle(next)
                const padding = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0)
                width = entry.borderBoxSize[0].inlineSize - entry.contentBoxSize[0].inlineSize - padding
            }
            width = Math.max(0, Math.round(width * 64) / 64)
            const value = `${width}px`
            if (next.style.getPropertyValue('--sidebar-scrollbar-width') !== value) {
                next.style.setProperty('--sidebar-scrollbar-width', value)
                wrapper?.style.setProperty(`--sidebar-${source}-scrollbar-width`, value)
            }
        }
        measure()
        // The content box changes when a consuming scrollbar appears or disappears,
        // even if the scroller's outer width stays fixed.
        observer = new env.ResizeObserver(measure)
        observer.observe(next)
    }, { immediate: true, flush: 'post' })

    onScopeDispose(stop)
}
