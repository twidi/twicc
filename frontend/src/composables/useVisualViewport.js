/**
 * Composable that tracks the visual viewport height and exposes it as a CSS custom property.
 * This is needed because CSS viewport units (vh, dvh) don't account for the virtual keyboard on mobile.
 * The visual viewport height is set on document.documentElement as --visual-viewport-height.
 */
import { onMounted, onUnmounted } from 'vue'

let listenerCount = 0
let currentHeight = null

function updateViewportHeight() {
    const vh = window.visualViewport?.height || window.innerHeight
    if (vh !== currentHeight) {
        currentHeight = vh
        document.documentElement.style.setProperty('--visual-viewport-height', `${vh}px`)
    }
}

/**
 * Hook into the visual viewport resize event to track keyboard appearance.
 * Multiple components can use this composable - it will only add one listener.
 */
export function useVisualViewport() {
    onMounted(() => {
        if (listenerCount === 0) {
            // First consumer - set up the listener
            updateViewportHeight()
            window.visualViewport?.addEventListener('resize', updateViewportHeight)
            window.addEventListener('resize', updateViewportHeight)
        }
        listenerCount++
    })

    onUnmounted(() => {
        listenerCount--
        if (listenerCount === 0) {
            // Last consumer - clean up
            window.visualViewport?.removeEventListener('resize', updateViewportHeight)
            window.removeEventListener('resize', updateViewportHeight)
        }
    })
}
