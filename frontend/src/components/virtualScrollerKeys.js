/**
 * Shared keys for VirtualScroller provide/inject pattern.
 *
 * This file exists because Vue SFC `<script setup>` blocks cannot have
 * ES module exports (export const). Shared symbols must be in a separate file.
 */

/**
 * Symbol key for the shared ResizeObserver context.
 * Used by VirtualScroller (provide) and VirtualScrollerItem (inject)
 * to share a single ResizeObserver instance for performance.
 *
 * See: https://github.com/WICG/resize-observer/issues/59
 */
export const RESIZE_OBSERVER_KEY = Symbol('virtualScrollerResizeObserver')
