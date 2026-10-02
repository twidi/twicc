// Group reveal (visual refresh retouches): the Vue side of utils/groupReveal.js. Opening a group of hidden
// elements in the chat: the block opens from 0 to its height like a curtain, top to bottom, and the head's own
// item and the rows the group adds slide in from outside the chat, one after the other (styles/group-reveal.css,
// driven by --group-index and --group-count). Closing it: the rows it removes exit
// (composables/useListExit.js, fed by `isCollapsing`) and the head's item folds out, kept in the template while
// `isHeadLeaving`. The owner calls `noteToggle` right before the store action; one pre-flush watcher then plans
// the entrance, so a row carries its class at its first render. The height follows the real content (a grid
// track, a min-height for a placeholder), never a height measured at insertion: a row whose content loads or
// renders late was clipped, then jumped. Every state ends on a timer, not on animationend: a row unmounted
// mid-animation must not keep it.

import { onScopeDispose, reactive, ref, watch } from 'vue'
import { GROUP_REVEAL_WINDOW_MS, groupRevealEndMs, planGroupReveal } from '../utils/groupReveal.js'
import { isReducedMotion } from '../utils/reducedMotion.js'

const LEAVE_DURATION_MS = 260
const END_MARGIN_MS = 60

export function useGroupReveal({ items, getKey, env = globalThis }) {
    const entering = reactive(new Map())      // key → index of the rows that enter (the head item is 0)
    const groupCount = ref(1)                 // rows + the head item, of the group being opened
    const headRevealing = reactive(new Set()) // head keys whose own item enters
    const headLeaving = reactive(new Map())   // head key → measured height of its item
    const collapsing = ref(false)
    const timers = new Set()
    let pending = null                        // { previousKeys, expanding }
    let collapseTimer = null

    function later(fn, ms) {
        const id = env.setTimeout(() => {
            timers.delete(id)
            fn()
        }, ms)
        timers.add(id)
        return id
    }

    /** Call right before the store toggles the group of `headKey`. `headHeight`: the head item's height now. */
    function noteToggle(headKey, expanding, headHeight) {
        // Reduced motion: the group opens and closes at once (the curtain is movement).
        if (isReducedMotion(env)) return
        pending = { previousKeys: new Set((items.value ?? []).map(getKey)), expanding }
        if (expanding) {
            headRevealing.add(headKey)
            // Until the plan says otherwise, the head item alone; a group with no new row ends here.
            later(() => headRevealing.delete(headKey), groupRevealEndMs(1) + END_MARGIN_MS)
            return
        }
        collapsing.value = true
        if (collapseTimer !== null) {
            env.clearTimeout(collapseTimer)
            timers.delete(collapseTimer)
        }
        collapseTimer = later(() => {
            collapsing.value = false
            collapseTimer = null
        }, GROUP_REVEAL_WINDOW_MS)
        if (headHeight > 0) {
            headLeaving.set(headKey, headHeight)
            later(() => headLeaving.delete(headKey), LEAVE_DURATION_MS + END_MARGIN_MS)
        }
    }

    watch(items, (next) => {
        if (!pending) return
        const { previousKeys, expanding } = pending
        pending = null
        if (!expanding) return
        const { rows, count } = planGroupReveal({ previousKeys, items: next ?? [], getKey })
        groupCount.value = count
        for (const { key, index } of rows) {
            entering.set(key, index)
            later(() => entering.delete(key), groupRevealEndMs(count) + END_MARGIN_MS)
        }
    }, { flush: 'pre' })

    onScopeDispose(() => {
        timers.forEach((id) => env.clearTimeout(id))
        timers.clear()
    })

    function itemClass(item) {
        const key = getKey(item)
        const classes = []
        if (entering.has(key)) classes.push('group-row-growing')
        if (headRevealing.has(key)) classes.push('group-head-revealing')
        return classes.length ? classes : null
    }

    function itemStyle(item) {
        const key = getKey(item)
        const index = headRevealing.has(key) ? 0 : entering.get(key)
        return index === undefined ? null : { '--group-index': index, '--group-count': groupCount.value }
    }

    return {
        itemClass,
        itemStyle,
        noteToggle,
        isCollapsing: () => collapsing.value,
        isHeadLeaving: (key) => headLeaving.has(key),
        headLeaveClass: (key) => (headLeaving.has(key) ? 'group-head-leaving' : null),
        headLeaveStyle: (key) => (headLeaving.has(key) ? { '--list-leave-h': `${headLeaving.get(key)}px` } : null),
    }
}
