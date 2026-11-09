import manifest from '../../../android/i18n/messages.json' with { type: 'json' }
import { formatMessageParams } from './messageDescriptors.js'

// Internal renderer. Tests can use independent composers without changing app state.
export function resolveUiMessageWithComposer(message, composer) {
    if (message?.type === 'local-ui-message-list') {
        return message.messages.map(item => resolveUiMessageWithComposer(item, composer)).join(' — ')
    }
    if (message?.type !== 'local-ui-message') return message
    const params = Object.fromEntries(Object.entries(message.params || {}).map(([key, value]) => [
        key, value?.type === 'local-ui-message' || value?.type === 'local-ui-message-list'
            ? resolveUiMessageWithComposer(value, composer) : value,
    ]))
    const count = typeof params.count === 'number' ? params.count
        : typeof params.n === 'number' ? params.n : message.count
    for (const name of ['count', 'n']) {
        if (params[name] == null && message.count !== undefined && manifest.messages[message.key]?.numberFormats?.[name]) {
            params[name] = message.count
        }
    }
    const formatted = formatMessageParams(message.key, params, composer.locale.value)
    // Vue prioritizes numeric aliases over its plural argument. Keep their display
    // values as text so the original numeric choice remains authoritative.
    for (const name of ['count', 'n']) {
        if (typeof formatted[name] === 'number') formatted[name] = String(formatted[name])
    }
    return composer.t(message.key, formatted, count === undefined ? {} : count)
}

