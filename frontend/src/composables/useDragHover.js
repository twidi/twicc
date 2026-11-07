import { i18n } from '../i18n/index.js'
import { ref } from 'vue'

/**
 * Check if a drag event carries files or text content.
 * Returns false for empty types (some external apps don't expose MIME info)
 * and for browser-internal drags like bookmarks/links.
 */
function isDragEligible(event) {
    const types = event.dataTransfer?.types
    if (!types || types.length === 0) return false
    return types.includes('Files') || types.includes('text/plain')
}

// ─── Floating cursor indicator ───────────────────────────────────────────────
// A wa-progress-ring element that follows the cursor during drag-hover,
// filling from 0 to 100% over the delay. Only one can be active at a time.

let indicatorEl = null
let animationFrame = null
let animationStart = null
let animationDelay = 0

function createIndicator(x, y, delay) {
    removeIndicator()
    const el = document.createElement('wa-progress-ring')
    el.value = 0
    el.label = i18n.global.t('navigation.dragHover.switching')
    el.className = 'drag-hover-indicator'
    el.style.setProperty('--x', x)
    el.style.setProperty('--y', y)
    document.body.appendChild(el)
    indicatorEl = el

    // Animate value from 0 to 100 over the delay using requestAnimationFrame
    animationStart = performance.now()
    animationDelay = delay
    function tick(now) {
        if (!indicatorEl) return
        const elapsed = now - animationStart
        const progress = Math.min(100, (elapsed / animationDelay) * 100)
        indicatorEl.value = progress
        if (progress < 100) {
            animationFrame = requestAnimationFrame(tick)
        }
    }
    animationFrame = requestAnimationFrame(tick)
}

function updateIndicatorPosition(x, y) {
    if (!indicatorEl) return
    indicatorEl.style.setProperty('--x', x)
    indicatorEl.style.setProperty('--y', y)
}

function removeIndicator() {
    if (animationFrame) {
        cancelAnimationFrame(animationFrame)
        animationFrame = null
    }
    animationStart = null
    if (indicatorEl) {
        indicatorEl.remove()
        indicatorEl = null
    }
}

// ─── Composable ──────────────────────────────────────────────────────────────

/**
 * Spring-loaded folder composable: after hovering with a drag for `delay` ms,
 * calls `onActivate`. Shows a floating wa-progress-ring near the cursor
 * that fills up during the delay.
 *
 * No nested-element counter here (unlike SessionItemsList's drop overlay):
 * each useDragHover instance tracks a single element. dragenter starts the
 * timer, dragleave cancels it. Simple and predictable.
 *
 * @param {Object} options
 * @param {Function} options.onActivate - Callback fired when the hover delay expires
 * @param {number} [options.delay=1000] - Hover duration in ms before activation
 * @param {Function} [options.shouldActivate] - Guard: return false to skip (e.g. already on this tab)
 * @param {Function} [options.onDropData] - Callback receiving { files: File[], text: string|null } when a drop occurs on this element. Called after extracting data from the native event (dataTransfer is only available synchronously).
 * @returns {{ onDragenter: Function, onDragleave: Function, onDragover: Function, onDrop: Function, isPending: import('vue').Ref<boolean>, cancel: Function }}
 */
export function useDragHover({ onActivate, delay = 1000, shouldActivate = null, onDropData = null }) {
    const isPending = ref(false)
    let timer = null
    let activated = false  // true after the timer completed and onActivate was called

    function startTimer(x, y) {
        cancelTimer()
        isPending.value = true
        createIndicator(x, y, delay)
        timer = setTimeout(() => {
            isPending.value = false
            timer = null
            activated = true
            removeIndicator()
            document.removeEventListener('dragend', cancel, true)
            onActivate()
        }, delay)
        // Listen for drag cancellation (Escape, drop outside window)
        document.addEventListener('dragend', cancel, true)
    }

    function cancelTimer() {
        if (timer) {
            clearTimeout(timer)
            timer = null
        }
        isPending.value = false
        activated = false
        removeIndicator()
        document.removeEventListener('dragend', cancel, true)
    }

    function onDragenter(event) {
        if (!isDragEligible(event)) return
        if (shouldActivate && !shouldActivate()) return
        startTimer(event.clientX, event.clientY)
    }

    function onDragleave(event) {
        // Only cancel if actually leaving the target element (not entering a child).
        // relatedTarget is the element the cursor moved INTO. If it's still inside
        // the current target, this is just a child-to-child transition — ignore it.
        if (event.relatedTarget && event.currentTarget.contains(event.relatedTarget)) return
        cancelTimer()
    }

    function onDragover(event) {
        // preventDefault required so browser treats the area as a valid drop zone.
        // Without this, dragenter/dragleave fire unreliably in some browsers.
        if (!isDragEligible(event)) return
        event.preventDefault()
        // Update floating indicator position to follow the cursor
        if (isPending.value) {
            updateIndicatorPosition(event.clientX, event.clientY)
        }
    }

    function onDrop(event) {
        event.preventDefault()
        event.stopPropagation()
        // Only forward drop data if the timer completed (activation happened).
        // If the user drops before the delay, just cancel — no action taken.
        const wasActivated = activated
        cancelTimer()
        if (wasActivated && onDropData) {
            const dt = event.dataTransfer
            const files = dt?.types?.includes('Files') ? [...dt.files] : []
            const text = !files.length ? (dt?.getData('text/plain') || null) : null
            if (files.length || text) {
                onDropData({ files, text })
            }
        }
    }

    function cancel() {
        cancelTimer()
    }

    return { onDragenter, onDragleave, onDragover, onDrop, isPending, cancel }
}
