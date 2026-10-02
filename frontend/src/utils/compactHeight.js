// The compact header mode (session header, project header, project panel, session view) starts
// when the viewport is at most COMPACT_HEIGHT_REM tall. A rem in a media query resolves against
// the browser's default font size, never the font size TwiCC applies to <html> (settings), so
// the threshold is measured here, against the real root font size, and exposed as a class on
// <html>. The styles read `:where(html.compact-height)`.
import { ref } from 'vue'

export const COMPACT_HEIGHT_REM = 56
export const COMPACT_HEIGHT_CLASS = 'compact-height'

// The same fact as a ref, for a component that must switch a class (a glass surface) on it: the
// styles read the class on <html>, which Vue cannot watch.
export const compactHeight = ref(false)

// The root font size in px, as a ref: a threshold in rem (a width below which a header drops its labels)
// is compared to a measured px width, so it needs the real root font size, kept current here.
export const rootFontSizePx = ref(16)

export function isCompactHeight(viewportHeightPx, rootFontSizePx) {
    return viewportHeightPx <= COMPACT_HEIGHT_REM * rootFontSizePx
}

/** Apply the class from the current viewport height and root font size. */
export function updateCompactHeight() {
    const root = document.documentElement
    const fontSize = parseFloat(getComputedStyle(root).fontSize)
    rootFontSizePx.value = fontSize
    compactHeight.value = isCompactHeight(window.innerHeight, fontSize)
    root.classList.toggle(COMPACT_HEIGHT_CLASS, compactHeight.value)
}

/** Apply it now, and again whenever the viewport is resized. The font size setting calls
 *  updateCompactHeight() itself after it changes. */
export function watchCompactHeight() {
    updateCompactHeight()
    window.addEventListener('resize', updateCompactHeight)
}
