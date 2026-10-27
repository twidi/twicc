// frontend/src/utils/timeline/panelController.js
//
// The close and focus behaviour of the Filters panel of the Timeline band (filters design sections 3.1 and 3.2).
// Pure: no Vue, no DOM global; the band hands the elements in.
//
// Why it exists. The panel is a `wa-popover` anchored to the Filters button. Web Awesome closes it from a
// bubble-phase `click` listener on the document (`handleDocumentClick`) for any click outside the popover and its
// anchor, and its native `<dialog>` close then moves the focus back to the anchor. The spec wants two things
// that behaviour does not give:
// - a click on another control of the band (a view, a pill, "Clear all") keeps the panel open: the band
//   listens to `click` in the CAPTURE phase (while the clicked node is still attached, before a pill removes
//   itself; keyboard activation fires a `click` too) and the next `wa-hide` is cancelled;
// - the focus goes to a fixed place when the focused control disappears (`focus`), and stays on the control the
//   user clicked in the list when that click closed the panel (the `wa-hide` / `wa-after-hide` pair).
// The band does NOT use `v-popover-focus-fix`: that directive re-focuses whatever was active at `wa-hide`,
// which would undo the focus targets.
//
// Event order of a user click (or a key) on a band control that removes itself while the panel is open: the
// browser runs a microtask checkpoint after each listener, so the band's `nextTick` resolves (and `focus` runs,
// `popover.open` still true) before the document listener sets `open = false`; `wa-hide` then saves the active
// element (the target) and `wa-after-hide` puts the focus back on it after the dialog moved it to the button.
// When `open` is already false at the `focus` call (a close is under way), the target is kept for
// `wa-after-hide` instead.

/**
 * @param {Object} [options]
 * @param {(callback: Function, delay: number) => unknown} [options.setTimeout] - Clears the one-shot band-click
 *   flag after the click's task.
 */
export function createPanelController({ setTimeout = globalThis.setTimeout } = {}) {
    // A click on a band control other than the Filters button: the next `wa-hide` is cancelled (one shot).
    let keep = false
    let clicks = 0
    // The panel is open, or closing (`wa-show` seen, `wa-after-hide` not yet).
    let shown = false
    // The close was asked by the code (the timeline left the screen): no veto, no focus move.
    let fromCode = false
    // The element that had the focus when the user closed the panel (restored after the dialog moved it).
    let saved = null
    // A focus target asked while a close was under way: focused after the close.
    let afterHideTarget = null

    function reset() {
        fromCode = false
        saved = null
        afterHideTarget = null
    }

    return {
        /**
         * The band root's `click` listener, in the capture phase.
         *
         * @param {{target: unknown}} event
         * @param {{band: {contains: Function}, button: {contains: Function}}} elements
         */
        onBandClick(event, { band, button }) {
            keep = Boolean(band?.contains(event.target)) && !button?.contains(event.target)
            const click = ++clicks
            setTimeout(() => {
                if (click === clicks) keep = false
            }, 0)
        },

        /** The popover's own `wa-show`. */
        onShow() {
            shown = true
            afterHideTarget = null
            saved = null
        },

        /**
         * The popover's own `wa-hide`.
         *
         * @param {{preventDefault: Function}} event
         * @param {{activeElement: unknown}} state - The element focused now.
         */
        onHide(event, { activeElement }) {
            if (fromCode) {
                saved = null
                return
            }
            if (keep) {
                keep = false
                event.preventDefault()
                // A vetoed close is no close: nothing is waiting for it.
                afterHideTarget = null
                return
            }
            if (!afterHideTarget) saved = activeElement
        },

        /**
         * The popover's own `wa-after-hide`: the dialog has closed and moved the focus to the button.
         *
         * @param {{button: {blur: Function}}} elements
         */
        onAfterHide({ button }) {
            shown = false
            if (fromCode) {
                // The user left the page: the focus is not ours to move.
                reset()
                return
            }
            if (afterHideTarget) {
                afterHideTarget()?.focus()
            } else if (saved?.isConnected) {
                button?.blur()
                saved.focus()
            }
            saved = null
            afterHideTarget = null
        },

        /**
         * Close the panel because the timeline left the screen. The flag lives until that close's
         * `wa-after-hide`, so it never bypasses a later veto; a closed panel sets nothing.
         *
         * @param {{open: boolean}} popover
         */
        closeFromCode(popover) {
            if (!popover?.open) return
            fromCode = true
            popover.open = false
        },

        /**
         * Focus a fixed target (spec 3.1) now, and again after the close under way, if any.
         *
         * @param {() => ({focus: Function}|null)} getTarget - Resolved at each call (the DOM may have changed).
         * @param {{open: boolean}} popover
         */
        focus(getTarget, popover) {
            getTarget()?.focus()
            if (shown && popover?.open === false) afterHideTarget = getTarget
        },
    }
}

/**
 * The height the Filters panel may take under its button, so the popover never runs past the bottom of the viewport
 * (it would overflow the page and break its scroll). The popover adds its own chrome around the panel: the distance
 * to the button, the arrow, the body's padding on both sides and its border; a small margin keeps it off the edge.
 *
 * @param {{anchorBottom: number, viewportHeight: number, remPx: number}} metrics - Pixels; `remPx` is the root font size.
 * @returns {number|null} The panel's max height in pixels, never under 10rem; null when a metric is missing.
 */
export function panelMaxHeight({ anchorBottom, viewportHeight, remPx }) {
    if (![anchorBottom, viewportHeight, remPx].every(Number.isFinite) || remPx <= 0) return null
    // distance 8px + arrow 0.375rem + body padding 2 × 1.5rem + border 2px + margin 0.75rem
    const chrome = 8 + 2 + remPx * (0.375 + 3 + 0.75)
    return Math.max(Math.round(remPx * 10), Math.floor(viewportHeight - anchorBottom - chrome))
}
