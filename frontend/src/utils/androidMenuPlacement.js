import { shallowRef, watch } from 'vue'

const anchors = shallowRef([])

/** A departing route releases only its own anchor. */
export function registerAndroidMenuAnchor(element) {
    anchors.value = [...anchors.value.filter(anchor => anchor !== element), element]
    return () => { anchors.value = anchors.value.filter(anchor => anchor !== element) }
}

/** One global menu moves between the current rail and its fixed top-left position. */
export function watchAndroidMenuPlacement(window, onTarget) {
    let resizeObserver, styleObserver, stopped = false
    function visible(anchor) {
        if (!anchor.isConnected) return false
        const rail = anchor.closest('.sidebar-rail')
        if (!rail?.isConnected) return false
        const rect = rail.getBoundingClientRect(), style = window.getComputedStyle(rail)
        return style.visibility === 'visible' && style.display !== 'none' && rect.width > 0 && rect.height > 0 &&
            rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth
    }
    function refresh() {
        if (!stopped) onTarget([...anchors.value].reverse().find(visible) ?? null)
    }
    function observe() {
        resizeObserver?.disconnect()
        styleObserver?.disconnect()
        resizeObserver = window.ResizeObserver ? new window.ResizeObserver(refresh) : null
        styleObserver = window.MutationObserver ? new window.MutationObserver(refresh) : null
        const observed = new Set()
        for (const anchor of anchors.value) {
            const rail = anchor.closest('.sidebar-rail')
            if (!rail) continue
            resizeObserver?.observe(rail)
            // Observe only rail ancestors, not the timeline's frequently changing subtree.
            for (let node = rail; node; node = node.parentElement) {
                if (observed.has(node)) continue
                observed.add(node)
                styleObserver?.observe(node, { attributes: true,
                    attributeFilter: ['class', 'style', 'hidden', 'data-rail-when-closed'] })
            }
        }
        refresh()
    }
    const stopWatch = watch(anchors, observe, { immediate: true, flush: 'post' })
    window.addEventListener('resize', refresh)
    window.addEventListener('scroll', refresh, true)
    return { refresh, stop() {
        if (stopped) return
        stopped = true
        stopWatch()
        resizeObserver?.disconnect()
        styleObserver?.disconnect()
        window.removeEventListener('resize', refresh)
        window.removeEventListener('scroll', refresh, true)
        onTarget(null)
    } }
}
