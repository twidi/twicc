import { i18n } from './index.js'
import { resolveUiMessageWithComposer } from './uiMessageRenderer.js'

// Store local UI messages without freezing their language. Never send these
// descriptors to a provider or the backend. Keep external prose as plain text.
export function localUiMessage(key, params = {}, count) {
    return { type: 'local-ui-message', key, params, ...(count === undefined ? {} : { count }) }
}

// Application messages always use the current global language.
export function resolveUiMessage(message) {
    return resolveUiMessageWithComposer(message, i18n.global)
}

// Keep the Error contract for callers and retain local message identity for caches.
export function localUiError(key, params = {}, count) {
    const uiMessage = localUiMessage(key, params, count)
    const error = new Error(resolveUiMessage(uiMessage))
    Object.defineProperty(error, 'message', {
        configurable: true,
        get: () => resolveUiMessage(uiMessage),
    })
    error.uiMessage = uiMessage
    return error
}
