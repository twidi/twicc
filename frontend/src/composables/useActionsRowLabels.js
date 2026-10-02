// Whether a header's row of action buttons spells out each button's name.
//
// The names show when the row fits on one line, and the buttons are icons only otherwise. It is measured,
// not a fixed width: the names' widths follow the font size setting and the set of buttons, so any
// constant goes stale the day a button is added. The names are tried whenever the header is wider than
// the last width at which the row wrapped; a wrap, detected right after the render and before the paint,
// records that width and drops the names. The record resets when `resetKey` (a getter returning what the
// row holds, or anything else that changes the buttons' widths) changes. The header lives in a dock pane,
// so its width does not follow the viewport: it is measured.
//
// The row's direct children must be the buttons (`wa-button`, or `wa-dropdown` around one).

import { computed, ref, watch } from 'vue'
import { useElementSize } from '@vueuse/core'

/**
 * @param {import('vue').Ref<HTMLElement|null>} headerRef - the header element, whose width is measured
 * @param {import('vue').Ref<HTMLElement|null>} actionsRef - the row of action buttons
 * @param {() => unknown[]} resetKey - getter of what, when it changes, forgets the recorded wrap width
 * @returns {import('vue').ComputedRef<boolean>} whether the buttons show their names
 */
export function useActionsRowLabels(headerRef, actionsRef, resetKey) {
    const { width: headerWidth } = useElementSize(headerRef)
    const wrappedAt = ref(0)
    const labels = computed(() => headerWidth.value > wrappedAt.value)

    function rowWraps() {
        const buttons = actionsRef.value?.querySelectorAll(':scope > wa-button, :scope > wa-dropdown')
        if (!buttons || buttons.length < 2) return false
        return buttons[buttons.length - 1].offsetTop - buttons[0].offsetTop > 2
    }

    watch([headerWidth, labels], () => {
        if (labels.value && rowWraps()) {
            wrappedAt.value = Math.max(wrappedAt.value, headerWidth.value)
        }
    }, { flush: 'post' })

    watch(resetKey, () => { wrappedAt.value = 0 })

    return labels
}
