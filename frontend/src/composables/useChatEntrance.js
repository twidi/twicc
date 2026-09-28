// Live chat entrances (visual refresh step 5a): the Vue side of utils/chatEntrance.js.
// Design: docs/plans/2026-09-28-chat-entrances-skeleton-design.md §5.
//
// Watches the visual-item list with a pre-flush watcher, so an entering row carries its
// class at its first render (a post-flush class would show the row one frame at full
// opacity, then blink). The owner feeds the live lines, the retired streamed blocks and
// the view changes (store.$onAction listeners); the scroller binds itemClass/itemStyle on
// each row wrapper. An entry ends on a timer, not on animationend: a row unmounted
// mid-animation must not keep it.

import { onScopeDispose, reactive, watch } from 'vue'
import { getParsedContent } from '../utils/parsedContent.js'
import { parseDurationMs } from '../utils/detailsMotion.js'
import { CHAT_ENTRANCE_STAGGER_MS, planChatEntrances } from '../utils/chatEntrance.js'

const FALLBACK_DURATION_MS = 380
const END_MARGIN_MS = 100

/** True when the item's parsed content holds a tool_use or tool_result block (Claude shape). */
function hasToolBlock(item) {
    const content = getParsedContent(item)?.message?.content
    return Array.isArray(content) && content.some((block) => block?.type === 'tool_use' || block?.type === 'tool_result')
}

export function useChatEntrance({ items, getKey, isRevealed, env = globalThis }) {
    let previousKeys = new Set()
    let previousStreamingKeys = new Set()
    const pendingLive = new Set()
    const pendingRetired = new Set()
    let pendingViewChange = false
    const entering = reactive(new Map())  // key → { index, variant }
    const timers = new Map()              // key → timeout id

    function entranceDuration() {
        const root = env.document?.documentElement
        if (!root) return FALLBACK_DURATION_MS
        const duration = parseDurationMs(env.getComputedStyle(root).getPropertyValue('--motion-dur-3'))
        return duration || FALLBACK_DURATION_MS
    }

    function cancelTimer(key) {
        if (!timers.has(key)) return
        env.clearTimeout(timers.get(key))
        timers.delete(key)
    }

    function run(initial) {
        const plan = planChatEntrances({
            previousKeys,
            items: items.value ?? [],
            getKey,
            liveLineNums: pendingLive,
            retiredRealLineNums: pendingRetired,
            previousStreamingKeys,
            revealed: !initial && isRevealed(),
            viewChanged: pendingViewChange,
            hasToolBlock,
        })
        const oldKeys = previousKeys
        previousKeys = plan.keys
        previousStreamingKeys = plan.streamingKeys
        pendingLive.clear()
        pendingRetired.clear()
        pendingViewChange = false

        // A row that leaves and comes back never replays nor gets cut.
        for (const key of oldKeys) {
            if (plan.keys.has(key)) continue
            entering.delete(key)
            cancelTimer(key)
        }

        if (!plan.entering.length) return
        const duration = entranceDuration()
        for (const { key, index, variant } of plan.entering) {
            cancelTimer(key)
            entering.set(key, { index, variant })
            timers.set(key, env.setTimeout(() => {
                timers.delete(key)
                entering.delete(key)
            }, index * CHAT_ENTRANCE_STAGGER_MS + duration + END_MARGIN_MS))
        }
    }

    run(true)
    watch(items, () => run(false), { flush: 'pre' })

    function itemClass(item) {
        const entry = entering.get(getKey(item))
        return entry ? ['chat-entering', `is-${entry.variant}`] : null
    }

    function itemStyle(item) {
        const entry = entering.get(getKey(item))
        return entry ? { '--chat-enter-index': entry.index } : null
    }

    function noteLive(lineNums) {
        if (!Array.isArray(lineNums)) return
        for (const lineNum of lineNums) pendingLive.add(lineNum)
    }

    function noteRetired(pairs) {
        if (!Array.isArray(pairs)) return
        for (const pair of pairs) pendingRetired.add(pair.realLineNum)
    }

    function noteViewChange() {
        pendingViewChange = true
    }

    function clear() {
        for (const id of timers.values()) env.clearTimeout(id)
        timers.clear()
        entering.clear()
    }

    onScopeDispose(clear)

    return { itemClass, itemStyle, noteLive, noteRetired, noteViewChange, clear }
}
