export const REDUCE_MOTION_ATTRIBUTE = 'data-twicc-reduce-motion'
export const REDUCE_EFFECTS_ATTRIBUTE = 'data-twicc-reduce-effects'
export const PREFERENCES_STYLE_ID = 'twicc-app-preferences'

/**
 * Carry the embedding page's display preferences into an artifact document. The app (and
 * the share viewer) put them on its <html>: the font size as an inline style, "Reduce
 * effects" and reduced motion as the `reduce-effects` / `reduce-motion` classes
 * (utils/reducedMotion.js). The artifact gets the font size as `--twicc-root-font-size` and
 * the two others as attributes on its root. Nothing changes how the page renders until it
 * uses them (the kit applies the font size): previews of the user's own HTML files get the
 * shim too and must stay faithful.
 * Runs synchronously from the shim, before the page paints, then follows changes.
 * A top-level or cross-origin parent leaves the document untouched.
 */
export function createAppPreferencesMirror({ document, window, MutationObserver = window.MutationObserver }) {
    let parentWindow = null
    let parentRoot = null
    try {
        if (window.parent !== window) {
            parentWindow = window.parent
            parentRoot = parentWindow.document.documentElement
        }
    } catch {
        // Cross-origin parent: no preferences to read.
    }
    if (!parentRoot) return { dispose() {} }
    const root = document.documentElement
    const style = document.createElement('style')
    style.id = PREFERENCES_STYLE_ID
    ;(document.head || root).prepend(style)
    const sync = () => {
        const fontSize = parentWindow.getComputedStyle(parentRoot).fontSize
        style.textContent = fontSize ? `:where(:root) { --twicc-root-font-size: ${fontSize}; }` : ''
        root.toggleAttribute(REDUCE_MOTION_ATTRIBUTE, parentRoot.classList.contains('reduce-motion'))
        root.toggleAttribute(REDUCE_EFFECTS_ATTRIBUTE, parentRoot.classList.contains('reduce-effects'))
    }
    sync()
    const observer = new MutationObserver(sync)
    observer.observe(parentRoot, { attributes: true, attributeFilter: ['class', 'style'] })
    return { dispose: () => observer.disconnect() }
}
