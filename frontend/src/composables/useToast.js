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
 *
 *   // Rich content with a Vue component:
 *   import MyContent from '@/components/MyContent.vue'
 *   toast.custom(MyContent, {
 *       type: 'success',
 *       title: 'Upload Complete',
 *       props: { fileName: 'report.pdf', size: '2.3 MB' },
 *   })
 *
 *   // Rich content with raw HTML:
 *   toast.custom({
 *       type: 'info',
 *       title: 'Details',
 *       html: '<strong>3 files</strong> uploaded successfully',
 *   })
 */

import { markRaw } from 'vue'
import { push } from 'notivue'
import SessionToastContent from '../components/SessionToastContent.vue'

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

/**
 * Show a toast with rich/custom content using CustomNotification.
 *
 * Two calling signatures:
 *
 * 1) With a Vue component for the content area:
 *    toast.custom(MyComponent, { type: 'success', title: '...', props: { ... } })
 *
 * 2) With raw HTML (no component):
 *    toast.custom({ type: 'info', title: '...', html: '<b>bold</b> text' })
 *
 * @param {Object|Object} componentOrOptions - A Vue component, or options if no component
 * @param {Object} [options] - Options (only when first arg is a component)
 * @param {string} [options.type='info'] - Notification type: 'success', 'error', 'warning', 'info'
 * @param {string} [options.title] - Optional title
 * @param {string} [options.html] - Raw HTML content (alternative to component)
 * @param {Object} [options.props] - Props to pass to the content component
 * @param {number} [options.duration] - Duration in ms
 */
function custom(componentOrOptions, options = {}) {
    // Support two signatures: custom(Component, opts) or custom(opts)
    let component = null
    let opts = options
    if (componentOrOptions && (componentOrOptions.__name || componentOrOptions.setup || componentOrOptions.render || componentOrOptions.template)) {
        // First arg is a Vue component
        component = componentOrOptions
    } else {
        // First arg is the options object
        opts = componentOrOptions || {}
    }

    const type = opts.type || 'info'
    const pushFn = push[type]
    if (!pushFn) {
        console.warn(`[useToast] Unknown notification type "${type}", falling back to "info"`)
        return push.info({ message: '', title: opts.title })
    }

    const pushOptions = {
        message: '',
        title: opts.title,
        props: {
            custom: true,
            ...(component ? { content: markRaw(component), contentProps: opts.props || {} } : {}),
            ...(opts.html ? { html: opts.html } : {}),
            ...(opts.style ? { style: opts.style } : {}),
        },
    }

    if (opts.duration !== undefined) {
        pushOptions.duration = opts.duration
    }

    return pushFn(pushOptions)
}

/**
 * Show a session-related toast with project badge and session title.
 *
 * Usage:
 *   toast.session(sessionId, { type: 'success', title: 'Claude Code started' })
 *   toast.session(sessionId, { type: 'error', title: 'Error', errorMessage: 'Something broke' })
 *
 * @param {string} sessionId - The session ID
 * @param {Object} options
 * @param {string} [options.type='info'] - Notification type: 'success', 'error', 'warning', 'info'
 * @param {string} [options.title] - Toast title (header)
 * @param {string} [options.errorMessage] - Optional error message to display below session title
 * @param {number} [options.duration] - Duration in ms
 */
function session(sessionId, options = {}) {
    return custom(SessionToastContent, {
        type: options.type || 'info',
        title: options.title,
        duration: options.duration,
        props: {
            sessionId,
            ...(options.errorMessage ? { errorMessage: options.errorMessage } : {}),
        },
    })
}

// Export as object for composable pattern
export const toast = {
    success,
    error,
    warning,
    info,
    custom,
    session,
}

// Export as composable function
export function useToast() {
    return toast
}
