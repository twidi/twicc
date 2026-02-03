/**
 * useToast composable - Simplified API for toast notifications
 *
 * This wraps Notivue's push API to provide a cleaner interface
 * that can be used from any component, store, or utility function.
 *
 * Usage:
 *   import { useToast } from '@/composables/useToast'
 *
 *   // In a component or store:
 *   const toast = useToast()
 *   toast.success('Saved!')
 *   toast.error('Failed to connect')
 *
 *   // Direct import also works:
 *   import { toast } from '@/composables/useToast'
 *   toast.success('Done!')
 */

import { push } from 'notivue'

/**
 * Show a success toast
 * @param {string} message - The message to display
 * @param {Object} options - Additional options
 * @param {string} options.title - Optional title
 * @param {number} options.duration - Duration in ms (default: 4000)
 */
function success(message, options = {}) {
    const pushOptions = {
        message,
        title: options.title,
    }
    // Only set duration if explicitly provided, otherwise use Notivue's default
    if (options.duration !== undefined) {
        pushOptions.duration = options.duration
    }
    return push.success(pushOptions)
}

/**
 * Show an error toast
 * @param {string} message - The message to display
 * @param {Object} options - Additional options
 */
function error(message, options = {}) {
    const pushOptions = {
        message,
        title: options.title,
    }
    // Only set duration if explicitly provided, otherwise use Notivue's default
    if (options.duration !== undefined) {
        pushOptions.duration = options.duration
    }
    return push.error(pushOptions)
}

/**
 * Show a warning toast
 * @param {string} message - The message to display
 * @param {Object} options - Additional options
 */
function warning(message, options = {}) {
    const pushOptions = {
        message,
        title: options.title,
    }
    // Only set duration if explicitly provided, otherwise use Notivue's default
    if (options.duration !== undefined) {
        pushOptions.duration = options.duration
    }
    return push.warning(pushOptions)
}

/**
 * Show an info toast
 * @param {string} message - The message to display
 * @param {Object} options - Additional options
 */
function info(message, options = {}) {
    const pushOptions = {
        message,
        title: options.title,
    }
    // Only set duration if explicitly provided, otherwise use Notivue's default
    if (options.duration !== undefined) {
        pushOptions.duration = options.duration
    }
    return push.info(pushOptions)
}

// Export as object for composable pattern
export const toast = {
    success,
    error,
    warning,
    info,
}

// Export as composable function
export function useToast() {
    return toast
}
