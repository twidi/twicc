import { localUiError } from '../i18n/uiMessages.js'
import { remoteUiError } from '../i18n/remoteMessages.js'
import { markAndroidApp } from './androidEnvironment.js'
import { setAndroidTelemetry } from './telemetryClient.js'

const RESPONSE_CHANNEL = 'twicc.android.response'
const REQUEST_CHANNEL = 'twicc.android.request'
const TTS_COMMANDS = new Set(['tts.attach', 'tts.state', 'tts.acknowledge', 'tts.manual', 'tts.listen', 'tts.set_muted', 'tts.disable', 'tts.skip', 'tts.stop_all'])
const DOCUMENT_COMMANDS = new Set([...TTS_COMMANDS, 'locale.set'])

/** The page uses one client for either native transport. No user-agent detection. */
export function createAndroidAppClient(target, { timeoutMs = 1500 } = {}) {
    const pending = new Map()
    const stateListeners = new Set()
    const prefix = Math.random().toString(36).slice(2)
    let sequence = 0, closed = false, documentToken = null
    const proxy = typeof target.TwiccAndroid?.postMessage === 'function' ? target.TwiccAndroid : null
    if (proxy) markAndroidApp()
    function receive(response) {
        if (closed) return
        if (response?.protocol === 1 && response.event === 'stateChanged') {
            for (const listener of stateListeners) listener()
            return
        }
        if (!response || response.protocol !== 1 || typeof response.id !== 'string') return
        const entry = pending.get(response.id)
        if (!entry || typeof response.ok !== 'boolean') return
        markAndroidApp()
        pending.delete(response.id)
        clearTimeout(entry.timer)
        if (response.ok) {
            if (entry.command === 'getState') setAndroidTelemetry(response.state)
            entry.resolve(response.state ?? null)
        }
        else {
            const error = response.ui_message || typeof response.error === 'string'
                ? remoteUiError(response, localUiError('common.android.errors.actionFailed').message)
                : localUiError('common.android.errors.actionFailed')
            if (response.state !== undefined) error.state = response.state
            entry.reject(error)
        }
    }
    function fromWindow(event) {
        if (event.source !== target || event.origin !== target.location.origin) return
        if (event.data?.channel === RESPONSE_CHANNEL) receive(event.data.response)
        if (event.data?.channel === 'twicc.android.environment' && event.data.protocol === 1
            && typeof event.data.id === 'string' && pending.get(event.data.id)?.command === 'getState') markAndroidApp()
    }
    function fromProxy(event) {
        try { receive(JSON.parse(event.data)) } catch { /* Ignore malformed transport messages. */ }
    }
    target.addEventListener('message', fromWindow)
    if (proxy) proxy.onmessage = fromProxy
    return {
        onStateChanged(listener) {
            if (!closed) stateListeners.add(listener)
            return () => stateListeners.delete(listener)
        },
        async request(command, args = {}) {
            if (closed) return Promise.reject(localUiError('common.android.errors.closed'))
            if (proxy && DOCUMENT_COMMANDS.has(command)) {
                const deadline = Date.now() + timeoutMs
                while (!documentToken && !closed) {
                    const token = target.__twiccNativeTtsDocument
                    if (typeof token === 'string' && token) { documentToken = token; break }
                    if (Date.now() >= deadline) throw localUiError(command === 'locale.set'
                        ? 'android.language.errors.staleDocument' : 'common.android.errors.speechPreparing')
                    await new Promise(resolve => setTimeout(resolve, 10))
                }
                if (closed) throw localUiError('common.android.errors.closed')
            }
            const id = `${prefix}-${++sequence}`
            const request = { ...args, ...(documentToken && DOCUMENT_COMMANDS.has(command) ? { documentToken } : {}), protocol: 1, id, command }
            let encoded
            try {
                encoded = JSON.stringify(request)
                const limit = TTS_COMMANDS.has(command) ? 12 * 1024 * 1024 : 16 * 1024
                if (new TextEncoder().encode(encoded).byteLength > limit) return Promise.reject(localUiError('common.android.errors.tooLarge'))
            } catch (error) {
                console.warn('Android request serialization failed', error)
                return Promise.reject(localUiError('common.android.errors.actionFailed'))
            }
            return new Promise((resolve, reject) => {
                const timer = setTimeout(() => {
                    pending.delete(id)
                    reject(localUiError('common.android.errors.unreachable'))
                }, timeoutMs)
                pending.set(id, { resolve, reject, timer, command })
                try {
                    if (proxy) proxy.postMessage(encoded)
                    else target.postMessage({ channel: REQUEST_CHANNEL, request }, target.location.origin)
                } catch (error) {
                    clearTimeout(timer)
                    pending.delete(id)
                    console.warn('Android request transport failed', error)
                    reject(localUiError('common.android.errors.unreachable'))
                }
            })
        },
        close() {
            closed = true
            stateListeners.clear()
            target.removeEventListener('message', fromWindow)
            if (proxy?.onmessage === fromProxy) proxy.onmessage = null
            for (const entry of pending.values()) { clearTimeout(entry.timer); entry.reject(localUiError('common.android.errors.closed')) }
            pending.clear()
        },
    }
}

let client
export function getAndroidAppClient() {
    return client ??= createAndroidAppClient(window)
}
