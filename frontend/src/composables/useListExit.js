// Sidebar list exits (visual refresh retouches): the Vue side of utils/listExit.js. It sits between the
// list's items and the scroller: `displayItems` is `items` with the rows that just left (and were on
// screen) kept in place for the length of their exit, flagged `list-leaving` (styles/motion.css) with
// their measured height. One pre-flush watcher, so the row is never absent from a render: it is kept
// before the update that would have removed it. An exit ends on a timer, not on animationend: a row
// unmounted mid-animation must not stay.

import { computed, onScopeDispose, shallowRef, watch } from 'vue'
import { LIST_EXIT_DURATION_MS, mergeExits, pickExits } from '../utils/listExit.js'

const END_MARGIN_MS = 40

export function useListExit({ items, getKey, isEligible, scopeKey, getVisibleRange, getHeight, env = globalThis }) {
    const displayItems = shallowRef(items.value)
    const leaving = shallowRef([])        // { item, key, afterKey, height }, list order
    const timers = new Set()
    let lastScope = scopeKey()

    const leavingByKey = computed(() => new Map(leaving.value.map((entry) => [entry.key, entry])))

    function clear() {
        timers.forEach((id) => env.clearTimeout(id))
        timers.clear()
        leaving.value = []
    }

    function schedule(key) {
        const id = env.setTimeout(() => {
            timers.delete(id)
            leaving.value = leaving.value.filter((entry) => entry.key !== key)
            displayItems.value = mergeExits(items.value, leaving.value, getKey)
        }, LIST_EXIT_DURATION_MS + END_MARGIN_MS)
        timers.add(id)
    }

    watch(items, (next) => {
        const scope = scopeKey()
        if (scope !== lastScope) {
            // Another list (scope, search, filters): its rows replace the old ones, nothing exits.
            lastScope = scope
            clear()
            displayItems.value = next
            return
        }
        const nextKeys = new Set(next.map(getKey))
        // A row that came back (unarchived) while it was leaving is a normal row again.
        if (leaving.value.some((entry) => nextKeys.has(entry.key))) {
            leaving.value = leaving.value.filter((entry) => !nextKeys.has(entry.key))
        }
        const exits = pickExits({
            previousItems: displayItems.value,
            nextKeys,
            getKey,
            range: getVisibleRange(),
            isEligible,
            skipKeys: new Set(leaving.value.map((entry) => entry.key)),
        })
        if (exits.length) {
            leaving.value = [...leaving.value, ...exits.map((exit) => ({ ...exit, height: getHeight(exit.key) }))]
            exits.forEach((exit) => schedule(exit.key))
        }
        displayItems.value = mergeExits(next, leaving.value, getKey)
    }, { flush: 'pre' })

    onScopeDispose(clear)

    function exitClass(item) {
        return leavingByKey.value.has(getKey(item)) ? 'list-leaving' : null
    }

    function exitStyle(item) {
        const entry = leavingByKey.value.get(getKey(item))
        return entry ? { '--list-leave-h': `${entry.height}px` } : null
    }

    return { displayItems, exitClass, exitStyle }
}
