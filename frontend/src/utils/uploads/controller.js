// frontend/src/utils/uploads/controller.js
// The upload lifecycle controller (spec §6.2–§6.5, §6.7–§6.9, §6.12–§6.13).
//
// It holds the entries and runs every lifecycle rule. It is free of Pinia,
// tus-js-client, notivue, `window` and the auth store: every outside effect
// comes from an injected dependency, so node:test drives it with fakes. The
// Pinia store (`stores/uploads.js`) builds it with the real dependencies.

import { computed, markRaw, reactive, ref } from 'vue'
import { entryActions as computeEntryActions } from './display.js'
import { fingerprintFile, makeClientId } from './ids.js'
import { baseName } from './paths.js'
import {
    computeStatusByOrigin,
    findEntryForRecord,
    isStalled as isStalledRule,
    isTerminal,
    originKey,
    rule7Action,
    selectNextToPromote,
} from './rules.js'
import {
    CHUNK_SIZE,
    CREATION_TIMEOUT_MS,
    RETRY_DELAYS,
    TUS_VERSION,
    classifyCreationAnswer,
    classifyTusError,
    isUploadAnswer,
    statusMessage,
    tusShouldRetry,
    uploadUrl,
} from './transport.js'

/** Duration of every upload toast. */
export const TOAST_DURATION_MS = 15_000

/** Period of the timer that refreshes `now` for the stalled rule. */
export const STALLED_TICK_MS = 15_000

/** Period of the finalization probe timer. */
export const PROBE_TICK_MS = 15_000

/** A `finalizing` record this old (and no probe this recent) is probed. */
export const PROBE_AFTER_MS = 60_000

/** Minimum interval between two `sentBytes` updates (4 per second). */
export const PROGRESS_THROTTLE_MS = 250

const PANEL_LABELS = { files: 'Files', artifacts: 'Artifacts' }

/** Local states for which leaving the page loses work. */
const UNLOAD_STATES = ['queued', 'creating', 'sending', 'paused']

/**
 * True when a `DELETE` answer means the upload is gone for good: `2xx`,
 * `404` or `410`.
 *
 * @param {number} status - 0 for no answer
 * @returns {boolean}
 */
function isDeleteDone(status) {
    return (status >= 200 && status < 300) || status === 404 || status === 410
}

/**
 * Create the upload lifecycle controller.
 *
 * @param {object} deps
 * @param {(url: string, options?: object) => Promise<Response>} deps.apiFetch
 * @param {(file: Blob, options: object) => {start(): void, abort(): unknown}} deps.createTusUpload
 *     - the tus `Upload` factory (`(file, options) => new tus.Upload(file, options)`)
 * @param {{success: Function, error: Function}} deps.toast
 * @param {() => number} deps.now - client clock, in ms
 * @param {Function} deps.setTimeout
 * @param {Function} deps.clearTimeout
 * @param {string} deps.tabId
 * @param {(n: number) => string} deps.randomHex
 * @param {() => boolean} deps.isAuthenticated
 * @param {() => void} deps.onUnauthorized - the shared 401 helper of `utils/api.js`
 * @param {() => boolean} deps.isAppNavigation
 * @param {{request(): unknown, release(): unknown}|null} deps.wakeLock
 * @param {() => boolean} deps.isVisible
 * @param {{on(type: string, handler: Function): (() => void)}|null} deps.events
 *     - source of `online`, `visibilitychange` and `beforeunload`
 */
export function createUploadsController(deps) {
    const {
        apiFetch,
        createTusUpload,
        toast,
        now,
        tabId,
        randomHex,
        isAuthenticated,
        onUnauthorized,
        isAppNavigation,
        wakeLock,
        isVisible,
        events,
    } = deps
    const setTimer = deps.setTimeout
    const clearTimer = deps.clearTimeout

    // ── State ────────────────────────────────────────────────────────────────

    /** key → entry (§6.2). Insertion order is the queue order. */
    const entries = reactive(new Map())

    /** key → { file, upload, creationAbort, retryTimer, retryWake, lastProgressAt }. */
    const localObjects = markRaw(new Map())

    const handledTerminal = new Set()
    const orphanClientIds = new Set()
    const cancelledHere = new Set()
    let networkToastShown = false

    /** Client time used by the stalled rule (§6.2). */
    const nowRef = ref(now())

    const completedListeners = new Set()

    let stalledTimer = null
    let probeTimer = null
    let wakeWanted = false
    let pumping = false
    let pumpAgain = false
    let disposed = false

    const statusByOrigin = computed(() => computeStatusByOrigin(
        entries.values(),
        { tabId, now: nowRef.value },
    ))

    function list() {
        return [...entries.values()]
    }

    function exists(entry) {
        return entries.get(entry.key) === entry
    }

    function rawOf(key) {
        let raw = localObjects.get(key)
        if (!raw) {
            raw = {
                file: null,
                upload: null,
                creationAbort: null,
                retryTimer: null,
                retryWake: null,
                lastProgressAt: -Infinity,
            }
            localObjects.set(key, raw)
        }
        return raw
    }

    function addEntry(fields) {
        const entry = {
            key: null,
            clientId: null,
            filename: '',
            size: 0,
            targetDir: '',
            origin: null,
            apiPrefix: null,
            root: null,
            fingerprint: null,
            server: null,
            localState: null,
            pauseReason: null,
            cancelRequested: false,
            creationUnanswered: false,
            sentBytes: 0,
            lastSeenAt: null,
            lastProbeAt: null,
            receivedAt: null,
            local: false,
            ...fields,
        }
        entries.set(entry.key, entry)
        return entries.get(entry.key)
    }

    /** Store the `File` of an entry; `entry.local` changes in the same step. */
    function storeFile(entry, file) {
        rawOf(entry.key).file = file
        entry.local = true
    }

    function abortUpload(entry) {
        const raw = localObjects.get(entry.key)
        const upload = raw?.upload
        if (!upload) return
        raw.upload = null
        try {
            const result = upload.abort()
            if (result && typeof result.catch === 'function') result.catch(() => {})
        } catch {
            // An abort failure changes nothing for us: the upload is dropped.
        }
    }

    /** Abort every local activity of an entry and drop its local objects. */
    function dropLocal(entry) {
        abortUpload(entry)
        const raw = localObjects.get(entry.key)
        if (raw) {
            raw.creationAbort?.abort()
            raw.retryWake?.()
            localObjects.delete(entry.key)
        }
        entry.local = false
    }

    function removeEntry(entry) {
        if (!exists(entry)) return
        dropLocal(entry)
        entries.delete(entry.key)
        afterChange()
    }

    // ── Toasts (§6.9) ────────────────────────────────────────────────────────

    function panelLabel(entry) {
        return PANEL_LABELS[entry.origin?.panel] || 'Files'
    }

    function failureToast(filename, message) {
        toast.error(message, { title: `Upload failed: ${filename}`, duration: TOAST_DURATION_MS })
    }

    function pausedToast(entry, message) {
        failureToast(
            entry.filename,
            `${message || 'The upload stopped.'} You can retry it from the ${panelLabel(entry)} tab.`,
        )
    }

    function networkToast() {
        if (networkToastShown) return
        networkToastShown = true
        toast.error('The server cannot be reached. Uploads resume when the connection comes back.', {
            title: 'Upload paused',
            duration: TOAST_DURATION_MS,
        })
    }

    function terminalToast(record) {
        if (record.state === 'completed') {
            toast.success(record.target_dir, {
                title: `Uploaded ${baseName(record.final_path) || record.filename}`,
                duration: TOAST_DURATION_MS,
            })
        } else if (record.state === 'failed') {
            failureToast(record.filename, record.error || 'The server stopped the upload.')
        } else if (record.state === 'cancelled' && !cancelledHere.has(record.id)) {
            toast.error('The upload was cancelled in another tab or on another device.', {
                title: `Upload cancelled: ${record.filename}`,
                duration: TOAST_DURATION_MS,
            })
        }
    }

    // ── Local state, pump, timers, wake lock ─────────────────────────────────

    /**
     * Set the local state of an entry, start its work (`creating`, `sending`),
     * then run the pump.
     */
    function setState(entry, localState, pauseReason = null) {
        entry.localState = localState
        entry.pauseReason = localState === 'paused' ? pauseReason : null
        if (localState === 'sending') startSending(entry)
        else if (localState === 'creating') runCreation(entry)
        afterChange()
    }

    function pauseNetwork(entry) {
        setState(entry, 'paused', 'network')
        networkToast()
    }

    function pauseError(entry, message) {
        setState(entry, 'paused', 'error')
        pausedToast(entry, message)
    }

    /**
     * Run after every local state change and entry removal: the pump (§6.4),
     * then the wake lock (§6.12) and the timers (§6.2, §6.5).
     */
    function afterChange() {
        if (disposed) return
        if (pumping) {
            pumpAgain = true
            return
        }
        pumping = true
        try {
            do {
                pumpAgain = false
                let next
                while ((next = selectNextToPromote(entries.values()))) {
                    setState(next, next.server ? 'sending' : 'creating')
                }
            } while (pumpAgain)
        } finally {
            pumping = false
        }
        updateWakeLock()
        updateTimers()
    }

    function hasNetworkPause() {
        return list().some(e => e.localState === 'paused' && e.pauseReason === 'network')
    }

    /**
     * The automatic restart (§6.5): every `network`-paused entry goes back to
     * `queued`, then the pump runs, even when no entry changed.
     */
    function autoRestart() {
        for (const entry of entries.values()) {
            if (entry.localState === 'paused' && entry.pauseReason === 'network') {
                entry.localState = 'queued'
                entry.pauseReason = null
            }
        }
        afterChange()
    }

    function requestWakeLock() {
        if (!wakeLock) return
        try {
            const result = wakeLock.request()
            if (result && typeof result.catch === 'function') result.catch(() => {})
        } catch {
            // API absent or refused: nothing happens.
        }
    }

    function updateWakeLock() {
        const sending = list().some(e => e.localState === 'sending')
        if (sending && !wakeWanted) {
            wakeWanted = true
            requestWakeLock()
        } else if (!sending && wakeWanted) {
            wakeWanted = false
            if (!wakeLock) return
            try {
                const result = wakeLock.release()
                if (result && typeof result.catch === 'function') result.catch(() => {})
            } catch {
                // Nothing to do.
            }
        }
    }

    function isProbeCandidate(entry) {
        return entry.local && entry.localState === null && entry.server?.state === 'finalizing'
    }

    function updateTimers() {
        if (disposed) return
        const all = list()
        const hasNonLocal = all.some(e => !e.local)
        if (hasNonLocal && stalledTimer === null) {
            nowRef.value = now()
            stalledTimer = setTimer(onStalledTick, STALLED_TICK_MS)
        } else if (!hasNonLocal && stalledTimer !== null) {
            clearTimer(stalledTimer)
            stalledTimer = null
        }
        const hasProbe = all.some(isProbeCandidate)
        if (hasProbe && probeTimer === null) {
            probeTimer = setTimer(onProbeTick, PROBE_TICK_MS)
        } else if (!hasProbe && probeTimer !== null) {
            clearTimer(probeTimer)
            probeTimer = null
        }
    }

    function onStalledTick() {
        stalledTimer = null
        nowRef.value = now()
        updateTimers()
    }

    /** The finalization probe (§6.5): one `HEAD` per 60 s for a stuck entry. */
    function onProbeTick() {
        probeTimer = null
        const t = now()
        for (const entry of list()) {
            if (!isProbeCandidate(entry)) continue
            if (t - (entry.lastSeenAt ?? t) <= PROBE_AFTER_MS) continue
            if (entry.lastProbeAt != null && t - entry.lastProbeAt <= PROBE_AFTER_MS) continue
            entry.lastProbeAt = t
            headUpload(entry.server.id).catch(() => {})
        }
        updateTimers()
    }

    // ── Requests ─────────────────────────────────────────────────────────────

    function headUpload(id) {
        return apiFetch(uploadUrl(id), { method: 'HEAD', headers: { 'Tus-Resumable': TUS_VERSION } })
    }

    /**
     * Send the `DELETE` of one upload (every `DELETE` goes through here).
     *
     * @param {string} id
     * @returns {Promise<number>} the answer status, 0 for no answer
     */
    async function deleteUpload(id) {
        try {
            const res = await apiFetch(uploadUrl(id), {
                method: 'DELETE',
                headers: { 'Tus-Resumable': TUS_VERSION },
            })
            return res.status
        } catch {
            return 0
        }
    }

    async function readErrorMessage(res) {
        try {
            const body = await res.json()
            return typeof body?.error === 'string' ? body.error : null
        } catch {
            return null
        }
    }

    // ── Server records (§6.3) ────────────────────────────────────────────────

    function emitCompleted(record) {
        for (const callback of [...completedListeners]) {
            try {
                callback(record)
            } catch (error) {
                console.error('Upload completion listener failed', error)
            }
        }
    }

    function deleteOrphan(record) {
        deleteUpload(record.id).then(status => {
            if (isDeleteDone(status)) orphanClientIds.delete(record.client_id)
        })
    }

    /** Terminal handling (§6.3). */
    function handleTerminal(entry, record) {
        const wasLocal = entry.local
        dropLocal(entry)
        if (wasLocal) terminalToast(record)
        if (record.state === 'completed') emitCompleted(record)
        entries.delete(entry.key)
        handledTerminal.add(record.id)
        afterChange()
    }

    function applyRules(record, seenAt) {
        // Rule 1: a terminal state already handled never comes back.
        if (handledTerminal.has(record.id)) return
        const terminal = isTerminal(record.state)
        // Rule 2: the server upload of a cancelled creation is deleted.
        if (!terminal && orphanClientIds.has(record.client_id)) {
            deleteOrphan(record)
            return
        }
        // Rule 3: find the entry.
        let entry = findEntryForRecord(entries.values(), record)
        if (!entry) {
            if (terminal) {
                handledTerminal.add(record.id)
                if (record.state === 'completed') emitCompleted(record)
                return
            }
            entry = addEntry({
                key: record.id,
                filename: record.filename,
                size: record.size,
                targetDir: record.target_dir,
                origin: { ...record.origin },
            })
        } else if (entry.server && !(record.version > entry.server.version)) {
            // Rule 4: not newer than the stored record.
            return
        }
        // Rule 5: store it.
        const previous = entry.server
        entry.server = record
        const t = now()
        entry.receivedAt = t
        entry.lastSeenAt = Number.isFinite(seenAt) ? seenAt : t
        // Rule 6: terminal handling.
        if (terminal) {
            handleTerminal(entry, record)
            return
        }
        // Rule 7: a local entry waiting for the server.
        const action = rule7Action(entry, previous, record)
        if (action === 'pause-error') pauseError(entry, record.error)
        else if (action === 'requeue') setState(entry, 'queued')
        else afterChange()
    }

    /**
     * Apply one server record: the `POST` answer, a WebSocket message
     * (`fromWs: true`) or a `GET` item (§6.3).
     *
     * @param {object} record
     * @param {{seenAt?: number, fromWs?: boolean}} [options]
     */
    function applyServerRecord(record, { seenAt, fromWs = false } = {}) {
        if (disposed || !record || typeof record.id !== 'string') return
        // Rule 0: a WebSocket record proves the server is reachable.
        const restart = fromWs && hasNetworkPause()
        try {
            applyRules(record, seenAt)
        } finally {
            if (restart) autoRestart()
        }
    }

    /**
     * Resynchronise with `GET api/uploads/` (§6.3).
     *
     * @returns {Promise<void>}
     */
    async function reconcile() {
        const requestedAt = now()
        let data
        try {
            const res = await apiFetch('/api/uploads/')
            if (!res.ok) return
            data = await res.json()
        } catch {
            return
        }
        if (disposed || !data || !Array.isArray(data.uploads)) return
        networkToastShown = false
        const clientNow = now()
        const serverNow = Date.parse(data.now)
        const seen = new Set()
        for (const item of data.uploads) {
            if (!item || typeof item.id !== 'string') continue
            seen.add(item.id)
            const age = serverNow - Date.parse(item.updated_at)
            applyServerRecord(item, { seenAt: Number.isFinite(age) ? clientNow - age : undefined })
        }
        // Select every entry to drop before dropping any: a drop runs the
        // pump, which could promote a `queued` entry of this same answer.
        const toDrop = list().filter(entry => (
            entry.server &&
            !seen.has(entry.server.id) &&
            entry.localState !== 'queued' &&
            entry.localState !== 'creating' &&
            entry.receivedAt < requestedAt
        ))
        for (const entry of toDrop) {
            if (!exists(entry)) continue
            const wasLocal = entry.local
            const wasCancelling = entry.localState === 'cancelling'
            removeEntry(entry)
            if (wasLocal && !wasCancelling) {
                failureToast(entry.filename, 'The upload is no longer on the server.')
            }
        }
        nowRef.value = now()
    }

    /**
     * Run at each WebSocket (re)connection: `reconcile()`, then the automatic
     * restart (§6.3, §6.5).
     */
    async function reconnected() {
        await reconcile()
        autoRestart()
    }

    // ── Creation (§6.4) ──────────────────────────────────────────────────────

    function creationBody(entry) {
        const body = {
            filename: entry.filename,
            size: entry.size,
            target_dir: entry.targetDir,
            origin: { panel: entry.origin.panel, key: entry.origin.key },
            fingerprint: entry.fingerprint,
            client_id: entry.clientId,
        }
        if (entry.apiPrefix === '/api' && entry.root) body.root = entry.root
        return body
    }

    function waitRetryDelay(raw, delay) {
        return new Promise(resolve => {
            const done = () => {
                if (raw.retryTimer !== null) clearTimer(raw.retryTimer)
                raw.retryTimer = null
                raw.retryWake = null
                resolve()
            }
            raw.retryWake = done
            raw.retryTimer = setTimer(done, delay)
        })
    }

    async function runCreation(entry) {
        const raw = rawOf(entry.key)
        let lastWasUpload500 = false
        for (let attempt = 0; ; attempt++) {
            if (entry.cancelRequested) break
            const abort = new AbortController()
            raw.creationAbort = abort
            const timer = setTimer(() => abort.abort(), CREATION_TIMEOUT_MS)
            let res = null
            try {
                res = await apiFetch(`${entry.apiPrefix}/uploads/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(creationBody(entry)),
                    signal: abort.signal,
                })
            } catch {
                res = null
            } finally {
                clearTimer(timer)
                if (raw.creationAbort === abort) raw.creationAbort = null
            }
            if (!exists(entry)) return
            const answer = res ? { status: res.status, getHeader: name => res.headers.get(name) } : null
            const kind = classifyCreationAnswer(answer)
            // 401: the login redirect of `apiFetch`; a full page load follows.
            if (kind === 'unauthorized') return
            if (kind === 'created') {
                let record = null
                try {
                    record = await res.json()
                } catch {
                    record = null
                }
                if (!exists(entry)) return
                if (record && typeof record.id === 'string') {
                    networkToastShown = false
                    applyServerRecord(record)
                    afterCreated(entry)
                    return
                }
            } else if (kind === 'refused') {
                const message = await readErrorMessage(res)
                if (!exists(entry)) return
                if (!entry.cancelRequested) failureToast(entry.filename, message || statusMessage(res.status))
                removeEntry(entry)
                return
            }
            // No answer: the upload may exist on the server.
            entry.creationUnanswered = true
            lastWasUpload500 = !!answer && answer.status === 500 && isUploadAnswer(answer.getHeader)
            if (entry.cancelRequested || attempt >= RETRY_DELAYS.length) break
            await waitRetryDelay(raw, RETRY_DELAYS[attempt])
            if (!exists(entry)) return
        }
        if (entry.cancelRequested) {
            const server = entry.server
            const clientId = entry.clientId
            removeEntry(entry)
            const status = server ? await deleteUpload(server.id) : 0
            if (!server || !isDeleteDone(status)) {
                orphanClientIds.add(clientId)
                reconcile()
            }
            return
        }
        if (lastWasUpload500) pauseError(entry, 'Server error.')
        else pauseNetwork(entry)
    }

    /** Creation step 5, after `applyServerRecord` of a `2xx` answer. */
    function afterCreated(entry) {
        if (!exists(entry)) return
        if (entry.cancelRequested) {
            cancelOnServer(entry, { localState: 'creating', pauseReason: null })
            return
        }
        const record = entry.server
        if (!record) {
            pauseError(entry, 'Unexpected answer from the server.')
        } else if (record.state === 'active' && record.error) {
            pauseError(entry, record.error)
        } else if (record.state === 'active') {
            setState(entry, 'sending')
        } else {
            setState(entry, null)
        }
    }

    // ── Transfer (§6.5) ──────────────────────────────────────────────────────

    function startSending(entry) {
        const raw = rawOf(entry.key)
        if (!raw.file || !entry.server) {
            // Nothing to send (no `File`): wait for the server.
            entry.localState = null
            return
        }
        entry.sentBytes = entry.server.offset
        raw.lastProgressAt = -Infinity
        let upload = null
        const isCurrent = () => upload !== null && raw.upload === upload
        upload = createTusUpload(raw.file, {
            uploadUrl: uploadUrl(entry.server.id),
            chunkSize: CHUNK_SIZE,
            retryDelays: [...RETRY_DELAYS],
            storeFingerprintForResuming: false,
            onShouldRetry: err => tusShouldRetry(err),
            onProgress: bytesSent => {
                if (!isCurrent()) return
                const t = now()
                if (t - raw.lastProgressAt < PROGRESS_THROTTLE_MS) return
                raw.lastProgressAt = t
                entry.sentBytes = bytesSent
            },
            onChunkComplete: (_chunkSize, bytesAccepted) => {
                if (!isCurrent()) return
                networkToastShown = false
                if (Number.isFinite(bytesAccepted)) entry.sentBytes = bytesAccepted
            },
            onSuccess: () => {
                if (!isCurrent()) return
                raw.upload = null
                networkToastShown = false
                setState(entry, null)
            },
            onError: err => {
                if (!isCurrent()) return
                raw.upload = null
                handleTransferError(entry, err)
            },
        })
        raw.upload = upload
        upload.start()
    }

    async function handleTransferError(entry, err) {
        const verdict = classifyTusError(err, entry.server)
        if (verdict.action === 'unauthorized') {
            onUnauthorized()
            return
        }
        if (verdict.action === 'reconcile') {
            setState(entry, null)
            await reconcile()
            if (exists(entry) && entry.localState === null && entry.server?.state === 'active') {
                pauseNetwork(entry)
            }
            return
        }
        if (verdict.reason === 'network') pauseNetwork(entry)
        else pauseError(entry, verdict.message)
    }

    // ── User actions ─────────────────────────────────────────────────────────

    /**
     * Start one upload per picked file (§6.4).
     *
     * @param {{files: File[], targetDir: string, apiPrefix: string, root?: string|null,
     *          origin: {panel: string, key: string}}} options
     * @returns {Promise<void>}
     */
    async function startUploads({ files, targetDir, apiPrefix, root = null, origin }) {
        for (const file of files) {
            let fingerprint
            try {
                fingerprint = await fingerprintFile(file)
            } catch {
                failureToast(file.name, 'The file could not be read.')
                continue
            }
            if (disposed) return
            const clientId = makeClientId(tabId, randomHex)
            const entry = addEntry({
                key: clientId,
                clientId,
                filename: file.name,
                size: file.size,
                targetDir,
                origin: { panel: origin.panel, key: origin.key },
                apiPrefix,
                root: root || null,
                fingerprint,
                localState: 'queued',
            })
            storeFile(entry, file)
        }
        autoRestart()
    }

    /** The "otherwise" branch of *Cancel* (§6.4): `DELETE` on the server. */
    async function cancelOnServer(entry, previous) {
        abortUpload(entry)
        entry.localState = 'cancelling'
        entry.pauseReason = null
        afterChange()
        const id = entry.server.id
        cancelledHere.add(id)
        const status = await deleteUpload(id)
        if (status >= 200 && status < 300) return
        if (status === 404 || status === 410) {
            removeEntry(entry)
            reconcile()
            return
        }
        if (!exists(entry) || entry.localState !== 'cancelling') return
        entry.cancelRequested = false
        let back = previous.localState
        if (back === 'creating' || back === 'sending') back = 'queued'
        setState(entry, back, previous.pauseReason)
    }

    /**
     * Cancel one entry (§6.4).
     *
     * @param {string} key
     * @returns {Promise<void>}
     */
    async function cancel(key) {
        const entry = entries.get(key)
        if (!entry) return
        if (entry.localState === 'cancelling' || entry.server?.state === 'finalizing') return
        const localState = entry.localState
        if ((localState === 'queued' || localState === 'paused') && !entry.server) {
            if (entry.creationUnanswered) {
                orphanClientIds.add(entry.clientId)
                removeEntry(entry)
                await reconcile()
            } else {
                removeEntry(entry)
            }
            return
        }
        if (localState === 'creating') {
            entry.cancelRequested = true
            const raw = localObjects.get(key)
            raw?.creationAbort?.abort()
            raw?.retryWake?.()
            return
        }
        if (!entry.server) return
        await cancelOnServer(entry, { localState, pauseReason: entry.pauseReason })
    }

    /**
     * *Retry* of a `paused` entry (§6.5 manual restart).
     *
     * @param {string} key
     */
    function retry(key) {
        const entry = entries.get(key)
        if (!entry || entry.localState !== 'paused') return
        entry.localState = 'queued'
        entry.pauseReason = null
        autoRestart()
    }

    /**
     * *Retry* of a non-local complete entry (§6.7): one `HEAD`, which
     * finalizes again on the server. One error toast on a failed answer.
     *
     * @param {string} key
     * @returns {Promise<void>}
     */
    async function retryFinalization(key) {
        const entry = entries.get(key)
        if (!entry?.server) return
        const filename = entry.filename
        let status = 0
        try {
            const res = await headUpload(entry.server.id)
            if (res.ok) return
            status = res.status
        } catch {
            status = 0
        }
        failureToast(filename, statusMessage(status))
    }

    /**
     * *Resume* of a stalled entry with a picked file (§6.8).
     *
     * @param {string} key
     * @param {File} file
     * @returns {Promise<boolean>} true when the file matched and was stored
     */
    async function resume(key, file) {
        const entry = entries.get(key)
        if (!entry || entry.local || entry.server?.state !== 'active') return false
        const record = entry.server
        const mismatch = 'The picked file is not the file of this upload.'
        if (file.size !== record.size) {
            failureToast(entry.filename, mismatch)
            return false
        }
        let fingerprint
        try {
            fingerprint = await fingerprintFile(file)
        } catch {
            failureToast(entry.filename, 'The picked file could not be read.')
            return false
        }
        if (!exists(entry) || entry.local || entry.server?.state !== 'active') return false
        if (fingerprint !== entry.server.fingerprint) {
            failureToast(entry.filename, mismatch)
            return false
        }
        storeFile(entry, file)
        entry.localState = 'queued'
        entry.pauseReason = null
        autoRestart()
        return true
    }

    // ── Page events (§6.5, §6.12, §6.13) ─────────────────────────────────────

    /** True when leaving the page must be confirmed (§6.13). */
    function shouldConfirmUnload() {
        if (isAppNavigation() || !isAuthenticated()) return false
        return list().some(e => e.local && UNLOAD_STATES.includes(e.localState))
    }

    function onBeforeUnload(event) {
        if (!shouldConfirmUnload()) return
        event.preventDefault()
        event.returnValue = ''
    }

    function onVisibilityChange() {
        if (!isVisible()) return
        if (wakeWanted) requestWakeLock()
        autoRestart()
    }

    const unsubscribers = []
    if (events) {
        unsubscribers.push(events.on('online', () => autoRestart()))
        unsubscribers.push(events.on('visibilitychange', onVisibilityChange))
        unsubscribers.push(events.on('beforeunload', onBeforeUnload))
    }

    /**
     * Subscribe to completed uploads (§6.10), local or not.
     *
     * @param {(record: object) => void} callback
     * @returns {() => void} unsubscribe
     */
    function onCompleted(callback) {
        completedListeners.add(callback)
        return () => completedListeners.delete(callback)
    }

    /** Stop every listener, timer and transfer (tests, dev hot reload). */
    function dispose() {
        disposed = true
        for (const off of unsubscribers) {
            try {
                off?.()
            } catch {
                // Nothing to do.
            }
        }
        if (stalledTimer !== null) clearTimer(stalledTimer)
        if (probeTimer !== null) clearTimer(probeTimer)
        stalledTimer = null
        probeTimer = null
        for (const entry of list()) dropLocal(entry)
        completedListeners.clear()
    }

    // ── Read helpers for the components ──────────────────────────────────────

    function isStalled(entry) {
        return isStalledRule(entry, { tabId, now: nowRef.value })
    }

    function entryActions(entry) {
        return computeEntryActions(entry, { tabId, now: nowRef.value })
    }

    /**
     * Entries that belong to a panel (same `origin.panel` and `origin.key`).
     *
     * @param {{panel: string, key: string}} origin
     * @returns {object[]}
     */
    function entriesForOrigin(origin) {
        const key = originKey(origin)
        return list().filter(e => originKey(e.origin) === key)
    }

    return {
        entries,
        statusByOrigin,
        now: nowRef,
        applyServerRecord,
        reconcile,
        reconnected,
        startUploads,
        cancel,
        retry,
        retryFinalization,
        resume,
        autoRestart,
        deleteUpload,
        onCompleted,
        shouldConfirmUnload,
        isStalled,
        entryActions,
        entriesForOrigin,
        dispose,
        /** Read-only view of the non-reactive sets and flags, for tests. */
        internals: {
            handledTerminal,
            orphanClientIds,
            cancelledHere,
            hasLocalFile: key => !!localObjects.get(key)?.file,
            get networkToastShown() {
                return networkToastShown
            },
        },
    }
}
