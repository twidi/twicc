// The oracle and entry-reference inspections deliberately run outside each timed interval.
export function runGeometryCase({ useVirtualScroll, shallowRef, container, size, position }) {
    const index = position === 'head' ? 0 : position === 'middle' ? Math.floor(size / 2) : size - 1
    container.scrollTop = 0
    const items = shallowRef(Array.from({ length: size }, (_, key) => ({ key })))
    let keyReads = 0
    const scroll = useVirtualScroll({ items, itemKey: item => { keyReads++; return item.key },
        minItemHeight: 24, buffer: 500, unloadBuffer: 1000, containerRef: shallowRef(container) })
    scroll.updateViewportHeight()
    let previous = scroll.positions.value
    const initialKeyReads = keyReads, changes = []
    for (let change = 0; change < 60; change++) {
        const render = scroll.renderRange.value, visible = scroll.visibleRange.value
        keyReads = 0
        const started = performance.now()
        scroll.updateItemHeight(index, 25 + change)
        const positions = scroll.positions.value
        const total = scroll.totalHeight.value, before = scroll.spacerBeforeHeight.value, after = scroll.spacerAfterHeight.value
        const elapsedMs = performance.now() - started
        const operationKeyReads = keyReads
        const accepted = scroll.getItemHeight(index) === 25 + change
        if (!accepted) throw new Error(`Height change ${change} was not accepted`)
        let rebuiltEntries = 0
        for (let i = 0; i < size; i++) if (positions[i] !== previous[i]) rebuiltEntries++
        changes.push({ change, accepted, height: 25 + change, keyReads: operationKeyReads, rebuiltEntries, elapsedMs, total,
            spacerBefore: before, spacerAfter: after, renderRange: { ...scroll.renderRange.value },
            visibleRange: { ...scroll.visibleRange.value }, sameRenderRange: render === scroll.renderRange.value,
            sameVisibleRange: visible === scroll.visibleRange.value })
        previous = positions
    }
    let top = 0
    for (let i = 0; i < size; i++) {
        const height = i === index ? 84 : 24
        const entry = previous[i]
        if (entry.index !== i || entry.key !== i || entry.top !== top || entry.height !== height) throw new Error(`Oracle mismatch at ${i}`)
        top += height
    }
    if (top !== scroll.totalHeight.value) throw new Error('Oracle total mismatch')
    return { size, position, index, attemptedChanges: 60, acceptedChanges: changes.filter(c => c.accepted).length, initialKeyReads, changes, oraclePassed: true,
        container: { width: container.clientWidth ?? null, height: container.clientHeight, scrollTop: container.scrollTop },
        remainingCosts: ['Published position array reference copying can remain O(N).',
            'Existing height-stability watchers can scan the height cache; these scans are not allocation counts.'],
        timedInterval: 'Synchronous height writer and demanded geometry/total/spacer evaluation only; excludes nextTick/post-flush height-stability watchers, oracle, acceptance and identity inspection',
        feed: '60 synchronous accepted integer height changes, without frame pacing; no native bottom-follow claim' }
}
