import { toLocalMessageDescriptor } from './messageDescriptors.js'
import { localUiError } from './uiMessages.js'

// Wire metadata is public data. Convert it to the private render-time descriptor;
// never translate on receipt because errors and notifications can remain visible.
export function remoteUiMessage(payload, fallback = payload?.error ?? payload?.message ?? '', field = 'ui_message') {
    const message = payload?.[field] ?? (field === 'ui_message' ? payload?.error_ui_message : undefined)
    return toLocalMessageDescriptor(message) ?? fallback
}

export function remoteUiError(payload, fallback = payload?.error ?? payload?.message ?? '') {
    const message = remoteUiMessage(payload, payload?.error ?? payload?.message ?? fallback)
    const error = message?.type === 'local-ui-message'
        ? localUiError(message.key, message.params, message.count)
        : new Error(message)
    if (payload?.code !== undefined) error.code = payload.code
    if (payload?.status !== undefined) error.status = payload.status
    return error
}

// Resolve separate validation messages together at render time.
export function remoteUiErrors(payload, fallback) {
    if (Array.isArray(payload?.errors) && payload.errors.length) {
        return { type: 'local-ui-message-list', messages: payload.errors.map(error => remoteUiMessage(error, error.message || error.code)) }
    }
    return remoteUiMessage(payload, payload?.error || payload?.reason || fallback)
}
