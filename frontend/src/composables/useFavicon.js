/**
 * useFavicon — Animates the favicon robot based on global session state.
 *
 * Two independent signals are tracked:
 * - hasAssistantTurn: at least one session is actively working
 * - hasUnread: at least one session has unread content
 *
 * At rest the favicon is the static robot (no animation). While a signal is
 * on, the robot nods its head — its tilt is mirrored left/right, 1s per frame —
 * in its own colour for activity, in the warning colour for unread content:
 * - Activity only → 2-step cycle: active-left → active-right
 * - Unread only   → 2-step cycle: unread-left → unread-right
 * - Both          → 4-step cycle: active-left → active-right → unread-left → unread-right
 *
 * The frames are derived from `favicon.svg`: its `#tilt` group carries the
 * rotation, its `#body` group the head colour. They are pre-generated as blob
 * URLs once when the base SVG is loaded, then reused from cache. Blob URLs
 * avoid polluting the browser network panel (unlike data: URLs which Chrome
 * logs as requests on every href change).
 */
import { watch, ref, computed, onBeforeUnmount } from 'vue'
import { useDataStore } from '../stores/data'

/** CSS variable for the head colour of the unread frames. */
const UNREAD_CSS_VAR = '--wa-color-warning-60'

/**
 * Resolve a CSS custom property to its computed value.
 */
function resolveCssColor(varName) {
    return getComputedStyle(document.documentElement).getPropertyValue(varName).trim()
}

/**
 * Build one frame from the base favicon SVG: optionally mirror the head tilt
 * (`rotate(a …)` → `rotate(-a …)` on `#tilt`) and recolour the head (`#body`).
 */
function buildFrameSvg(svgText, { mirrored = false, color = null } = {}) {
    const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml')
    const tilt = doc.getElementById('tilt')
    if (mirrored && tilt) {
        const transform = tilt.getAttribute('transform') || ''
        tilt.setAttribute('transform', transform.replace(/rotate\(\s*(-?[\d.]+)/, (_, angle) => `rotate(${-parseFloat(angle)}`))
    }
    const body = doc.getElementById('body')
    if (color && body) {
        body.setAttribute('fill', color)
    }
    return new XMLSerializer().serializeToString(doc)
}

/**
 * Convert an SVG string to a blob: URL (avoids polluting the browser network panel).
 */
function svgToBlobUrl(svgString) {
    return URL.createObjectURL(new Blob([svgString], { type: 'image/svg+xml' }))
}

/**
 * Composable: watches global process state and animates the browser favicon,
 * cycling through the nodding frames.
 */
export function useFavicon() {
    const store = useDataStore()

    // ── Computed state ─────────────────────────────────────────────────
    const hasAssistantTurn = computed(() => store.hasGlobalAssistantTurn)
    const hasUnread = computed(() => store.getGlobalUnreadCount > 0)

    /**
     * Animation cycle steps. Each step is a key into cachedUrls.
     * - Neither active → null (no cycle, static favicon)
     * - One active → its left/right pair (2-step)
     * - Both active → active pair then unread pair (4-step)
     */
    const cycleSteps = computed(() => {
        const steps = []
        if (hasAssistantTurn.value) steps.push('activeLeft', 'activeRight')
        if (hasUnread.value) steps.push('unreadLeft', 'unreadRight')
        return steps.length ? steps : null
    })

    // ── DOM references ─────────────────────────────────────────────────
    const linkEl = document.querySelector('link[rel="icon"][type="image/svg+xml"]')
    const originalHref = linkEl?.getAttribute('href')

    // Set once the base SVG is loaded and the frames are cached (re-triggers the watcher)
    const framesReady = ref(false)
    let animationInterval = null

    // Pre-generated blob URLs for the 4 frames: { activeLeft, activeRight, unreadLeft, unreadRight }
    let cachedUrls = null

    /**
     * Build and cache the 4 frame blob URLs from the base SVG text.
     */
    function buildCache(svgText) {
        const unreadColor = resolveCssColor(UNREAD_CSS_VAR)
        cachedUrls = {
            activeLeft: svgToBlobUrl(buildFrameSvg(svgText)),
            activeRight: svgToBlobUrl(buildFrameSvg(svgText, { mirrored: true })),
            unreadLeft: svgToBlobUrl(buildFrameSvg(svgText, { color: unreadColor })),
            unreadRight: svgToBlobUrl(buildFrameSvg(svgText, { mirrored: true, color: unreadColor })),
        }
    }

    // Fetch the original favicon SVG
    if (originalHref) {
        fetch(originalHref)
            .then((r) => r.text())
            .then((svgText) => {
                buildCache(svgText)
                framesReady.value = true
            })
            .catch(() => {
                // Fetch failed — favicon stays unchanged
            })
    }

    // ── Helpers ────────────────────────────────────────────────────────

    function renderStep(stepKey) {
        if (!linkEl || !cachedUrls) return
        linkEl.setAttribute('href', cachedUrls[stepKey])
    }

    function restoreFavicon() {
        if (linkEl && originalHref) {
            linkEl.setAttribute('href', originalHref)
        }
    }

    function clearAnimation() {
        if (animationInterval !== null) {
            clearInterval(animationInterval)
            animationInterval = null
        }
    }

    // ── Reactive watcher ───────────────────────────────────────────────
    watch(
        [cycleSteps, framesReady],
        ([steps]) => {
            clearAnimation()

            if (!steps) {
                restoreFavicon()
                return
            }

            // Start at first step
            let stepIndex = 0
            renderStep(steps[stepIndex])

            // Cycle through steps
            animationInterval = setInterval(() => {
                stepIndex = (stepIndex + 1) % steps.length
                renderStep(steps[stepIndex])
            }, 1000)
        },
        { immediate: true }
    )

    // ── Cleanup ────────────────────────────────────────────────────────
    onBeforeUnmount(() => {
        clearAnimation()
        restoreFavicon()
    })
}
