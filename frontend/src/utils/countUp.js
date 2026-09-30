// Count-up of the project stats numbers (visual refresh step 7a,
// docs/plans/2026-09-30-stats-motion-design.md §4). Pure helpers of AnimatedNumber.vue.

/** Ease-out cubic: fast start, soft landing. `k` is the linear progress, 0..1. */
export function easeOutCubic(k) {
    return 1 - (1 - k) ** 3
}

/**
 * The eased value between `from` and `to` at linear progress `k` (0..1); exactly `to` at the
 * end. A negative `k` (a rAF timestamp earlier than the tween start) stays on `from`.
 */
export function tweenValue(from, to, k) {
    if (k >= 1) return to
    if (k <= 0) return from
    return from + (to - from) * easeOutCubic(k)
}

/**
 * Format a decimal number for display (e.g. 12.3, 0.5).
 * Shows one decimal place, or integer if whole number; '-' for null.
 */
export function formatAvg(value) {
    if (value === null) return '-'
    const rounded = Math.round(value * 10) / 10
    return rounded % 1 === 0 ? rounded.toString() : rounded.toFixed(1)
}
