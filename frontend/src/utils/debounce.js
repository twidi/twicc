// frontend/src/utils/debounce.js
// Simple debounce utility with cancel support

/**
 * Creates a debounced function that delays invoking `fn` until after `delay`
 * milliseconds have elapsed since the last time the debounced function was invoked.
 *
 * @param {Function} fn - The function to debounce
 * @param {number} delay - The delay in milliseconds
 * @returns {Function} The debounced function with a `cancel` method
 */
export function debounce(fn, delay) {
    let timeoutId = null

    const debounced = (...args) => {
        if (timeoutId) clearTimeout(timeoutId)
        timeoutId = setTimeout(() => {
            fn(...args)
            timeoutId = null
        }, delay)
    }

    /**
     * Cancel any pending invocation.
     */
    debounced.cancel = () => {
        if (timeoutId) {
            clearTimeout(timeoutId)
            timeoutId = null
        }
    }

    return debounced
}
