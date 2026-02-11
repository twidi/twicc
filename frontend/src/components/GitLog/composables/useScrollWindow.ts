import { ref, computed, watch, onMounted, onUnmounted, type Ref } from 'vue'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ScrollWindow {
  /** Zero-based index of the first rendered row (includes buffer). */
  startIndex: Readonly<Ref<number>>
  /** Zero-based index past the last rendered row (includes buffer). */
  endIndex: Readonly<Ref<number>>
  /** Template ref to bind to the scroll container element. */
  scrollContainerRef: Ref<HTMLElement | null>
}

// ---------------------------------------------------------------------------
// Composable
// ---------------------------------------------------------------------------

/**
 * Tracks a scroll container and computes the visible row window.
 *
 * Designed for fixed-height rows: the row height is constant across
 * all items so we can compute indices in O(1) from scrollTop.
 *
 * @param totalCount  Total number of rows (reactive).
 * @param rowHeight   Height of a single row in pixels (reactive).
 * @param buffer      Extra rows rendered above and below the viewport (reactive).
 */
export function useScrollWindow(
  totalCount: Readonly<Ref<number>>,
  rowHeight: Readonly<Ref<number>>,
  buffer: Readonly<Ref<number>>,
): ScrollWindow {
  const scrollContainerRef = ref<HTMLElement | null>(null)

  // Reactive state updated by scroll / resize
  const scrollTop = ref(0)
  const containerHeight = ref(0)

  // ----- Scroll listener (throttled via rAF) -----

  let ticking = false

  function onScroll() {
    if (!ticking) {
      requestAnimationFrame(() => {
        scrollTop.value = scrollContainerRef.value?.scrollTop ?? 0
        ticking = false
      })
      ticking = true
    }
  }

  // ----- ResizeObserver for container height -----

  let resizeObserver: ResizeObserver | null = null

  function setupObserver() {
    const el = scrollContainerRef.value
    if (!el) return

    resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        containerHeight.value = entry.contentRect.height
      }
    })
    resizeObserver.observe(el)

    // Initial measurement
    containerHeight.value = el.clientHeight
  }

  function teardownObserver() {
    resizeObserver?.disconnect()
    resizeObserver = null
  }

  // ----- Lifecycle -----

  // Watch the ref so we can attach/detach when the element is set
  watch(scrollContainerRef, (el, oldEl) => {
    oldEl?.removeEventListener('scroll', onScroll)
    teardownObserver()

    if (el) {
      el.addEventListener('scroll', onScroll, { passive: true })
      setupObserver()
      scrollTop.value = el.scrollTop
    }
  })

  onMounted(() => {
    const el = scrollContainerRef.value
    if (el) {
      el.addEventListener('scroll', onScroll, { passive: true })
      setupObserver()
      scrollTop.value = el.scrollTop
    }
  })

  onUnmounted(() => {
    scrollContainerRef.value?.removeEventListener('scroll', onScroll)
    teardownObserver()
  })

  // ----- Computed window -----

  const startIndex = computed(() => {
    const rh = rowHeight.value
    if (rh <= 0) return 0
    const rawStart = Math.floor(scrollTop.value / rh)
    return Math.max(0, rawStart - buffer.value)
  })

  const endIndex = computed(() => {
    const rh = rowHeight.value
    if (rh <= 0) return 0
    const rawStart = Math.floor(scrollTop.value / rh)
    const visibleCount = Math.ceil(containerHeight.value / rh)
    return Math.min(totalCount.value, rawStart + visibleCount + buffer.value)
  })

  return {
    startIndex,
    endIndex,
    scrollContainerRef,
  }
}
