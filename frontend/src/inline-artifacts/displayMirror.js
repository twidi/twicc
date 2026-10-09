export const DISPLAY_ATTRIBUTE = 'data-twicc-display'

/**
 * Mirror the host iframe's `data-twicc-display` on the document root, so the
 * artifact's CSS can target `:root[data-twicc-display="inline"]`. Runs
 * synchronously from the shim, before the page paints. Frames without the
 * attribute (Artifacts tab, dedicated page) and top-level pages leave the root
 * untouched.
 */
export function createDisplayMirror({ document, window, MutationObserver = window.MutationObserver }) {
    let frame = null
    try {
        frame = window.frameElement
    } catch {
        // Cross-origin parent: no display information.
    }
    if (!frame) return { dispose() {} }
    const root = document.documentElement
    const sync = () => {
        const value = frame.getAttribute(DISPLAY_ATTRIBUTE)
        if (value) root.setAttribute(DISPLAY_ATTRIBUTE, value)
        else root.removeAttribute(DISPLAY_ATTRIBUTE)
    }
    sync()
    const observer = new MutationObserver(sync)
    observer.observe(frame, { attributes: true, attributeFilter: [DISPLAY_ATTRIBUTE] })
    return { dispose: () => observer.disconnect() }
}
