/**
 * Directive: v-popover-focus-fix
 *
 * Fixes the native <dialog> focus restoration behavior in wa-popover.
 * When a wa-popover closes, dialog.close() restores focus to the trigger button,
 * stealing it from whatever the user actually clicked. This directive captures
 * the active element before close and restores it after, or at minimum blurs the
 * trigger button to prevent space-bar from reopening the popover.
 *
 * Usage: <wa-popover v-popover-focus-fix ...>
 */

const stateMap = new WeakMap()

export const vPopoverFocusFix = {
    mounted(el) {
        const state = {
            // The element that had focus when the user clicked outside the popover
            // (captured on wa-hide, before dialog.close() steals focus)
            savedActiveElement: null,
        }

        // Both handlers only react to the popover's own events: a nested wa-select
        // or wa-dropdown fires the same wa-hide / wa-after-hide when its panel
        // closes, and handling those would move focus back onto that control.

        // wa-hide fires BEFORE the animation and dialog.close().
        // At this point, document.activeElement is still what the user clicked on.
        state.hideHandler = (event) => {
            if (event.target !== el) return
            state.savedActiveElement = document.activeElement
        }

        // wa-after-hide fires AFTER dialog.close() has restored focus to the trigger.
        // We undo that restoration: blur the trigger, then re-focus the saved element.
        state.afterHideHandler = (event) => {
            if (event.target !== el) return
            // Blur whatever dialog.close() just focused (works through shadow DOM)
            document.activeElement?.blur()

            // Re-focus what the user originally clicked, if it's focusable
            if (state.savedActiveElement && state.savedActiveElement !== document.body) {
                state.savedActiveElement.focus()
            }
            state.savedActiveElement = null
        }

        el.addEventListener('wa-hide', state.hideHandler)
        el.addEventListener('wa-after-hide', state.afterHideHandler)
        stateMap.set(el, state)
    },
    unmounted(el) {
        const state = stateMap.get(el)
        if (!state) return
        el.removeEventListener('wa-hide', state.hideHandler)
        el.removeEventListener('wa-after-hide', state.afterHideHandler)
        stateMap.delete(el)
    },
}
