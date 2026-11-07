import { i18n } from '../i18n/index.js'

import { getAndroidAppClient } from './androidAppClient.js'

const ABORTED = Object.freeze({ ok: false, aborted: true })
const copy = value => JSON.parse(JSON.stringify(value))

/** A page mirror only. Native owns the socket, arbitration, summary readers, and audio. */
export function createNativeTtsClient({ appClient, document, timers = globalThis, fetchImpl = (...args) => fetch(...args) }) {
    let snapshot = null, attached = false, negotiated = null, attaching = null, attachmentEpoch = -1, timer = null, epoch = 0, operation = 0
    const prefix = Math.random().toString(36).slice(2)
    const pending = new Map(), subscribers = new Set(), pageOperations = new Set()
    function apply(value, refresh = false) {
        if (!value || value.version !== 1 || !Number.isSafeInteger(value.revision)) return
        if (snapshot && (value.instanceId !== snapshot.instanceId || value.revision < snapshot.revision
            || value.revision === snapshot.revision && !refresh)) return
        snapshot = value
        for (const [id, entry] of pending) {
            const terminal = value.results?.find(result => result.operationId === id)
            if (terminal) {
                const start = terminal.start
                if (!entry.started) entry.resolve(start.ok ? { ok: true, ended: entry.ended } : start)
                entry.resolveEnded(terminal.ended)
                pending.delete(id)
            } else if (!entry.started && value.playback?.operationId === id && value.playback.started) {
                entry.started = true
                entry.resolve({ ok: true, ended: entry.ended })
            } else if (entry.accepted && value.playback?.operationId !== id && !['starting', 'reconnecting'].includes(value.sessionStatus)) {
                entry.resolve(ABORTED); entry.resolveEnded(ABORTED); pending.delete(id)
            }
        }
        for (const callback of subscribers) callback(value, refresh)
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
        }, 1000)
    }
    async function visibility() {
        clearPoll()
        if (!attached || document.visibilityState === 'hidden') return
        const mine = epoch
        try { const result = await appClient.request('tts.state'); if (attached && mine === epoch) apply(result?.snapshot, true) } catch { /* Retry on the next visible poll. */ }
        if (mine === epoch) schedule()
    }
    async function command(name, args = {}) {
        const mine = epoch
        try {
            const response = await appClient.request(`tts.${name}`, args)
            if (attached && mine === epoch) apply(response?.snapshot)
            return response
        } catch (error) { return { accepted: false, error: { message: error.message || i18n.global.t('session.speech.errors.unavailable') } } }
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
                let supported = false
                try {
                    supported = await negotiated
                    if (!supported || mine !== epoch) return false
                    let result = await appClient.request('tts.attach')
                    for (let attempt = 0; !result?.snapshot && result?.error?.code === 'initializing' && attempt < 3 && mine === epoch; attempt++) {
                        await new Promise(resolve => timers.setTimeout(resolve, 20))
                        result = await appClient.request('tts.attach')
                    }
                    if (mine !== epoch) return false
                    // Supported native failures never select the Web owner, including a stale document attachment.
                    if (!result?.snapshot) return true
                    attached = true; apply(result.snapshot)
                    document.addEventListener('visibilitychange', visibility)
                    schedule()
                    return true
                } catch { return supported && mine === epoch }
                finally { if (attachmentEpoch === mine) attaching = null }
            })()
            return attaching
        },
        state: () => snapshot,
        handlesOperation: operationId => pageOperations.has(operationId),
        subscribe(callback) { subscribers.add(callback); return () => subscribers.delete(callback) },
        acknowledge: revision => command('acknowledge', { revision }),
        manual(request) {
            if (!attached) return Promise.resolve({ ok: false, error: i18n.global.t('session.speech.errors.unavailable') })
            abortOperations()
            const operationId = `${prefix}-${++operation}`
            pageOperations.add(operationId)
            if (pageOperations.size > 64) pageOperations.delete(pageOperations.values().next().value)
            let resolve, resolveEnded
            const result = new Promise(done => { resolve = done }), ended = new Promise(done => { resolveEnded = done })
            const frozen = copy(request)
            pending.set(operationId, { resolve, ended, resolveEnded, started: false, accepted: false })
            // Invoke the bridge now, before any deferred Web summary reference or network preparation.
            void command('manual', { operationId, request: frozen }).then(acceptance => {
                const entry = pending.get(operationId)
                if (!entry) return
                if (acceptance?.accepted) { entry.accepted = true; return }
                pending.delete(operationId)
                entry.resolve({ ok: false, error: acceptance?.error?.message || i18n.global.t('session.speech.errors.startFailed') })
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
            abortOperations(); subscribers.clear(); pageOperations.clear()
            // No stop or audio DELETE: the service owns accepted work independently.
        },
    }
    return client
}
let client
export function getNativeTtsClient() {
    return client ??= createNativeTtsClient({ appClient: getAndroidAppClient(), document })
}
