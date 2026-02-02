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
 * @param {boolean} options.showUndo - Show undo button
 * @param {Function} options.onUndo - Undo callback
 * @param {string} options.actionLabel - Custom action button label
 * @param {Function} options.onAction - Custom action callback
 */
function success(message, options = {}) {
    const pushOptions = {
        message,
        title: options.title,
        props: {
            showUndo: options.showUndo,
            onUndo: options.onUndo,
            actionLabel: options.actionLabel,
            onAction: options.onAction,
            details: options.details,
            closable: options.closable
        }
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
        props: {
            actionLabel: options.actionLabel,
            onAction: options.onAction,
            details: options.details,
            closable: options.closable
        }
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
        props: {
            actionLabel: options.actionLabel,
            onAction: options.onAction,
            details: options.details,
            closable: options.closable
        }
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
        props: {
            actionLabel: options.actionLabel,
            onAction: options.onAction,
            details: options.details,
            closable: options.closable
        }
    }
    // Only set duration if explicitly provided, otherwise use Notivue's default
    if (options.duration !== undefined) {
        pushOptions.duration = options.duration
    }
    return push.info(pushOptions)
}

/**
 * Show a loading toast that can be resolved or rejected
 * @param {string} message - The loading message
 * @returns {Object} Object with resolve() and reject() methods
 *
 * Usage:
 *   const loading = toast.loading('Saving...')
 *   try {
 *     await save()
 *     loading.resolve('Saved!')
 *   } catch (e) {
 *     loading.reject('Failed to save')
 *   }
 */
function loading(message) {
    const notification = push.promise({
        message,
        props: {
            closable: false
        }
    })

    return {
        /**
         * Resolve the loading toast with a success message
         * @param {string} successMessage - Success message
         * @param {Object} options - Additional options
         */
        resolve(successMessage, options = {}) {
            notification.resolve({
                message: successMessage,
                title: options.title,
                duration: options.duration,
                props: {
                    showUndo: options.showUndo,
                    onUndo: options.onUndo,
                    actionLabel: options.actionLabel,
                    onAction: options.onAction,
                    details: options.details,
                    closable: options.closable
                }
            })
        },

        /**
         * Reject the loading toast with an error message
         * @param {string} errorMessage - Error message
         * @param {Object} options - Additional options
         */
        reject(errorMessage, options = {}) {
            notification.reject({
                message: errorMessage,
                title: options.title,
                duration: options.duration,
                props: {
                    actionLabel: options.actionLabel,
                    onAction: options.onAction,
                    details: options.details,
                    closable: options.closable
                }
            })
        },

        /**
         * Clear the loading toast without showing success/error
         */
        clear() {
            notification.clear()
        }
    }
}

/**
 * Promise-based toast - automatically shows loading, then success or error
 * @param {Promise} promise - The promise to track
 * @param {Object} messages - Messages for each state
 * @param {string} messages.loading - Loading message
 * @param {string} messages.success - Success message
 * @param {string} messages.error - Error message (or function receiving error)
 * @returns {Promise} The original promise result
 *
 * Usage:
 *   const result = await toast.promise(
 *     api.saveData(data),
 *     {
 *       loading: 'Saving...',
 *       success: 'Saved!',
 *       error: (err) => `Failed: ${err.message}`
 *     }
 *   )
 */
async function promise(promiseToTrack, messages) {
    const loadingToast = loading(messages.loading)

    try {
        const result = await promiseToTrack
        const successMsg = typeof messages.success === 'function'
            ? messages.success(result)
            : messages.success
        loadingToast.resolve(successMsg)
        return result
    } catch (err) {
        const errorMsg = typeof messages.error === 'function'
            ? messages.error(err)
            : messages.error
        loadingToast.reject(errorMsg)
        throw err
    }
}

/**
 * Clear all toasts
 */
function clearAll() {
    push.clearAll()
}

// Export as object for composable pattern
export const toast = {
    success,
    error,
    warning,
    info,
    loading,
    promise,
    clearAll
}

// Export as composable function
export function useToast() {
    return toast
}
