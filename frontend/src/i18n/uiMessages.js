import { i18n } from './index.js'

// Store local UI messages without freezing their language. Never send these
// descriptors to a provider or the backend. Keep external prose as plain text.
export function localUiMessage(key, params = {}, count) {
    return { type: 'local-ui-message', key, params, ...(count === undefined ? {} : { count }) }
}

export function resolveUiMessage(message, composer = i18n.global) {
    if (message?.type !== 'local-ui-message') return message
    return composer.t(message.key, message.params, message.count === undefined ? {} : message.count)
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
