// frontend/src/composables/useChatNavigation.js

import { computed } from 'vue'
import { blockTargets, nextBlockTarget, prevBlockTarget } from '../utils/chatBlocks'

/**
 * Transcript navigation: jump to the extremes, or block by block.
 *
 * Shared by the session chat and the read-only share viewer. Everything that
 * differs between them is injected: how an item's content is loaded before the
 * scroller can land on it, and how an extreme is reached. What stays here is
 * the block arithmetic, the enabled/disabled states, and the serialization of
 * overlapping navigations.
 *
 * @param {Object} options
 * @param {import('vue').Ref} options.scrollerRef - Ref on the VirtualScroller component.
 * @param {import('vue').Ref<Array>} options.visualItems - The rendered items.
 * @param {import('vue').Ref<Array>} [options.displayItems] - Rows including retained exits.
 * @param {(lineNum: any, offset: number) => Promise<any>} options.scrollToItem - Bring one
 *   item to the top of the viewport, stopping `offset` pixels short of it, and
 *   loading whatever it needs first.
 * @param {(edge: 'top' | 'bottom') => Promise<any>} options.scrollToEdge - Reach an extreme.
 */
export function useChatNavigation({ scrollerRef, visualItems, displayItems = visualItems, scrollToItem, scrollToEdge }) {
    const targets = computed(() => blockTargets(visualItems.value))

    // ── Leading gap ──────────────────────────────────────────────────────────
    // Blocks do not all carry the space that separates them. A user card holds
    // its own top margin, so pinning it to the viewport top shows that margin
    // first. Everything the agent writes spaces itself with padding instead:
    // pinned the same way, it would sit flush against the top edge. So those
    // blocks stop short by the gap, and the two read alike.
    //
    // The value is the scroll container's `scroll-padding-top` — declared in
    // SessionItem.vue, which is also where the gap itself is defined, and
    // already resolved to pixels whatever the container query decided.

    let cachedGap = null

    function readGap() {
        const el = scrollerRef.value?.$el
        if (!el) return 0
        return parseFloat(getComputedStyle(el).scrollPaddingTop) || 0
    }

    /**
     * @param {boolean} [refresh] - Re-read the stylesheet. Actions do; the
     *   buttons' enabled state reuses the cache rather than forcing a style
     *   recalculation on every scroll frame, where being a few pixels stale
     *   changes nothing it decides.
     */
    function gapPx(refresh = false) {
        if (refresh || cachedGap === null) cachedGap = readGap()
        return cachedGap
    }

    /** True when the item already carries the inter-block space above itself. */
    function carriesOwnGap(item) {
        return !item || item.isDaySeparator || item.kind === 'user_message'
    }

    // `positions` and `scrollTop` come off the scroller's exposed object, which
    // Vue runs through `proxyRefs` — the refs arrive already unwrapped, and
    // reading them here still registers the dependency.

    /**
     * Where the scroll must land for the block at `index` to count as reached:
     * its own offset, less the room left above it. Null while geometry is
     * unknown. This — not the raw item offset — is what the navigation compares
     * against, so a block is never re-targeted just because we stopped short of
     * it on purpose.
     */
    const positionByKey = computed(() => {
        const positions = scrollerRef.value?.positions ?? []
        return new Map(displayItems.value.map((item, index) => [item.lineNum, positions[index]]))
    })

    function makeTopOf(gap) {
        const positions = positionByKey.value
        const items = visualItems.value
        return (index) => {
            const key = items?.[index]?.lineNum
            const top = positions.get(key)?.top
            if (top == null) return null
            return Math.max(0, top - (carriesOwnGap(items?.[index]) ? 0 : gap))
        }
    }

    /** Every target whose position is known, so `topOf` never returns null downstream. */
    const placedTargets = computed(() => {
        const positions = positionByKey.value
        return targets.value.filter(target => positions.get(visualItems.value[target].lineNum) !== undefined)
    })

    // Reactive, updated on every scroll frame. Good enough to drive the buttons'
    // enabled state. Actions read the DOM instead — see currentScrollTop.
    const scrollTop = computed(() => scrollerRef.value?.scrollTop ?? 0)

    /**
     * The scroll position as the browser actually has it.
     *
     * The scroller's ref is written optimistically by its own jumps and only
     * reconciled on the next scroll event, which never fires when a jump lands
     * where the container already was. A navigation decided on a stale value
     * targets the block it is already on, and the press does nothing.
     */
    function currentScrollTop() {
        return scrollerRef.value?.getScrollState?.().scrollTop ?? scrollTop.value
    }

    // Asymmetric on purpose. The top is an exact position, so a few pixels are
    // enough to call it reached. The bottom is only ever an estimate while items
    // below the viewport are unmeasured, so it uses the scroller's own
    // near-bottom sentinel — the same "at bottom" the rest of the app follows.
    const atTop = computed(() => scrollerRef.value?.isAtTop?.(4) ?? true)
    const atBottom = computed(() => scrollerRef.value?.isAtBottom?.() ?? true)

    const canGoTop = computed(() => !atTop.value)
    const canGoBottom = computed(() => !atBottom.value)
    const canGoPrev = computed(
        () => prevBlockTarget(placedTargets.value, makeTopOf(gapPx()), scrollTop.value) !== null,
    )
    // Also off at the bottom: the remaining blocks all start below a scroll
    // position the container can no longer reach, so the button would be dead.
    const canGoNext = computed(
        () => !atBottom.value
            && nextBlockTarget(placedTargets.value, makeTopOf(gapPx()), scrollTop.value) !== null,
    )

    // Nothing to navigate — a transcript shorter than the viewport. Hosts use
    // this to drop the toolbar entirely rather than show four dead buttons.
    const hasNavigation = computed(
        () => canGoTop.value || canGoPrev.value || canGoNext.value || canGoBottom.value,
    )

    // ── Serialization ────────────────────────────────────────────────────────
    // A navigation takes a few hundred ms (the scroller jumps, waits for the
    // heights to settle, corrects). Letting a second one start meanwhile makes
    // the two fight over the scroll position, so requests queue instead. The
    // queue holds one entry, and repeated clicks in the same direction add up
    // into it: hitting "next block" five times moves five blocks, in one scroll.

    let running = false
    /** @type {{ kind: 'block', dir: 1 | -1, count: number } | { kind: 'edge', edge: string } | null} */
    let queued = null

    async function run(request) {
        if (running) {
            queued = mergeRequest(queued, request)
            return
        }
        running = true
        try {
            let current = request
            while (current) {
                queued = null
                await perform(current)
                current = queued
            }
        } finally {
            running = false
            queued = null
        }
    }

    /** Fold a new request into the queued one: same-direction block steps add up. */
    function mergeRequest(pending, request) {
        if (
            pending?.kind === 'block' && request.kind === 'block'
            && pending.dir === request.dir
        ) {
            return { ...pending, count: pending.count + request.count }
        }
        return request
    }

    async function perform(request) {
        if (request.kind === 'edge') {
            await scrollToEdge(request.edge)
            return
        }
        const gap = gapPx(true)
        const index = stepBlocks(request.dir, request.count, gap)
        if (index === null) return
        const item = visualItems.value?.[index]
        if (!item) return
        await scrollToItem(item.lineNum, carriesOwnGap(item) ? 0 : gap)
    }

    /**
     * Walk `count` blocks in `dir` from the current position and return the
     * index to land on (null when already at the end of that direction).
     * Walking in one go means repeated clicks cost a single scroll.
     */
    function stepBlocks(dir, count, gap) {
        const list = placedTargets.value
        const topOf = makeTopOf(gap)
        let position = currentScrollTop()
        let target = null

        for (let step = 0; step < count; step++) {
            const next = dir > 0
                ? nextBlockTarget(list, topOf, position)
                : prevBlockTarget(list, topOf, position)
            if (next === null) break
            target = next
            // Every intermediate hop lands exactly on a block start.
            position = topOf(next)
        }
        return target
    }

    return {
        hasNavigation,
        canGoTop,
        canGoBottom,
        canGoPrev,
        canGoNext,
        goTop: () => run({ kind: 'edge', edge: 'top' }),
        goBottom: () => run({ kind: 'edge', edge: 'bottom' }),
        goPrevBlock: () => run({ kind: 'block', dir: -1, count: 1 }),
        goNextBlock: () => run({ kind: 'block', dir: 1, count: 1 }),
    }
}
