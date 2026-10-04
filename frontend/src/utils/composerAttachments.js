// frontend/src/utils/composerAttachments.js
// Composer attachments (spec 2026-10-03 §9.1, §9.2): every file added to the
// composer is uploaded to the server staging store, and the draft keeps only a
// reference record `{id, sessionId, bucket, position, name, size, mimeType,
// kind}` in IndexedDB. The `File`, the upload state, its progress, the current
// upload attempt (`clientId`) and its upload key live in memory only.
//
// This module holds the pure helpers (ref conversion, display kind, state
// mapping), the staging HTTP helpers, and `createComposerAttachments`: the
// attachment actions the data store delegates to. The actions are free of
// Pinia and IndexedDB: every outside effect comes from an injected dependency,
// so node:test drives them on top of the real upload controller.

import { watch } from 'vue'
import { entryPercent } from './uploads/display.js'
import { makeClientId } from './uploads/ids.js'

/** The upload origin panel of composer attachments. */
export const COMPOSER_PANEL = 'composer'

/** Display states of a composer attachment chip (§9.2). */
export const ATTACHMENT_STATE = Object.freeze({
    UPLOADING: 'uploading',
    READY: 'ready',
    FAILED: 'failed',
    MISSING: 'missing',
})

const API_ROOT = '/api/composer-attachments'
const HOLDERS = ['draft', 'snapshot']

// ── Pure helpers ─────────────────────────────────────────────────────────────

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'avif', 'heic', 'heif', 'tif', 'tiff', 'ico'])
const VIDEO_EXTENSIONS = new Set(['mp4', 'm4v', 'mov', 'webm', 'mkv', 'avi', 'wmv', 'mpg', 'mpeg', 'ogv', '3gp'])
const AUDIO_EXTENSIONS = new Set(['mp3', 'wav', 'ogg', 'oga', 'opus', 'flac', 'm4a', 'aac', 'wma', 'aiff', 'aif', 'mid', 'midi'])
const TEXT_EXTENSIONS = new Set([
    'txt', 'md', 'markdown', 'rst', 'csv', 'tsv', 'log', 'json', 'jsonl', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf',
    'xml', 'html', 'htm', 'css', 'scss', 'less', 'svg', 'js', 'mjs', 'cjs', 'jsx', 'ts', 'tsx', 'vue', 'py', 'rb', 'go',
    'rs', 'java', 'kt', 'c', 'h', 'cc', 'cpp', 'hpp', 'cs', 'php', 'sh', 'bash', 'zsh', 'fish', 'sql', 'swift', 'lua',
    'pl', 'r', 'tex', 'diff', 'patch', 'env',
])
const TEXT_MIME_TYPES = new Set([
    'application/json', 'application/xml', 'application/javascript', 'application/x-javascript',
    'application/x-yaml', 'application/yaml', 'application/toml', 'application/x-sh', 'application/sql',
    'image/svg+xml',
])

function extensionOf(name) {
    const match = /\.([^./\\]+)$/.exec(name || '')
    return match ? match[1].toLowerCase() : ''
}

/**
 * Display kind of a file, from its MIME type then its extension: `image`,
 * `PDF`, `text`, `video`, `audio` or `other`. For display only: the server's
 * detection decides how a file is sent. SVG is text (no raster thumbnail).
 *
 * @param {{name?: string, type?: string}} file
 * @returns {'image'|'PDF'|'text'|'video'|'audio'|'other'}
 */
export function getDisplayKind(file) {
    const type = (file?.type || '').toLowerCase().split(';')[0].trim()
    const ext = extensionOf(file?.name)
    if (type === 'application/pdf' || (!type && ext === 'pdf')) return 'PDF'
    if (TEXT_MIME_TYPES.has(type) || type.startsWith('text/')) return 'text'
    if (type.startsWith('image/')) return 'image'
    if (type.startsWith('video/')) return 'video'
    if (type.startsWith('audio/')) return 'audio'
    if (ext === 'pdf') return 'PDF'
    if (ext === 'svg' || TEXT_EXTENSIONS.has(ext)) return 'text'
    if (IMAGE_EXTENSIONS.has(ext)) return 'image'
    if (VIDEO_EXTENSIONS.has(ext)) return 'video'
    if (AUDIO_EXTENSIONS.has(ext)) return 'audio'
    return 'other'
}

/**
 * The staging refs of records, in `position` order. The bucket is each
 * record's own (never re-derived from the current session id).
 *
 * @param {Array<{bucket: string, id: string, position: number}>} records
 * @returns {Array<{bucket: string, id: string}>}
 */
export function toAttachmentRefs(records) {
    return [...records]
        .sort((a, b) => a.position - b.position)
        .map(record => ({ bucket: record.bucket, id: record.id }))
}

/**
 * Chip state from one `status/` item (§9.2). On a reconnect, a chip already
 * `failed` stays failed when the server answers `missing` (a refused creation
 * leaves an entry without `ready.json`).
 *
 * @param {{state: string}} status
 * @param {string} [previousState] - the chip state before the request (none at hydrate)
 * @returns {{state: string}}
 */
export function mapAttachmentStatus(status, previousState) {
    switch (status?.state) {
        case 'ready':
        case 'promoted':
            return { state: ATTACHMENT_STATE.READY }
        case 'uploading':
            return { state: ATTACHMENT_STATE.UPLOADING }
        default:
            return { state: previousState === ATTACHMENT_STATE.FAILED ? ATTACHMENT_STATE.FAILED : ATTACHMENT_STATE.MISSING }
    }
}

/**
 * True when a `completed` upload is the chip's current attempt. Never matched
 * by origin key: the settle rule may complete an older attempt of the same
 * entry just before resetting it.
 *
 * @param {string|null} currentClientId
 * @param {string|null} eventClientId
 * @returns {boolean}
 */
export function shouldAcceptCompletion(currentClientId, eventClientId) {
    return !!currentClientId && currentClientId === eventClientId
}

/**
 * Chip state from the upload entry of the chip's current attempt, while the
 * chip is `uploading` (§9.2). A removed entry fails the chip only once it was
 * seen (a completion turns the chip `ready` before its entry goes away). A
 * creation that ended with no server record after the controller's retries
 * (error pause, no record) fails, and so does a stalled upload. A network
 * pause and a paused transfer (the controller's own Retry) stay `uploading`.
 *
 * @param {object|null} entry
 * @param {{seen: boolean, stalled: boolean}} ctx
 * @returns {string}
 */
export function uploadDisplayState(entry, { seen, stalled }) {
    if (!entry) return seen ? ATTACHMENT_STATE.FAILED : ATTACHMENT_STATE.UPLOADING
    if (entry.localState === 'paused' && entry.pauseReason === 'error' && !entry.server) return ATTACHMENT_STATE.FAILED
    if (stalled) return ATTACHMENT_STATE.FAILED
    return ATTACHMENT_STATE.UPLOADING
}

/**
 * The upload entry of an attempt: by its `clientId` (an entry created here) or
 * by its server record's `client_id` (an entry of another tab, or of this tab
 * before a reload).
 *
 * @param {Map<string, object>} entries
 * @param {string} clientId
 * @returns {object|null}
 */
export function findUploadEntry(entries, clientId) {
    const direct = entries.get(clientId)
    if (direct) return direct
    for (const entry of entries.values()) {
        if (entry.clientId === clientId || entry.server?.client_id === clientId) return entry
    }
    return null
}

/** The upload origin key of a record: `"<bucket>/<id>"`. */
export function attachmentOriginKey(record) {
    return `${record.bucket}/${record.id}`
}

/** URL of the staged content of one entry (image and text previews). */
export function attachmentContentUrl(ref) {
    return `${API_ROOT}/${encodeURIComponent(ref.bucket)}/${encodeURIComponent(ref.id)}/content`
}

// ── HTTP helpers (spec §6.1.2) ───────────────────────────────────────────────

function jsonPost(fetchFn, url, body) {
    return fetchFn(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
}

/**
 * `POST status/`: the state of each ref, in the requested order.
 *
 * @param {Array<{bucket: string, id: string}>} refs
 * @param {Function} fetchFn - `apiFetch`
 * @returns {Promise<Array<{bucket: string, id: string, state: string, client_id?: string, offset?: number}>>}
 *     rejects on a failed request or answer
 */
export async function statusRefs(refs, fetchFn) {
    if (!refs.length) return []
    const res = await jsonPost(fetchFn, `${API_ROOT}/status/`, { refs })
    if (!res.ok) throw new Error(`Attachment status failed (${res.status})`)
    const data = await res.json()
    if (!Array.isArray(data?.statuses) || data.statuses.length !== refs.length) {
        throw new Error('Unexpected attachment status answer')
    }
    return data.statuses
}

/**
 * `POST touch/`: heartbeat of held entries. `draft` also makes a committed
 * entry a draft attachment again; `snapshot` only touches.
 *
 * @param {Array<{bucket: string, id: string}>} refs
 * @param {'draft'|'snapshot'} holder
 * @param {Function} fetchFn
 * @returns {Promise<void>} rejects on a failed request or answer
 */
export async function touchRefs(refs, holder, fetchFn) {
    if (!HOLDERS.includes(holder)) throw new Error(`Invalid holder: ${holder}`)
    if (!refs.length) return
    const res = await jsonPost(fetchFn, `${API_ROOT}/touch/`, { refs, holder })
    if (!res.ok) throw new Error(`Attachment touch failed (${res.status})`)
}

/**
 * `DELETE <bucket>/<id>/`: release one entry (idempotent).
 *
 * @param {{bucket: string, id: string}} ref
 * @param {Function} fetchFn
 * @returns {Promise<void>} rejects on a failed request or answer
 */
export async function releaseRef(ref, fetchFn) {
    const url = `${API_ROOT}/${encodeURIComponent(ref.bucket)}/${encodeURIComponent(ref.id)}/`
    const res = await fetchFn(url, { method: 'DELETE' })
    if (!res.ok) throw new Error(`Attachment release failed (${res.status})`)
}

// ── Actions ──────────────────────────────────────────────────────────────────

function plainRecord(record) {
    return {
        id: record.id,
        sessionId: record.sessionId,
        bucket: record.bucket,
        position: record.position,
        name: record.name,
        size: record.size,
        mimeType: record.mimeType,
        kind: record.kind,
    }
}

/**
 * The composer attachment actions.
 *
 * @param {object} deps
 * @param {object} deps.records - reactive `sessionId → {id → record}`
 * @param {object} deps.runtime - reactive `id → {state, progress, retryable,
 *     pauseReason, clientId, uploadKey}` (memory only)
 * @param {{saveDraftAttachment: Function, deleteDraftAttachment: Function,
 *     deleteLegacyMedia: (id: string) => Promise<void>}} deps.storage
 * @param {{tabId: string, entries: Map, startUploads: Function, cancel: Function,
 *     onCompleted: Function, isStalled: Function}} deps.uploads - the uploads store
 * @param {Function} deps.fetch - `apiFetch`
 * @param {() => string} deps.uuid
 * @param {(n: number) => string} deps.randomHex
 */
export function createComposerAttachments(deps) {
    const { records, runtime, storage, uploads, uuid, randomHex } = deps
    const fetchFn = deps.fetch

    /** id → File, for previews and chip Retry (memory only). */
    const files = new Map()
    /** id → {clientId, pending, seen}: the current upload attempt. */
    const attempts = new Map()
    /** id → counter, bumped by every new attempt and every removal. */
    const generations = new Map()

    function bump(id) {
        generations.set(id, (generations.get(id) || 0) + 1)
    }

    function findRecord(id) {
        for (const byId of Object.values(records)) {
            if (byId[id]) return byId[id]
        }
        return null
    }

    function allRecords() {
        return Object.values(records).flatMap(byId => Object.values(byId))
    }

    function getRecords(sessionId) {
        return Object.values(records[sessionId] || {}).sort((a, b) => a.position - b.position)
    }

    function setState(id, state) {
        const rt = runtime[id]
        if (!rt) return
        rt.state = state
        rt.retryable = state === ATTACHMENT_STATE.FAILED && files.has(id)
        if (state !== ATTACHMENT_STATE.UPLOADING) rt.pauseReason = null
        if (state === ATTACHMENT_STATE.READY) rt.progress = 100
    }

    function createRuntime(id) {
        runtime[id] = {
            state: ATTACHMENT_STATE.UPLOADING,
            progress: 0,
            retryable: false,
            pauseReason: null,
            clientId: null,
            uploadKey: null,
        }
        return runtime[id]
    }

    /** Drop every local trace of one attachment (not its stored rows). */
    function dropLocal(id) {
        const record = findRecord(id)
        if (record) {
            delete records[record.sessionId][id]
            if (!Object.keys(records[record.sessionId]).length) delete records[record.sessionId]
        }
        delete runtime[id]
        files.delete(id)
        attempts.delete(id)
        bump(id)
    }

    function isCurrentAttempt(id, clientId) {
        return !!runtime[id] && attempts.get(id)?.clientId === clientId
    }

    /** True when this tab is creating or transferring the attachment's current attempt. */
    function hasLiveLocalUpload(id) {
        const attempt = attempts.get(id)
        if (!attempt) return false
        if (attempt.pending) return true
        return !!uploads.entries.get(attempt.clientId)?.local
    }

    // Upload entries → chip states (§9.2).
    function syncUploadStates() {
        for (const [id, rt] of Object.entries(runtime)) {
            const attempt = attempts.get(id)
            if (rt.state !== ATTACHMENT_STATE.UPLOADING || !attempt?.clientId || attempt.pending) continue
            const entry = findUploadEntry(uploads.entries, attempt.clientId)
            if (entry) {
                attempt.seen = true
                rt.uploadKey = entry.key
                rt.progress = entryPercent(entry)
                rt.pauseReason = entry.localState === 'paused' ? entry.pauseReason : null
            }
            const state = uploadDisplayState(entry, { seen: attempt.seen, stalled: !!entry && uploads.isStalled(entry) })
            if (state !== ATTACHMENT_STATE.UPLOADING) setState(id, state)
        }
    }

    function onRejected(id, info) {
        if (!isCurrentAttempt(id, info.client_id)) return
        attempts.get(id).pending = false
        setState(id, ATTACHMENT_STATE.FAILED)
    }

    function onCompleted(record) {
        if (record?.origin?.panel !== COMPOSER_PANEL || typeof record.origin.key !== 'string') return
        const id = record.origin.key.slice(record.origin.key.lastIndexOf('/') + 1)
        const rt = runtime[id]
        if (!rt || rt.state === ATTACHMENT_STATE.MISSING) return
        if (!shouldAcceptCompletion(attempts.get(id)?.clientId ?? null, record.client_id)) return
        setState(id, ATTACHMENT_STATE.READY)
    }

    /** Start a new upload attempt (new client id) of a record whose `File` is in memory. */
    async function startAttempt(record) {
        const id = record.id
        const file = files.get(id)
        const clientId = makeClientId(uploads.tabId, randomHex)
        bump(id)
        attempts.set(id, { clientId, pending: true, seen: false })
        const rt = runtime[id]
        rt.clientId = clientId
        rt.uploadKey = clientId
        rt.progress = 0
        rt.pauseReason = null
        setState(id, ATTACHMENT_STATE.UPLOADING)
        let rejected = false
        try {
            await uploads.startUploads({
                files: [file],
                origin: { panel: COMPOSER_PANEL, key: attachmentOriginKey(record) },
                clientId,
                onRejected: info => {
                    if (info.client_id === clientId) rejected = true
                    onRejected(id, info)
                },
            })
        } catch (error) {
            console.error('Composer upload start failed', error)
            rejected = true
            onRejected(id, { client_id: clientId })
        }
        if (!isCurrentAttempt(id, clientId)) {
            // Removed while the file was read: drop the upload that just started.
            if (!rejected) Promise.resolve(uploads.cancel(clientId)).catch(() => {})
            return
        }
        const attempt = attempts.get(id)
        attempt.pending = false
        if (!rejected) attempt.seen = true
        syncUploadStates()
    }

    /**
     * Add a file: persist its record (bucket = the session id, next position),
     * then upload it into its staging entry. No type or size check.
     *
     * @param {string} sessionId
     * @param {File} file
     * @returns {Promise<object>} the record
     */
    async function addAttachment(sessionId, file) {
        const id = uuid()
        const existing = Object.values(records[sessionId] || {})
        const position = existing.reduce((max, r) => Math.max(max, r.position), -1) + 1
        const record = {
            id,
            sessionId,
            bucket: sessionId,
            position,
            name: file.name,
            size: file.size,
            mimeType: file.type || '',
            kind: getDisplayKind(file),
        }
        if (!records[sessionId]) records[sessionId] = {}
        records[sessionId][id] = record
        createRuntime(id)
        files.set(id, file)
        try {
            await storage.saveDraftAttachment(plainRecord(record))
        } catch (error) {
            dropLocal(id)
            throw error
        }
        if (!findRecord(id)) {
            // Removed during the write: make sure no row survives.
            storage.deleteDraftAttachment(id).catch(() => {})
            return record
        }
        await startAttempt(findRecord(id))
        return record
    }

    /**
     * Chip Retry of a `failed` attachment whose `File` is in memory: cancel the
     * previous local attempt, then a new attempt (new client id) into the same
     * entry (the server settles any other attempt).
     *
     * @param {string} id
     * @returns {Promise<boolean>} false when Retry is not possible
     */
    async function retryAttachment(id) {
        const rt = runtime[id]
        const record = findRecord(id)
        if (!rt || !record || rt.state !== ATTACHMENT_STATE.FAILED || !files.has(id)) return false
        const previousKey = rt.uploadKey
        if (previousKey && uploads.entries.has(previousKey)) {
            Promise.resolve(uploads.cancel(previousKey)).catch(() => {})
        }
        await startAttempt(record)
        return true
    }

    /**
     * Drop local records only (post-send clear, cleanups): their rows and the
     * legacy media rows with the same ids. Never cancels an upload nor
     * releases a staging entry.
     *
     * @param {string} sessionId
     * @returns {Promise<void>}
     */
    async function forgetAttachments(sessionId) {
        const forgotten = Object.values(records[sessionId] || {}).map(record => record.id)
        for (const id of forgotten) dropLocal(id)
        for (const id of forgotten) {
            await Promise.allSettled([storage.deleteDraftAttachment(id), storage.deleteLegacyMedia(id)])
        }
    }

    /**
     * Release explicit refs (user removals and discards, §6.1.4): cancel this
     * tab's local attempt, delete the local and legacy rows, then release each
     * staging entry on the server.
     *
     * @param {Array<{bucket: string, id: string}>} refs
     * @returns {Promise<void>}
     */
    async function releaseAttachments(refs) {
        const cancels = []
        for (const ref of refs) {
            const key = attempts.get(ref.id)?.clientId
            dropLocal(ref.id)
            if (key && uploads.entries.has(key)) cancels.push(uploads.cancel(key))
        }
        await Promise.allSettled(cancels)
        for (const ref of refs) {
            await Promise.allSettled([storage.deleteDraftAttachment(ref.id), storage.deleteLegacyMedia(ref.id)])
        }
        for (const ref of refs) {
            try {
                await releaseRef(ref, fetchFn)
            } catch (error) {
                // Left to the server reaper.
                console.warn('Attachment release failed', error)
            }
        }
    }

    /**
     * Ask `status/` for every record without a live local upload of this tab,
     * then apply the answers (§9.2). Local live-upload state always wins: an
     * answer for an attachment that started a new attempt or was removed
     * meanwhile is ignored.
     *
     * @param {{hydrate?: boolean}} [options] - at hydrate, no previous state counts
     * @returns {Promise<void>}
     */
    async function reconcileAttachmentStatuses({ hydrate = false } = {}) {
        const targets = []
        for (const record of allRecords()) {
            if (hasLiveLocalUpload(record.id)) continue
            targets.push({
                record,
                previous: hydrate ? undefined : runtime[record.id]?.state,
                generation: generations.get(record.id) || 0,
            })
        }
        if (!targets.length) return
        let statuses
        try {
            statuses = await statusRefs(targets.map(t => ({ bucket: t.record.bucket, id: t.record.id })), fetchFn)
        } catch (error) {
            console.warn('Attachment status failed', error)
            return
        }
        targets.forEach(({ record, previous, generation }, index) => {
            const status = statuses[index]
            const id = record.id
            if (findRecord(id) !== record || (generations.get(id) || 0) !== generation || hasLiveLocalUpload(id)) return
            if (status?.bucket !== record.bucket || status?.id !== id) return
            const rt = runtime[id] || createRuntime(id)
            const { state } = mapAttachmentStatus(status, previous)
            const clientId = status.state === 'uploading' && typeof status.client_id === 'string' ? status.client_id : null
            attempts.set(id, { clientId, pending: false, seen: false })
            rt.clientId = clientId
            rt.uploadKey = null
            rt.pauseReason = null
            rt.progress = state === ATTACHMENT_STATE.UPLOADING && record.size > 0
                ? Math.min(100, Math.floor((100 * (status.offset || 0)) / record.size))
                : 0
            setState(id, state)
        })
        syncUploadStates()
    }

    /**
     * Heartbeat: touch every draft record's entry (`holder: draft`), and the
     * given snapshot refs (`holder: snapshot`), in two requests.
     *
     * @param {{snapshotRefs?: Array<{bucket: string, id: string}>}} [options]
     * @returns {Promise<void>}
     */
    async function touchHeldAttachments({ snapshotRefs = [] } = {}) {
        const draftRefs = allRecords().map(record => ({ bucket: record.bucket, id: record.id }))
        const results = await Promise.allSettled([
            touchRefs(draftRefs, 'draft', fetchFn),
            touchRefs(snapshotRefs, 'snapshot', fetchFn),
        ])
        for (const result of results) {
            if (result.status === 'rejected') console.warn('Attachment heartbeat failed', result.reason)
        }
    }

    /**
     * Load stored records (app startup). Their state stays `uploading` until
     * `reconcileAttachmentStatuses` answers.
     *
     * @param {object[]} rows
     */
    function hydrate(rows) {
        for (const row of rows) {
            if (!row?.id || !row.sessionId || !row.bucket) continue
            if (!records[row.sessionId]) records[row.sessionId] = {}
            records[row.sessionId][row.id] = plainRecord(row)
            if (!runtime[row.id]) createRuntime(row.id)
        }
    }

    function getFile(id) {
        return files.get(id) || null
    }

    const stopCompleted = uploads.onCompleted(onCompleted)
    // The signature reads every field a chip state depends on. Not a
    // synchronous watcher: the controller updates an entry in several steps
    // (e.g. its record, then its `lastSeenAt`), and a state read in between
    // could look stalled. A local attempt is marked seen by `startAttempt`
    // itself, so a removal in the same tick as its creation still fails it.
    const stopWatch = watch(
        () => {
            const parts = []
            for (const entry of uploads.entries.values()) {
                if (entry.origin?.panel !== COMPOSER_PANEL) continue
                parts.push([
                    entry.key, entry.clientId, entry.server?.client_id, entry.server?.state, entry.server?.offset,
                    entry.localState, entry.pauseReason, entry.local, entry.sentBytes, uploads.isStalled(entry),
                ])
            }
            return JSON.stringify(parts)
        },
        () => syncUploadStates(),
    )

    function dispose() {
        stopWatch()
        stopCompleted()
    }

    return {
        addAttachment,
        forgetAttachments,
        releaseAttachments,
        retryAttachment,
        reconcileAttachmentStatuses,
        touchHeldAttachments,
        hydrate,
        getRecords,
        getFile,
        syncUploadStates,
        dispose,
    }
}
