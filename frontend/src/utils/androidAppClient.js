const RESPONSE_CHANNEL = 'twicc.android.response'
const REQUEST_CHANNEL = 'twicc.android.request'

/** The page uses one client for either native transport. No user-agent detection. */
export function createAndroidAppClient(target, { timeoutMs = 1500 } = {}) {
    const pending = new Map()
    const prefix = Math.random().toString(36).slice(2)
    let sequence = 0, closed = false
    const proxy = target.TwiccAndroid
    function receive(response) {
        if (!response || response.protocol !== 1 || typeof response.id !== 'string') return
        const entry = pending.get(response.id)
        if (!entry || typeof response.ok !== 'boolean') return
        pending.delete(response.id)
        clearTimeout(entry.timer)
        if (response.ok) entry.resolve(response.state ?? null)
        else entry.reject(new Error(typeof response.error === 'string' ? response.error : 'Android command failed.'))
    }
    function fromWindow(event) {
        if (event.source !== target || event.origin !== target.location.origin) return
        if (event.data?.channel === RESPONSE_CHANNEL) receive(event.data.response)
    }
    function fromProxy(event) {
        try { receive(JSON.parse(event.data)) } catch { /* Ignore malformed transport messages. */ }
    }
    target.addEventListener('message', fromWindow)
    if (proxy) proxy.onmessage = fromProxy
    return {
        request(command, args = {}) {
            if (closed) return Promise.reject(new Error('Android client is closed.'))
            const id = `${prefix}-${++sequence}`
            const request = { ...args, protocol: 1, id, command }
            return new Promise((resolve, reject) => {
                const timer = setTimeout(() => {
                    pending.delete(id)
                    reject(new Error('Android app is unavailable or the request timed out.'))
                }, timeoutMs)
                pending.set(id, { resolve, reject, timer })
                try {
                    if (proxy) proxy.postMessage(JSON.stringify(request))
                    else target.postMessage({ channel: REQUEST_CHANNEL, request }, target.location.origin)
                } catch (error) {
                    clearTimeout(timer)
                    pending.delete(id)
                    reject(error)
                }
            })
        },
        close() {
            closed = true
            target.removeEventListener('message', fromWindow)
            if (proxy?.onmessage === fromProxy) proxy.onmessage = null
            for (const entry of pending.values()) { clearTimeout(entry.timer); entry.reject(new Error('Android client is closed.')) }
            pending.clear()
        },
    }
}

let client
export function getAndroidAppClient() {
    return client ??= createAndroidAppClient(window)
}
