import { defineStore } from 'pinia'
import { reactive } from 'vue'

// Only serializable text/metadata enters Pinia. Each store owns its own transports and interests.
const runtimes = new WeakMap()
// Actions can run with a different proxy of the same store as `this` (a caller holding it behind a reactive wrapper),
// so the transports are keyed by the Pinia instance, never by the store object.
function runtime(store) {
    const owner = store._p ?? store
    if (!runtimes.has(owner)) runtimes.set(owner, {
        sequence: 0, runs: new Map(), owners: new Set(), lookups: new Map(), sources: new Map(),
        queued: new Map(), scheduled: false, temporary: null,
    })
    return runtimes.get(owner)
}
export function summarySourceKey(source) {
    if ('event_id' in source) return `event:${source.event_id}`
    return `message:${source.session_id}:${source.line_num}`
}
function variantKey(descriptor) {
    return JSON.stringify([descriptor.session_id, descriptor.source_key, descriptor.source_hash, descriptor.policy_key])
}
function entryFor(source) {
    return reactive({ source: { ...source }, descriptor: null, state: 'running', text: '', revision: 0,
        error: null, engine: null, fallback_reason: null, first_text_ms: null, total_ms: null,
        saved_id: null, result_hash: null, previousPolicy: false, sourceChanged: false })
}
async function checked(response) {
    if (response.ok) return response
    let error
    try { error = (await response.json()).error } catch { /* Use the HTTP status below. */ }
    throw new Error(error || `Summary request failed (${response.status})`)
}
const post = body => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
const cancelled = () => new Error('The summary request was cancelled')

export const useSummariesStore = defineStore('summaries', {
    state: () => ({ descriptors: {}, lookupErrors: {}, lookupVersions: {}, entries: {}, temporary: null }),
    actions: {
        async _fetch(url, options) {
            const { apiFetch } = await import('../utils/api.js')
            return apiFetch(url, options)
        },
        versionFor(source) { return this.lookupVersions[summarySourceKey(source)] || 0 },
        descriptorFor(source) { return this.descriptors[summarySourceKey(source)] ?? null },
        entryFor(source) {
            const descriptor = this.descriptorFor(source)
            if (!descriptor) return null
            return Object.values(this.entries).findLast(entry => entry.descriptor && variantKey(entry.descriptor) === variantKey(descriptor)
                && (entry.state !== 'complete' || entry.result_hash === descriptor.saved?.result_hash)) ?? null
        },
        resolve(sources, { signal } = {}) {
            // Temporary source evidence belongs only to its caller, never the retained message caches.
            if (sources.some(source => 'text' in source)) return this._resolveTemporary(sources, signal)
            const rt = runtime(this)
            const promises = sources.map(source => {
                const key = summarySourceKey(source)
                rt.sources.set(key, { ...source })
                if (this.descriptors[key]) return Promise.resolve(this.descriptors[key])
                if (rt.lookups.has(key)) return rt.lookups.get(key)
                let finish
                const promise = new Promise(resolve => { finish = resolve })
                rt.lookups.set(key, promise)
                rt.queued.set(key, { key, source: { ...source }, version: this.lookupVersions[key] || 0, finish, promise })
                return promise
            })
            if (!rt.scheduled && rt.queued.size) {
                rt.scheduled = true
                queueMicrotask(() => this._flushLookups())
            }
            return Promise.all(promises)
        },
        async _resolveTemporary(sources, signal) {
            const response = await checked(await this._fetch('/api/summaries/resolve/', { ...post({ sources }), signal }))
            const results = (await response.json()).sources
            if (!Array.isArray(results) || results.length !== sources.length) throw new Error('Invalid summary lookup response')
            return results
        },
        async _flushLookups() {
            const rt = runtime(this), queued = [...rt.queued.values()]
            rt.queued.clear(); rt.scheduled = false
            const chunks = []
            for (let i = 0; i < queued.length; i += 100) chunks.push(queued.slice(i, i + 100))
            await Promise.all(chunks.map(async chunk => {
                let results
                try {
                    const response = await checked(await this._fetch('/api/summaries/resolve/', post({ sources: chunk.map(item => item.source) })))
                    results = (await response.json()).sources
                    if (!Array.isArray(results) || results.length !== chunk.length) throw new Error('Invalid summary lookup response')
                } catch (error) { results = chunk.map(() => ({ error: error.message || 'The summary lookup failed' })) }
                chunk.forEach((item, index) => {
                    const current = (this.lookupVersions[item.key] || 0) === item.version
                    const result = current ? results[index] : null
                    if (current) {
                        this.descriptors[item.key] = result?.error ? null : result
                        this.lookupErrors[item.key] = result?.error || null
                    }
                    if (rt.lookups.get(item.key) === item.promise) rt.lookups.delete(item.key)
                    item.finish(result)
                })
            }))
        },
        request(source, { manual = true, regenerate = false, start = true } = {}) {
            const rt = runtime(this), temporary = 'text' in source
            const subscription = reactive({ entry: entryFor(source) })
            let resolveReady, rejectReady
            const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject })
            // A visible entry records errors even if its view never awaits acquisition.
            ready.catch(() => {})
            const owner = { subscription, controller: null, active: true, attached: true,
                manual: manual && !temporary,
                intentKey: temporary ? null : JSON.stringify([summarySourceKey(source), this.versionFor(source)]),
                resolveReady, rejectReady }
            rt.owners.add(owner)
            const publicSubscription = {
                get id() { return owner.controller?.id ?? null },
                get entry() { return subscription.entry }, ready,
                release: () => {
                    owner.attached = false
                    if (!owner.manual || subscription.entry.state !== 'running') this._releaseOwner(owner)
                },
            }
            const begin = async () => {
                try {
                    let descriptor = null
                    if (!temporary) {
                        ;[descriptor] = await this.resolve([source])
                        if (!descriptor) throw new Error('The message changed. Try again.')
                        if (descriptor.error) throw new Error(descriptor.error)
                    }
                    if (!owner.active) throw cancelled()
                    const key = temporary ? `temporary:${++rt.sequence}` : variantKey(descriptor)
                    let controller = rt.runs.get(key)
                    if (!controller || controller.stopped || (controller.entry.state !== 'running' && (regenerate || controller.entry.state !== 'complete'
                        || controller.entry.result_hash !== descriptor?.saved?.result_hash))) {
                        controller = { key, entry: subscription.entry, id: null, owners: new Set(), stopped: false, abort: new AbortController() }
                        controller.entry.descriptor = descriptor
                        rt.runs.set(key, controller)
                        if (!temporary) this.entries[++rt.sequence] = controller.entry
                        controller.ready = this._acquire(controller, source, { regenerate, start })
                        controller.ready.catch(() => {})
                    }
                    owner.controller = controller
                    controller.owners.add(owner)
                    subscription.entry = controller.entry
                    await controller.ready
                    if (!owner.active) throw cancelled()
                    resolveReady(publicSubscription)
                } catch (error) {
                    if (owner.active) {
                        subscription.entry.state = 'failed'
                        subscription.entry.error = error.message || 'The summary request failed'
                    }
                    rejectReady(error)
                    if (!owner.attached) this._releaseOwner(owner)
                }
            }
            // Owners attach before acquisition can resume after its network await.
            begin()
            return publicSubscription
        },
        async _acquire(controller, source, options) {
            const rt = runtime(this)
            try {
                // Defer until request() has attached its first independent owner.
                await Promise.resolve()
                const response = await checked(await this._fetch('/api/summaries/', { ...post({ source, ...options }), signal: controller.abort.signal }))
                const opened = await response.json()
                controller.id = opened.id
                if (controller.stopped) { await this._deleteHandle(opened.id); throw cancelled() }
                const descriptor = opened.descriptor
                controller.entry.descriptor = descriptor
                if (!('text' in source)) {
                    const lookupKey = summarySourceKey(source), current = this.descriptors[lookupKey]
                    if (current && variantKey(current) !== variantKey(descriptor)) {
                        this.descriptors[lookupKey] = null
                        this.lookupVersions[lookupKey] = (this.lookupVersions[lookupKey] || 0) + 1
                    }
                }
                // Acquire resolves the source again. Never label its body with lookup metadata from an older source.
                if (!('text' in source) && descriptor?.source_key) {
                    const actualKey = variantKey(descriptor), existing = rt.runs.get(actualKey)
                    if (actualKey !== controller.key && existing && existing !== controller && !existing.stopped) {
                        for (const owner of controller.owners) {
                            owner.controller = existing; owner.subscription.entry = existing.entry; existing.owners.add(owner)
                        }
                        controller.owners.clear(); controller.stopped = true
                        if (rt.runs.get(controller.key) === controller) rt.runs.delete(controller.key)
                        await this._deleteHandle(opened.id)
                        await existing.ready
                        return
                    }
                    if (rt.runs.get(controller.key) === controller) rt.runs.delete(controller.key)
                    controller.key = actualKey; rt.runs.set(actualKey, controller)
                }
                this._read(controller).catch(() => {})
            } catch (error) {
                if (!controller.stopped) {
                    controller.entry.state = 'failed'; controller.entry.error = error.message || 'The summary request failed'
                    this._finishDetached(controller)
                }
                throw error
            }
        },
        async _read(controller) {
            let reader
            try {
                const response = await checked(await this._fetch(`/api/summaries/${controller.id}/`, { signal: controller.abort.signal }))
                if (controller.stopped) return
                reader = response.body.getReader()
                controller.reader = reader
                const decoder = new TextDecoder(), entry = controller.entry
                let pending = '', terminal = false
                const consume = line => {
                    if (!line.trim() || controller.stopped) return
                    const snapshot = JSON.parse(line)
                    if (!['running', 'complete', 'failed', 'cancelled'].includes(snapshot.state)) throw new Error('Invalid summary stream')
                    if (snapshot.revision < entry.revision) return
                    // Every record is a complete snapshot, including fallback replacements and exact body hashes.
                    for (const field of ['revision', 'state', 'text', 'engine', 'fallback_reason', 'first_text_ms', 'total_ms', 'error', 'saved_id', 'result_hash']) entry[field] = snapshot[field] ?? (field === 'text' ? '' : null)
                    terminal = entry.state !== 'running'
                }
                while (!controller.stopped && !terminal) {
                    const { value, done } = await reader.read()
                    pending += decoder.decode(value, { stream: !done })
                    let newline
                    while ((newline = pending.indexOf('\n')) !== -1) { consume(pending.slice(0, newline)); pending = pending.slice(newline + 1) }
                    if (done) { if (pending) consume(pending); break }
                }
                if (!controller.stopped && !terminal) throw new Error('The summary stream stopped before the end')
                this._finishDetached(controller)
            } catch (error) {
                if (!controller.stopped) {
                    controller.entry.state = 'failed'; controller.entry.error = error.message || 'The summary stream failed'
                    this._finishDetached(controller)
                    // A broken network stream cannot retain a public backend handle.
                    await this._deleteHandle(controller.id)
                }
            } finally {
                try { await reader?.cancel() } catch { /* A failed stream is already visible above. */ }
                reader?.releaseLock()
            }
        },
        _finishDetached(controller) {
            for (const owner of [...controller.owners]) if (!owner.attached) this._releaseOwner(owner)
        },
        _releaseOwner(owner) {
            if (!owner.active) return
            owner.active = false
            runtime(this).owners.delete(owner)
            const controller = owner.controller
            owner.rejectReady(cancelled())
            if (!controller) {
                if (owner.subscription.entry.state === 'running') owner.subscription.entry.state = 'cancelled'
                return
            }
            controller.owners.delete(owner)
            if (controller.owners.size) return
            controller.stopped = true
            controller.abort.abort()
            controller.reader?.cancel().catch(() => {})
            if ('text' in controller.entry.source) {
                controller.entry.text = ''
                controller.entry.source.text = ''
            }
            const rt = runtime(this)
            if (rt.runs.get(controller.key) === controller) rt.runs.delete(controller.key)
            if (controller.entry.state === 'running') controller.entry.state = 'cancelled'
            if (controller.id) this._deleteHandle(controller.id)
        },
        async _deleteHandle(id) {
            if (!id) return
            try { await this._fetch(`/api/summaries/${id}/`, { method: 'DELETE' }) } catch { /* Idempotent best effort; the backend bounds abandoned handles. */ }
        },
        cancel(entry) {
            const owners = [...runtime(this).owners]
            // Equivalent coordinates share manual intent before their descriptor establishes native identity.
            const intents = new Set(owners.filter(owner => owner.manual && owner.subscription.entry === entry).map(owner => owner.intentKey))
            for (const owner of owners) {
                if (owner.manual && (owner.subscription.entry === entry || (!owner.controller && intents.has(owner.intentKey)))) this._releaseOwner(owner)
            }
        },
        async loadSaved(savedId) {
            const response = await checked(await this._fetch(`/api/summaries/saved/${savedId}/`))
            // Body and fingerprint come from one row read. Never combine this with lookup metadata.
            return { ...(await response.json()), state: 'complete', previousPolicy: false }
        },
        invalidate(message) {
            const rt = runtime(this)
            const sourceEvents = new Set(['session_items_added', 'session_updated', 'session_removed', 'process_state'])
            const policyChange = message.type === 'synced_settings_updated'
                && ['summaryModel', 'disabledProviders'].some(key => key in (message.settings || {}))
            if (!sourceEvents.has(message.type) && message.type !== 'summary_saved' && !policyChange) return
            const sessionId = message.session_id ?? message.session?.id
            for (const [key, source] of rt.sources) {
                const descriptor = this.descriptors[key]
                const matches = policyChange || (message.type === 'summary_saved'
                    ? !descriptor || (descriptor.source_key === message.source_key && descriptor.source_hash === message.source_hash)
                    : !('event_id' in source) && source.session_id === sessionId)
                if (!matches) continue
                this.descriptors[key] = null; this.lookupErrors[key] = null
                rt.lookups.delete(key)
                const queued = rt.queued.get(key)
                if (queued) { rt.queued.delete(key); queued.finish(null) }
                this.lookupVersions[key] = (this.lookupVersions[key] || 0) + 1
            }
            if (sourceEvents.has(message.type)) for (const controller of rt.runs.values()) {
                if (!('text' in controller.entry.source) && !('event_id' in controller.entry.source) && controller.entry.source.session_id === sessionId) controller.entry.sourceChanged = true
            }
            // Active views and queued listeners watch versionFor(source) and resolve on demand.
            // Frozen entries retain their original descriptor/body/hash for deliberate snapshot playback.
        },
        openTemporary(text, purpose = 'selection') {
            this.closeTemporary()
            const rt = runtime(this), source = { text: String(text), purpose }
            const subscription = this.request(source, { manual: false })
            rt.temporary = subscription
            this.temporary = { id: `selection:${++rt.sequence}`, source, entry: subscription.entry }
            return subscription
        },
        retryTemporary() {
            if (!this.temporary) return
            const { text, purpose } = this.temporary.source
            return this.openTemporary(text, purpose)
        },
        temporaryReference() {
            const subscription = runtime(this).temporary
            if (!subscription) return Promise.reject(cancelled())
            const reference = subscription.ready.then(() => ({ id: subscription.id }))
            reference.catch(() => {})
            return reference
        },
        cancelTemporary() {
            if (!this.temporary) return
            // Release the transport, but keep this panel's captured source and visible partial text.
            const entry = { ...this.temporary.entry, source: { ...this.temporary.entry.source } }
            const rt = runtime(this)
            rt.temporary?.release(); rt.temporary = null
            if (entry.state === 'running') entry.state = 'cancelled'
            this.temporary.entry = entry
        },
        closeTemporary() {
            const rt = runtime(this)
            rt.temporary?.release(); rt.temporary = null
            if (this.temporary) {
                this.temporary.entry.text = ''; this.temporary.entry.source.text = ''
                this.temporary.source.text = ''
            }
            this.temporary = null
        },
    },
})
