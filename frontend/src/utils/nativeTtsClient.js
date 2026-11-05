import { getAndroidAppClient } from './androidAppClient.js'

const ABORTED = Object.freeze({ ok: false, aborted: true })
const copy = value => JSON.parse(JSON.stringify(value))

/** A page mirror only. Native owns the socket, arbitration, summary readers, and audio. */
export function createNativeTtsClient({ appClient, document, timers = globalThis, fetchImpl = (...args) => fetch(...args) }) {
    let snapshot = null, attached = false, negotiated = null, attaching = null, attachmentEpoch = -1, timer = null, epoch = 0, operation = 0
    const prefix = Math.random().toString(36).slice(2)
    const pending = new Map(), subscribers = new Set()
    function apply(value) {
        if (!value || value.version !== 1 || !Number.isSafeInteger(value.revision)) return
        if (snapshot && (value.instanceId !== snapshot.instanceId || value.revision <= snapshot.revision)) return
        snapshot = value
        for (const [id, entry] of pending) {
            const terminal = value.results?.find(result => result.operationId === id)
            if (terminal) {
                let start = terminal.start
                if (!start.ok && value.error?.summary_changed && entry.request.summary?.saved_id === value.error.saved_id) {
                    start = { ...start, summaryChanged: true, savedId: value.error.saved_id }
                }
                if (!entry.started) entry.resolve(start.ok ? { ok: true, ended: entry.ended } : start)
                entry.resolveEnded(terminal.ended)
                pending.delete(id)
            } else if (!entry.started && value.playback?.operationId === id && value.playback.started) {
                entry.started = true
                entry.resolve({ ok: true, ended: entry.ended })
            }
        }
        for (const callback of subscribers) callback(value)
    }
    function clearPoll() { if (timer !== null) timers.clearTimeout(timer); timer = null }
    function schedule() {
        clearPoll()
        if (!attached || document.visibilityState === 'hidden') return
        const mine = epoch
        timer = timers.setTimeout(async () => {
            timer = null
            try { const response = await appClient.request('tts.state'); if (attached && mine === epoch) apply(response?.snapshot) } catch { /* Reattach on the next visible poll. */ }
            if (mine === epoch) schedule()
        }, 400)
    }
    function visibility() { schedule() }
    async function command(name, args = {}) {
        const mine = epoch
        try {
            const response = await appClient.request(`tts.${name}`, args)
            if (attached && mine === epoch) apply(response?.snapshot)
            return response
        } catch (error) { return { accepted: false, error: { message: error.message || 'Native speech is unavailable' } } }
    }
    function abortOperations(predicate = () => true) {
        for (const [id, entry] of pending) if (predicate(id)) {
            entry.resolve(ABORTED); entry.resolveEnded(ABORTED); pending.delete(id)
        }
    }
    const client = {
        attach() {
            if (attached) return Promise.resolve(true)
            if (attaching && attachmentEpoch === epoch) return attaching
            const mine = epoch
            attachmentEpoch = mine
            negotiated ??= (async () => {
                try {
                    const app = await appClient.request('getState')
                    if (app?.capabilities?.nativeTts !== 1) return false
                    const response = await fetchImpl('/api/tts/native/capabilities/')
                    if (!response.ok) return false
                    const server = await response.json()
                    return server.protocol === 1 && server.available === true
                } catch { return false }
            })()
            attaching = (async () => {
                try {
                    if (!await negotiated || mine !== epoch) return false
                    const result = await appClient.request('tts.attach')
                    if (!result?.snapshot || mine !== epoch) return false
                    attached = true; apply(result.snapshot)
                    document.addEventListener('visibilitychange', visibility)
                    schedule()
                    return true
                } catch { return false }
                finally { if (attachmentEpoch === mine) attaching = null }
            })()
            return attaching
        },
        state: () => snapshot,
        subscribe(callback) { subscribers.add(callback); return () => subscribers.delete(callback) },
        manual(request) {
            if (!attached) return Promise.resolve({ ok: false, error: 'Native speech is unavailable' })
            abortOperations()
            const operationId = `${prefix}-${++operation}`
            let resolve, resolveEnded
            const result = new Promise(done => { resolve = done }), ended = new Promise(done => { resolveEnded = done })
            const frozen = copy(request)
            pending.set(operationId, { request: frozen, resolve, ended, resolveEnded, started: false })
            // Invoke the bridge now, before any deferred Web summary reference or network preparation.
            void command('manual', { operationId, request: frozen }).then(acceptance => {
                const entry = pending.get(operationId)
                if (!entry || acceptance?.accepted) return
                pending.delete(operationId)
                entry.resolve({ ok: false, error: acceptance?.error?.message || 'Native speech could not start' })
                entry.resolveEnded(ABORTED)
            })
            return Object.assign(result, { operationId })
        },
        listen: activation => command('listen', copy(activation)),
        setMuted: includeMuted => command('set_muted', { activationId: snapshot?.listening?.activationId, includeMuted }),
        disable: () => command('disable', { activationId: snapshot?.listening?.activationId }),
        skip(readingId, operationId) {
            const target = operationId ?? (readingId ? snapshot?.playback?.operationId : null)
            abortOperations(id => !readingId && !operationId || id === target)
            return command('skip', readingId ? { readingId, identity: snapshot?.playback?.identity ?? null }
                : operationId ? { operationId } : {})
        },
        stopAll() { abortOperations(); return command('stop_all') },
        detach() {
            attached = false; epoch++; clearPoll()
            document.removeEventListener('visibilitychange', visibility)
            abortOperations(); subscribers.clear()
            // No stop or audio DELETE: the service owns accepted work independently.
        },
    }
    return client
}
let client
export function getNativeTtsClient() {
    return client ??= createNativeTtsClient({ appClient: getAndroidAppClient(), document })
}
