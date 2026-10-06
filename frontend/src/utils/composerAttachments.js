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
import { COMPOSER_PANEL } from './uploads/controller.js'
import { entryPercent } from './uploads/display.js'
import { makeClientId } from './uploads/ids.js'
import { migrateLegacyAttachments } from './attachmentMigration.js'
import { attachmentKindIcon } from './attachmentStrip.js'

/** The upload origin panel of composer attachments (defined by the upload controller). */
export { COMPOSER_PANEL }
// One kind → icon mapping for composer chips and history strips.
export { attachmentKindIcon }

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

function byPosition(a, b) {
    return a.position - b.position
}

/**
 * The records a composer shows, in order (spec 2026-10-03 §9.5): the records
 * of the session the id resolves to through `aliases` (draft id → canonical
 * id), then, per draft aliased to it, that draft's records not yet moved plus
 * the records added through its alias (`joinedFrom`, memory only). A draft id
 * and its canonical id therefore show the same list before, during and after
 * `rebindDraftAttachments`, which keeps this exact order.
 *
 * @param {object} records - `sessionId → {id → record}`
 * @param {object} aliases - `draftId → canonicalId`
 * @param {string} sessionId
 * @returns {object[]}
 */
export function composerRecordsFor(records, aliases, sessionId) {
    const owner = aliases?.[sessionId] || sessionId
    const own = Object.values(records[owner] || {}).sort(byPosition)
    const sources = Object.keys(aliases || {}).filter(id => id !== owner && aliases[id] === owner)
    const joined = new Set(sources)
    const result = own.filter(record => !joined.has(record.joinedFrom))
    for (const source of sources) {
        const group = [...Object.values(records[source] || {}), ...own.filter(record => record.joinedFrom === source)]
        result.push(...group.sort(byPosition))
    }
    return result
}

/**
 * Staged refs deduplicated by bucket and id, first occurrence order.
 *
 * @param {Array<{bucket: string, id: string}>} refs
 * @returns {Array<{bucket: string, id: string}>}
 */
export function uniqueRefs(refs) {
    const seen = new Set()
    const result = []
    for (const ref of refs) {
        if (!ref?.bucket || !ref.id) continue
        const key = `${ref.bucket}/${ref.id}`
        if (seen.has(key)) continue
        seen.add(key)
        result.push({ bucket: ref.bucket, id: ref.id })
    }
    return result
}

/**
 * Snapshot metadata of composer records (in-flight send, §9.5): plain
 * `{bucket, id, name, size, mimeType, kind}` objects, in the given order. No
 * bytes: the staged entry holds the file.
 *
 * The order is the caller's send order (`composerRecordsFor`), never a sort
 * by `position`: a bound draft shows records of two groups, and each group
 * numbers its positions on its own.
 *
 * @param {object[]} records
 * @returns {Array<{bucket: string, id: string, name: string, size: number, mimeType: string, kind: string}>}
 */
export function snapshotAttachments(records) {
    return (records || []).map(record => ({
        bucket: record.bucket,
        id: record.id,
        name: record.name,
        size: record.size,
        mimeType: record.mimeType || '',
        kind: record.kind,
    }))
}

/**
 * The staged refs of an in-flight or failed send snapshot, in send order
 * (empty for a legacy snapshot).
 *
 * @param {{attachments?: object[]}|null} snapshot
 * @returns {Array<{bucket: string, id: string}>}
 */
export function snapshotAttachmentRefs(snapshot) {
    return (snapshot?.attachments || [])
        .filter(attachment => attachment?.bucket && attachment.id)
        .map(attachment => ({ bucket: attachment.bucket, id: attachment.id }))
}

/**
 * Attachment fields of an optimistic or failed-send bubble (§9.4): the total
 * count (matching) and one item per attachment (name, display kind). An image
 * thumbnail comes from a local object URL only (`previewUrl` starting with
 * `blob:`), never from the staging content endpoint: the server releases
 * those entries at delivery.
 *
 * @param {Array<{id: string, name: string, kind: string, previewUrl?: string}>} attachments
 * @returns {{attachmentCount: number, attachmentItems: Array<{id: string, name: string, kind: string, src: string|null}>}}
 */
export function optimisticAttachmentFields(attachments) {
    const list = attachments || []
    return {
        attachmentCount: list.length,
        attachmentItems: list.map(attachment => ({
            id: attachment.id,
            name: attachment.name,
            kind: attachment.kind,
            src: typeof attachment.previewUrl === 'string' && attachment.previewUrl.startsWith('blob:')
                ? attachment.previewUrl
                : null,
        })),
    }
}

/**
 * True when the composer may send its attachments: every record is `ready`
 * (D6). An empty list never blocks. A record with no runtime state yet (its
 * `status/` answer is pending) is not ready.
 *
 * @param {Array<{id: string}>} records
 * @param {object} runtimeStates - `id → {state, …}`
 * @returns {boolean}
 */
export function canSendAttachments(records, runtimeStates) {
    return (records || []).every(record => runtimeStates?.[record.id]?.state === ATTACHMENT_STATE.READY)
}

/**
 * True when the composer may send (D6, §9.6): every staged record is `ready`
 * and no legacy media is left. A legacy media still in the composer (its
 * migration pending or failed) blocks Send: the composer never sends legacy
 * `images` / `documents` (Codex drops documents), only staged refs.
 *
 * @param {Array<{id: string}>} records
 * @param {object} runtimeStates - `id → {state, …}`
 * @param {number} legacyCount - legacy medias the composer still shows
 * @returns {boolean}
 */
export function composerAttachmentsReady(records, runtimeStates, legacyCount) {
    return !legacyCount && canSendAttachments(records, runtimeStates)
}

/**
 * Record legacy medias that cannot be decoded (§9.6), per session, without
 * duplicates. Memory only: the next start decodes the rows again.
 *
 * @param {object} failed - reactive `sessionId → string[]` (the store's `legacyFailedIds`)
 * @param {string} sessionId
 * @param {string[]} ids
 */
export function addLegacyFailures(failed, sessionId, ids) {
    if (!sessionId || !ids?.length) return
    const current = failed[sessionId] || []
    const fresh = ids.filter(id => id && !current.includes(id))
    if (fresh.length) failed[sessionId] = [...current, ...fresh]
}

/**
 * Forget the decode failure of one legacy media (its chip was removed).
 *
 * @param {object} failed - `sessionId → string[]`
 * @param {string} mediaId
 */
export function clearLegacyFailure(failed, mediaId) {
    for (const [sessionId, ids] of Object.entries(failed)) {
        if (!ids.includes(mediaId)) continue
        const rest = ids.filter(id => id !== mediaId)
        if (rest.length) failed[sessionId] = rest
        else delete failed[sessionId]
    }
}

/**
 * The undecodable legacy medias a composer still shows as legacy chips.
 *
 * @param {object} failed - `sessionId → string[]`
 * @param {string} sessionId
 * @param {Iterable<string>|null|undefined} legacyIds - ids of the session's legacy chips
 * @returns {number}
 */
export function legacyFailedCount(failed, sessionId, legacyIds) {
    const ids = failed[sessionId]
    if (!ids?.length || !legacyIds) return 0
    const shown = new Set(legacyIds)
    return ids.filter(id => shown.has(id)).length
}

/**
 * The composer attachment badge: its label, how many attachments need the
 * user (a failed or missing chip, an undecodable legacy media that only Remove
 * clears), and its variant (`danger` when any does).
 *
 * @param {{count: number, chipStates?: string[], legacyFailed?: number, ready?: boolean}} options
 * @returns {{label: string, attention: number, variant: 'danger'|'primary'}}
 */
export function attachmentBadge({ count, chipStates = [], legacyFailed = 0, ready = true }) {
    const failedChips = chipStates.filter(state => state === ATTACHMENT_STATE.FAILED || state === ATTACHMENT_STATE.MISSING).length
    const attention = failedChips + legacyFailed
    const parts = [`${count} file${count > 1 ? 's' : ''} attached`]
    if (failedChips) parts.push(`${failedChips} need${failedChips > 1 ? '' : 's'} attention`)
    if (legacyFailed) {
        parts.push(legacyFailed > 1
            ? `${legacyFailed} older attachments could not be converted — remove them`
            : '1 older attachment could not be converted — remove it')
    }
    if (!attention) {
        if (chipStates.includes(ATTACHMENT_STATE.UPLOADING)) parts.push('uploading')
        else if (!ready) parts.push('preparing older attachments')
    }
    return { label: parts.join(' · '), attention, variant: attention ? 'danger' : 'primary' }
}

/**
 * The attachment fields of a `send_message` frame (spec §8): `attachments`,
 * the `{bucket, id}` refs in the given (composer display) order, or nothing.
 *
 * @param {Array<{bucket: string, id: string}>} records
 * @returns {{attachments?: Array<{bucket: string, id: string}>}}
 */
export function attachmentPayloadFields(records) {
    if (!records?.length) return {}
    return { attachments: records.map(record => ({ bucket: record.bucket, id: record.id })) }
}

/**
 * Set the attachment fields of a `send_message` frame in place: with
 * attachments, the ordered refs and never the legacy `images` / `documents`
 * (mutually exclusive, §8). Without attachments, the frame is left as is.
 *
 * @param {object} payload
 * @param {object[]} records - the composer records, in send order
 * @returns {object} the same payload
 */
export function setAttachmentPayloadFields(payload, records) {
    if (records?.length) {
        delete payload.images
        delete payload.documents
        Object.assign(payload, attachmentPayloadFields(records))
    }
    return payload
}

/**
 * Send one composer message (§9.4). With attachments, the frame carries their
 * ordered refs and never the legacy `images` / `documents` fields (mutually
 * exclusive, §8). Only after a successful socket send: `register` receives
 * the sent attachments' metadata (with the local `previewUrl` of each, for the
 * optimistic bubble), then `forget` drops exactly the sent records locally
 * (never a release: the server owns the entries now). A failed socket send
 * changes nothing, so the draft stays intact.
 *
 * @param {object} options
 * @param {object} options.payload - the frame; attachment fields are set on it
 * @param {object[]} options.records - the composer records, in send order
 * @param {(payload: object) => boolean} options.send
 * @param {((attachments: object[]) => void)|null} [options.register]
 * @param {(ids: string[]) => unknown} options.forget
 * @param {(id: string) => string|null} [options.previewUrlFor]
 * @returns {boolean} the socket send result
 */
export function sendComposerMessage({ payload, records, send, register = null, forget, previewUrlFor = () => null }) {
    const sent = [...(records || [])]
    setAttachmentPayloadFields(payload, sent)
    // A socket that throws did not send the frame: same outcome as a refusal.
    let dispatched = false
    try { dispatched = send(payload) } catch { /* nothing left the socket */ }
    if (!dispatched) return false
    if (register) {
        register(snapshotAttachments(sent).map(attachment => ({
            ...attachment,
            previewUrl: previewUrlFor(attachment.id) || null,
        })))
    }
    if (sent.length) forget(sent.map(record => record.id))
    return true
}

/**
 * A file size for a chip: bytes, then KB, MB, GB (1024 based, one decimal).
 *
 * @param {number} bytes
 * @returns {string}
 */
export function formatAttachmentSize(bytes) {
    const size = Number.isFinite(bytes) && bytes > 0 ? bytes : 0
    if (size < 1024) return `${size} B`
    const units = ['KB', 'MB', 'GB', 'TB']
    let value = size / 1024
    let unit = 0
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024
        unit += 1
    }
    return `${value.toFixed(1)} ${units[unit]}`
}

function chipStatusText(state, retryable, pauseReason) {
    switch (state) {
        case ATTACHMENT_STATE.READY:
            return ''
        case ATTACHMENT_STATE.FAILED:
            return retryable ? 'Upload failed' : 'Upload interrupted, attach the file again'
        case ATTACHMENT_STATE.MISSING:
            return 'File no longer available'
        default:
            if (pauseReason === 'error') return 'Upload paused'
            if (pauseReason === 'network') return 'Waiting for the connection'
            return 'Uploading'
    }
}

/**
 * One composer chip (§9.3) for `MediaThumbnailGroup`: the record fields, its
 * upload state, and how to preview it. An image thumbnail and a text preview
 * come from the local object URL while the `File` is in memory, else from the
 * staging content endpoint once the entry is `ready`. Other kinds show an
 * icon. `type` is the legacy media family the preview dialog understands
 * (`image`, `txt`, `pdf`, or `other`). Retry is offered for a failed upload
 * whose `File` is in memory, and for a transfer the controller paused on an
 * error. No native/file indicator (D7).
 *
 * @param {{id: string, bucket: string, name: string, size: number, kind: string}} record
 * @param {{state?: string, progress?: number, retryable?: boolean, pauseReason?: string|null}|null} runtime
 * @param {{previewUrl?: string|null}} [options]
 * @returns {object}
 */
export function attachmentChipItem(record, runtime, { previewUrl = null } = {}) {
    const state = runtime?.state || ATTACHMENT_STATE.UPLOADING
    const pauseReason = runtime?.pauseReason || null
    const retryable = state === ATTACHMENT_STATE.FAILED
        ? !!runtime?.retryable
        : state === ATTACHMENT_STATE.UPLOADING && pauseReason === 'error'
    const remote = state === ATTACHMENT_STATE.READY ? attachmentContentUrl(record) : null
    let type = 'other'
    let src = null
    let textUrl = null
    if (record.kind === 'image') {
        src = previewUrl || remote
        if (src) type = 'image'
    } else if (record.kind === 'text') {
        textUrl = previewUrl || remote
        if (textUrl) type = 'txt'
    } else if (record.kind === 'PDF') {
        type = 'pdf'
    }
    return {
        id: record.id,
        name: record.name,
        size: record.size,
        sizeLabel: formatAttachmentSize(record.size),
        kind: record.kind,
        state,
        progress: runtime?.progress ?? 0,
        retryable,
        statusText: chipStatusText(state, !!runtime?.retryable, pauseReason),
        icon: attachmentKindIcon(record.kind),
        type,
        src,
        textUrl,
    }
}

/**
 * Read the start of a text file for a preview: at most `limit` bytes, decoded
 * as UTF-8 (a character cut by the limit is dropped). A local object URL or
 * the staging content endpoint.
 *
 * @param {string} url
 * @param {{fetch: Function, limit: number}} options
 * @returns {Promise<{text: string, truncated: boolean}>} rejects on a failed answer; an answer
 *     without a body is an empty, untruncated preview
 */
export async function readTextPreview(url, { fetch: fetchFn, limit }) {
    const res = await fetchFn(url)
    if (!res.ok) throw new Error(`Preview failed (${res.status})`)
    // A 204 or an empty answer has no body stream: an empty preview.
    if (!res.body) return { text: '', truncated: false }
    const decoder = new TextDecoder()
    let text = ''
    let read = 0
    let truncated = false
    const reader = res.body.getReader()
    try {
        for (;;) {
            const { done, value } = await reader.read()
            if (done) break
            const room = limit - read
            if (value.length > room) {
                text += decoder.decode(value.subarray(0, room), { stream: true })
                truncated = true
                break
            }
            read += value.length
            text += decoder.decode(value, { stream: true })
        }
    } finally {
        if (truncated) reader.cancel().catch(() => {})
        else reader.releaseLock()
    }
    if (!truncated) text += decoder.decode()
    return { text, truncated }
}

/**
 * Collector of every staged ref held for some session ids (ephemeral Discard,
 * §6.1.4): their draft records, in-flight and failed-send snapshots in
 * memory, then the stored draft records and snapshots (a discard hydrated
 * before them). The memory part is read synchronously when the collector is
 * called, so the caller may remove its local state right after the call.
 *
 * @param {object} deps
 * @param {object} deps.records - `sessionId → {id → record}`
 * @param {() => Iterable<object>} deps.inflightEntries
 * @param {() => Iterable<object>} deps.failedEntries
 * @param {{getDraftAttachmentsBySession: Function, getAllInflightSends: Function}} deps.storage
 * @returns {(ids: string[]) => Promise<Array<{bucket: string, id: string}>>}
 */
export function createAttachmentRefCollector({ records, inflightEntries, failedEntries, storage }) {
    return function collectAttachmentRefs(ids) {
        const wanted = new Set(ids.filter(Boolean))
        const refs = []
        for (const id of wanted) {
            for (const record of Object.values(records[id] || {})) refs.push({ bucket: record.bucket, id: record.id })
        }
        for (const entry of [...inflightEntries(), ...failedEntries()]) {
            if (wanted.has(entry?.sessionId)) refs.push(...snapshotAttachmentRefs(entry))
        }
        return (async () => {
            for (const id of wanted) {
                try {
                    for (const row of await storage.getDraftAttachmentsBySession(id)) refs.push({ bucket: row.bucket, id: row.id })
                } catch (error) {
                    console.warn('Failed to read draft attachment records:', error)
                }
            }
            try {
                for (const entry of Object.values(await storage.getAllInflightSends())) {
                    if (wanted.has(entry?.sessionId)) refs.push(...snapshotAttachmentRefs(entry))
                }
            } catch (error) {
                console.warn('Failed to read in-flight send snapshots:', error)
            }
            return uniqueRefs(refs)
        })()
    }
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
 * @param {object} [deps.aliases] - reactive `draftId → canonicalId` (bound drafts)
 * @param {{saveDraftAttachment: Function, saveDraftAttachments: Function,
 *     deleteDraftAttachment: Function,
 *     deleteLegacyMedia: (id: string) => Promise<void>}} deps.storage -
 *     `saveDraftAttachments` writes several records in one readwrite transaction
 * @param {{tabId: string, entries: Map, startUploads: Function, cancel: Function,
 *     onCompleted: Function, isStalled: Function}} deps.uploads - the uploads store
 * @param {Function} deps.fetch - `apiFetch`
 * @param {() => string} deps.uuid
 * @param {(n: number) => string} deps.randomHex
 * @param {{create: (file: File) => string, revoke: (url: string) => void}} [deps.objectUrls] -
 *     object URL factory of the local previews (default: `URL.createObjectURL`)
 * @param {() => Iterable<string>} [deps.previewUrlsInUse] - reactive: the
 *     preview URLs other users still show (optimistic bubbles). A URL is
 *     revoked once its record is gone and no such user holds it.
 * @param {Function} [deps.setTimeout] - timer of the status polling (default: global)
 * @param {Function} [deps.clearTimeout]
 */
export function createComposerAttachments(deps) {
    const { records, runtime, storage, uploads, uuid, randomHex } = deps
    const aliases = deps.aliases || {}
    const fetchFn = deps.fetch
    const objectUrls = deps.objectUrls || {
        create: file => URL.createObjectURL(file),
        revoke: url => URL.revokeObjectURL(url),
    }
    const previewUrlsInUse = deps.previewUrlsInUse || (() => [])
    const setTimer = deps.setTimeout || ((fn, ms) => globalThis.setTimeout(fn, ms))
    const clearTimer = deps.clearTimeout || (id => globalThis.clearTimeout(id))

    /** The session that owns a composer id: its canonical id once bound. */
    function resolveOwner(sessionId) {
        return aliases[sessionId] || sessionId
    }

    function maxPosition(list) {
        return list.reduce((max, record) => Math.max(max, record.position), -1)
    }

    function samePersisted(a, b) {
        return a.sessionId === b.sessionId && a.position === b.position && a.bucket === b.bucket
    }

    /** id → File, for previews and chip Retry (memory only). */
    const files = new Map()
    /** id → {clientId, pending, seen}: the current upload attempt. */
    const attempts = new Map()
    /** id → counter, bumped by every new attempt and every removal. */
    const generations = new Map()
    /** id → local object URL of an image or text `File` (chip and bubble previews). */
    const previews = new Map()
    /** Object URLs whose record is gone, kept while an optimistic bubble shows them. */
    const releasedPreviews = new Set()
    /** id → cleanups run once the attachment is `ready` (legacy row deletion, §9.6). */
    const readyCleanups = new Map()
    /** Ids a legacy migration is deciding about: no other status answer applies meanwhile. */
    const migrating = new Set()
    /**
     * id → client ids of completions that matched no attempt of the chip yet:
     * a broadcast completion can land before the `status/` answer that names
     * its attempt (the controller keeps no terminal entry to look up later).
     */
    const unmatchedCompletions = new Map()

    function bump(id) {
        generations.set(id, (generations.get(id) || 0) + 1)
    }

    function createPreview(id, file, kind) {
        if (kind !== 'image' && kind !== 'text') return
        try {
            previews.set(id, objectUrls.create(file))
        } catch (error) {
            console.warn('Attachment preview unavailable', error)
        }
    }

    /** Revoke every released preview URL no optimistic bubble holds. */
    function sweepPreviewUrls() {
        if (!releasedPreviews.size) return
        const used = new Set(previewUrlsInUse())
        for (const url of releasedPreviews) {
            if (used.has(url)) continue
            releasedPreviews.delete(url)
            objectUrls.revoke(url)
        }
    }

    /** The composer no longer holds this preview: revoke it unless a bubble shows it. */
    function releasePreview(id) {
        const url = previews.get(id)
        if (!url) return
        previews.delete(id)
        releasedPreviews.add(url)
        sweepPreviewUrls()
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
        if (state === ATTACHMENT_STATE.READY) {
            rt.progress = 100
            runReadyCleanups(id)
        }
    }

    function runReadyCleanups(id) {
        const cleanups = readyCleanups.get(id)
        if (!cleanups) return
        readyCleanups.delete(id)
        for (const cleanup of cleanups) {
            Promise.resolve()
                .then(cleanup)
                .catch(error => console.warn('Attachment readiness cleanup failed', error))
        }
    }

    /** Run `cleanup` once the attachment is `ready` (at once if it is); dropped with the record. */
    function whenReady(id, cleanup) {
        if (!findRecord(id)) return
        if (!readyCleanups.has(id)) readyCleanups.set(id, [])
        readyCleanups.get(id).push(cleanup)
        if (runtime[id]?.state === ATTACHMENT_STATE.READY) runReadyCleanups(id)
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
        readyCleanups.delete(id)
        unmatchedCompletions.delete(id)
        releasePreview(id)
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

    /**
     * A rejected attempt fails its chip. A creation answered `410` means the
     * entry was released (removed in another tab, §6.1.4): no attempt can
     * ever succeed, so the chip is `missing` (Remove only), never Retry.
     */
    function onRejected(id, info) {
        if (!isCurrentAttempt(id, info.client_id)) return
        attempts.get(id).pending = false
        setState(id, info.status === 410 ? ATTACHMENT_STATE.MISSING : ATTACHMENT_STATE.FAILED)
    }

    function onCompleted(record) {
        if (record?.origin?.panel !== COMPOSER_PANEL || typeof record.origin.key !== 'string') return
        const id = record.origin.key.slice(record.origin.key.lastIndexOf('/') + 1)
        const rt = runtime[id]
        if (!rt || rt.state === ATTACHMENT_STATE.MISSING) return
        if (!shouldAcceptCompletion(attempts.get(id)?.clientId ?? null, record.client_id)) {
            if (typeof record.client_id === 'string') {
                if (!unmatchedCompletions.has(id)) unmatchedCompletions.set(id, new Set())
                unmatchedCompletions.get(id).add(record.client_id)
            }
            return
        }
        unmatchedCompletions.delete(id)
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
     * A draft id already bound to a canonical id (alias) adds to the canonical
     * composer, with the draft id as bucket. While the draft's own records
     * have not moved yet (`rebindDraftAttachments` pending), the new record
     * joins their group (`joinedFrom`, memory only) so it keeps its place
     * after them.
     *
     * @param {string} sessionId
     * @param {File} file
     * @returns {Promise<object>} the record
     */
    async function addAttachment(sessionId, file) {
        const id = uuid()
        const owner = resolveOwner(sessionId)
        const draftRecords = owner !== sessionId ? Object.values(records[sessionId] || {}) : []
        const joinedFrom = draftRecords.length ? sessionId : null
        const group = joinedFrom
            ? [...draftRecords, ...Object.values(records[owner] || {}).filter(r => r.joinedFrom === sessionId)]
            : Object.values(records[owner] || {})
        const record = {
            id,
            sessionId: owner,
            bucket: sessionId,
            position: maxPosition(group) + 1,
            name: file.name,
            size: file.size,
            mimeType: file.type || '',
            kind: getDisplayKind(file),
        }
        if (joinedFrom) record.joinedFrom = joinedFrom
        // Before the record is published: its chip reads the preview at once.
        createPreview(id, file, record.kind)
        if (!records[owner]) records[owner] = {}
        records[owner][id] = record
        createRuntime(id)
        files.set(id, file)
        const saved = plainRecord(record)
        try {
            await storage.saveDraftAttachment(saved)
        } catch (error) {
            dropLocal(id)
            throw error
        }
        if (!findRecord(id)) {
            // Removed during the write: make sure no row survives.
            storage.deleteDraftAttachment(id).catch(() => {})
            return record
        }
        // Rehomed by a binding during the write, whose own transaction may
        // have run first: write the current owner and position again.
        const current = plainRecord(findRecord(id))
        if (!samePersisted(saved, current)) {
            await storage.saveDraftAttachment(current).catch(error =>
                console.warn('Failed to save rehomed attachment record:', error))
        }
        await startAttempt(findRecord(id))
        return record
    }

    /**
     * Rehome the unsent records of a draft onto its canonical id (Codex
     * `bindDraftSession`, ephemeral binding and recovery), before the old
     * draft is deleted. The records keep their id, bucket, relative order,
     * `File`, upload attempt and chip state (all keyed by attachment id); they
     * are appended after the records the target already holds. The move is
     * published in memory at once (the order `composerRecordsFor` already
     * showed through the alias), then written in one readwrite transaction.
     *
     * @param {string} oldSessionId
     * @param {string} newSessionId
     * @returns {Promise<object[]>} the moved records
     */
    async function rebindDraftAttachments(oldSessionId, newSessionId) {
        if (!oldSessionId || !newSessionId || oldSessionId === newSessionId) return []
        const target = Object.values(records[newSessionId] || {})
        const group = [
            ...Object.values(records[oldSessionId] || {}),
            ...target.filter(record => record.joinedFrom === oldSessionId),
        ].sort(byPosition)
        if (!group.length) return []
        let position = maxPosition(target.filter(record => record.joinedFrom !== oldSessionId)) + 1
        if (!records[newSessionId]) records[newSessionId] = {}
        for (const record of group) {
            record.sessionId = newSessionId
            record.position = position++
            delete record.joinedFrom
            records[newSessionId][record.id] = record
        }
        delete records[oldSessionId]
        try {
            await storage.saveDraftAttachments(group.map(plainRecord))
        } catch (error) {
            console.warn('Failed to persist rebound attachment records:', error)
        }
        return group
    }

    /**
     * Edit of a failed send (§9.5): re-create its records with the same `id`
     * and `bucket` in the composer of `sessionId` (through its alias), after
     * the attachments already there. Touched at once as draft refs, then their
     * chip state comes from `status/` (an entry released at delivery shows
     * `missing`). An attachment already held by a composer is skipped.
     *
     * @param {string} sessionId
     * @param {Array<{bucket: string, id: string, name: string, size: number, mimeType: string, kind: string}>} attachments
     * @returns {Promise<object[]>} the restored records
     */
    async function restoreDraftAttachmentRefs(sessionId, attachments) {
        const owner = resolveOwner(sessionId)
        const fresh = []
        const seen = new Set()
        for (const attachment of attachments || []) {
            if (!attachment?.id || !attachment.bucket || seen.has(attachment.id) || findRecord(attachment.id)) continue
            seen.add(attachment.id)
            fresh.push(attachment)
        }
        if (!fresh.length) return []
        let position = maxPosition(Object.values(records[owner] || {})) + 1
        const restored = fresh.map(attachment => ({
            id: attachment.id,
            sessionId: owner,
            bucket: attachment.bucket,
            position: position++,
            name: attachment.name,
            size: attachment.size,
            mimeType: attachment.mimeType || '',
            kind: attachment.kind,
        }))
        if (!records[owner]) records[owner] = {}
        for (const record of restored) {
            bump(record.id)
            attempts.delete(record.id)
            records[owner][record.id] = record
            createRuntime(record.id)
        }
        try {
            await storage.saveDraftAttachments(restored.map(plainRecord))
        } catch (error) {
            for (const record of restored) dropLocal(record.id)
            throw error
        }
        try {
            await touchRefs(toAttachmentRefs(restored), 'draft', fetchFn)
        } catch (error) {
            console.warn('Attachment touch failed', error)
        }
        const live = restored.map(record => findRecord(record.id)).filter(Boolean)
        await reconcileRecords(live, { hydrate: true })
        return live
    }

    /**
     * Chip Retry. A transfer the controller paused on an error resumes the
     * same upload (same client id, the controller's own Retry, §9.2). A
     * `failed` attachment whose `File` is in memory: cancel the previous local
     * attempt, then a new attempt (new client id) into the same entry (the
     * server settles any other attempt).
     *
     * @param {string} id
     * @returns {Promise<boolean>} false when Retry is not possible
     */
    async function retryAttachment(id) {
        const rt = runtime[id]
        const record = findRecord(id)
        if (rt && record && rt.state === ATTACHMENT_STATE.UPLOADING && rt.pauseReason === 'error' && rt.uploadKey) {
            uploads.retry(rt.uploadKey)
            return true
        }
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
     * With `ids` (the post-send clear), only those records are forgotten,
     * wherever they live (a bound draft shows records of two session ids): an
     * attachment added after the send stays in the composer.
     *
     * @param {string} sessionId
     * @param {{ids?: string[]}} [options]
     * @returns {Promise<void>}
     */
    async function forgetAttachments(sessionId, { ids } = {}) {
        const forgotten = ids
            ? [...ids]
            : Object.values(records[sessionId] || {}).map(record => record.id)
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
        return reconcileRecords(allRecords(), { hydrate })
    }

    async function reconcileRecordsOnce(list, { hydrate }) {
        const targets = []
        for (const record of list) {
            if (hasLiveLocalUpload(record.id) || migrating.has(record.id)) continue
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
            if (migrating.has(id)) return
            if (status?.bucket !== record.bucket || status?.id !== id) return
            applyStatusAnswer(record, status, previous)
        })
        syncUploadStates()
    }

    async function reconcileRecords(list, options) {
        try {
            await reconcileRecordsOnce(list, options)
        } finally {
            ensureStatusPolling()
        }
    }

    // Status polling (§9.2): a chip mirroring a server-side upload that this
    // tab does not run learns its end only from a broadcast completion, which
    // can precede this page's WebSocket (reload while the server finalizes).
    // So `status/` is asked again, with a growing delay, until no such chip is left.
    const POLL_FIRST_MS = 2_000
    const POLL_MAX_MS = 10_000
    let pollTimer = null
    let pollDelay = POLL_FIRST_MS

    function mirroredRecords() {
        return allRecords().filter(record =>
            runtime[record.id]?.state === ATTACHMENT_STATE.UPLOADING
            && !hasLiveLocalUpload(record.id)
            && !migrating.has(record.id))
    }

    function ensureStatusPolling() {
        if (pollTimer !== null) return
        if (!mirroredRecords().length) {
            pollDelay = POLL_FIRST_MS
            return
        }
        pollTimer = setTimer(async () => {
            pollTimer = null
            const list = mirroredRecords()
            pollDelay = Math.min(POLL_MAX_MS, Math.round(pollDelay * 1.5))
            if (list.length) await reconcileRecords(list, { hydrate: false })
            else ensureStatusPolling()
        }, pollDelay)
    }

    /** Chip state and current attempt of one record from its `status/` item (§9.2). */
    function applyStatusAnswer(record, status, previous) {
        const id = record.id
        const rt = runtime[id] || createRuntime(id)
        const { state } = mapAttachmentStatus(status, previous)
        const clientId = status.state === 'uploading' && typeof status.client_id === 'string' ? status.client_id : null
        const sameAttempt = !!clientId && attempts.get(id)?.clientId === clientId
        attempts.set(id, { clientId, pending: false, seen: sameAttempt && attempts.get(id).seen })
        rt.clientId = clientId
        rt.uploadKey = null
        rt.pauseReason = null
        rt.progress = state === ATTACHMENT_STATE.UPLOADING && record.size > 0
            ? Math.min(100, Math.floor((100 * (status.offset || 0)) / record.size))
            : 0
        // The attempt the answer names already completed while it was in flight.
        const completedMeanwhile = !!clientId && !!unmatchedCompletions.get(id)?.has(clientId)
        unmatchedCompletions.delete(id)
        setState(id, completedMeanwhile ? ATTACHMENT_STATE.READY : state)
    }

    /**
     * Records of legacy medias (§9.6): a record already holding a media id is
     * reused (its position and bucket unchanged); otherwise the media is
     * claimed from the legacy chips (`claim` false: removed meanwhile, skipped)
     * and a record is created with the media id, `bucket = sessionId`, after
     * the composer's records. Published synchronously with the claim, then the
     * new records are written in one transaction. The decoded `File` is kept
     * in memory (previews, uploads, Retry). Ids already under migration are
     * skipped.
     *
     * @param {string} sessionId
     * @param {Array<{media: object, file: File}>} entries
     * @param {{claim: (media: object) => boolean, unclaim: (media: object) => void}} legacy
     * @returns {Promise<object[]>} the records, marked as under migration
     */
    async function adoptMigratedRecords(sessionId, entries, { claim, unclaim }) {
        const owner = resolveOwner(sessionId)
        let position = maxPosition(Object.values(records[owner] || {})) + 1
        const adopted = []
        const created = []
        for (const { media, file } of entries) {
            const id = media.id
            if (migrating.has(id)) continue
            let record = findRecord(id)
            if (!record) {
                if (!claim(media)) continue
                if (!records[owner]) records[owner] = {}
                records[owner][id] = {
                    id,
                    sessionId: owner,
                    bucket: sessionId,
                    position: position++,
                    name: file.name,
                    size: file.size,
                    mimeType: file.type || '',
                    kind: getDisplayKind(file),
                }
                record = findRecord(id)
                createRuntime(id)
                created.push({ media, record })
            } else if (!runtime[id]) {
                createRuntime(id)
            }
            if (!files.has(id)) {
                files.set(id, file)
                if (!previews.has(id)) createPreview(id, file, record.kind)
                if (runtime[id].state === ATTACHMENT_STATE.FAILED) runtime[id].retryable = true
            }
            migrating.add(id)
            adopted.push(record)
        }
        if (created.length) {
            try {
                await storage.saveDraftAttachments(created.map(({ record }) => plainRecord(record)))
            } catch (error) {
                for (const record of adopted) migrating.delete(record.id)
                for (const { media, record } of created) {
                    if (findRecord(record.id) === record) dropLocal(record.id)
                    unclaim(media)
                }
                throw error
            }
        }
        const current = []
        for (const record of adopted) {
            if (findRecord(record.id) === record) {
                current.push(record)
            } else {
                // Removed during the write: no row survives it.
                migrating.delete(record.id)
                if (created.some(entry => entry.record === record)) storage.deleteDraftAttachment(record.id).catch(() => {})
            }
        }
        return current
    }

    /**
     * Migrate legacy medias into the composer of `sessionId` (startup and the
     * Edit of a legacy failed send, §9.5, §9.6): see `migrateLegacyAttachments`.
     *
     * @param {string} sessionId - the migration session id (the new records' bucket)
     * @param {object[]} medias - legacy `draftMedias` rows
     * @param {string[]} mediaIds - the draft's media order
     * @param {{claim: (media: object) => boolean, unclaim: (media: object) => void,
     *     undecodable?: (ids: string[]) => void}} legacy -
     *     take a media out of the legacy chips (false when it is gone), or put it back;
     *     `undecodable` receives the ids of the medias that cannot be decoded (they stay legacy chips)
     * @returns {Promise<void>} rejects when the records cannot be stored
     */
    function migrateLegacy(sessionId, medias, mediaIds, legacy) {
        return migrateLegacyAttachments({
            sessionId,
            medias,
            mediaIds,
            dependencies: {
                tabId: uploads.tabId,
                reportUndecodable: ids => legacy.undecodable?.(ids),
                adoptRecords: (id, entries) => adoptMigratedRecords(id, entries, legacy),
                hasLiveLocalUpload,
                isCurrent: record => findRecord(record.id) === record,
                whenReady,
                deleteLegacyMedia: id => storage.deleteLegacyMedia(id),
                statusRefs: refs => statusRefs(refs, fetchFn),
                applyStatus: (record, status) => {
                    applyStatusAnswer(record, status, undefined)
                    syncUploadStates()
                },
                markFailed: id => setState(id, ATTACHMENT_STATE.FAILED),
                startUpload: record => startAttempt(record),
                finish: list => {
                    for (const record of list) migrating.delete(record.id)
                },
            },
        })
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

    /** The local preview object URL of an attachment (image or text File in memory), or null. */
    function getPreviewUrl(id) {
        return previews.get(id) || null
    }

    const stopCompleted = uploads.onCompleted(onCompleted)
    // An optimistic bubble that stops showing a released preview frees it.
    const stopPreviewWatch = watch(
        () => [...previewUrlsInUse()].join('\n'),
        () => sweepPreviewUrls(),
    )
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

    /** Stop every watcher and revoke every object URL still held (HMR, teardown). */
    function dispose() {
        if (pollTimer !== null) clearTimer(pollTimer)
        pollTimer = null
        stopWatch()
        stopCompleted()
        stopPreviewWatch()
        for (const url of [...previews.values(), ...releasedPreviews]) objectUrls.revoke(url)
        previews.clear()
        releasedPreviews.clear()
    }

    return {
        addAttachment,
        forgetAttachments,
        releaseAttachments,
        rebindDraftAttachments,
        restoreDraftAttachmentRefs,
        migrateLegacy,
        retryAttachment,
        reconcileAttachmentStatuses,
        touchHeldAttachments,
        hydrate,
        getRecords,
        getFile,
        getPreviewUrl,
        syncUploadStates,
        dispose,
    }
}
