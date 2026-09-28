// Keep a lazy wa-details body rendered while its card folds (visual refresh step 4b,
// docs/plans/2026-09-27-details-motion-design.md §5).
//
// The lazy cards render their content under `v-if` on an open flag that their `wa-hide`
// handler sets to false: Vue would remove the content before the fold starts. A key is
// marked closing in the same tick as the flag goes false (`wa-hide`, or any other write
// of the flag to false) and cleared at `wa-after-hide`, or at `wa-show` (a reopen during
// the fold: the abandoned fold sends no `wa-after-hide`). Render with
// `v-if="<open flag> || isClosing(key)"`.
import { reactive } from 'vue'

export function useDetailsClosing() {
    const closing = reactive(new Set())
    return {
        isClosing: (key = 'default') => closing.has(key),
        markClosing: (key = 'default') => { closing.add(key) },
        clearClosing: (key = 'default') => { closing.delete(key) },
    }
}
