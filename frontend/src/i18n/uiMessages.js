import { i18n } from './index.js'
import manifest from '../../../android/i18n/messages.json' with { type: 'json' }
import { formatMessageParams } from './messageDescriptors.js'

// Store local UI messages without freezing their language. Never send these
// descriptors to a provider or the backend. Keep external prose as plain text.
export function localUiMessage(key, params = {}, count) {
    return { type: 'local-ui-message', key, params, ...(count === undefined ? {} : { count }) }
}

export function resolveUiMessage(message, composer = i18n.global) {
    if (message?.type === 'local-ui-message-list') {
        return message.messages.map(item => resolveUiMessage(item, composer)).join(' — ')
    }
    if (message?.type !== 'local-ui-message') return message
    const params = Object.fromEntries(Object.entries(message.params || {}).map(([key, value]) => [
        key, value?.type === 'local-ui-message' || value?.type === 'local-ui-message-list'
            ? resolveUiMessage(value, composer) : value,
    ]))
    const count = typeof params.count === 'number' ? params.count
        : typeof params.n === 'number' ? params.n : message.count
    for (const name of ['count', 'n']) {
        if (params[name] == null && message.count !== undefined && manifest.messages[message.key]?.numberFormats?.[name]) {
            params[name] = message.count
        }
    }
    return composer.t(message.key, formatMessageParams(message.key, params, composer.locale.value), count === undefined ? {} : count)
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
