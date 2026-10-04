// frontend/src/utils/attachmentMigration.js
// Migration of legacy draft medias (spec 2026-10-03 §9.6). Drafts saved
// before staged uploads hold their files encoded in the IndexedDB
// `draftMedias` store. At startup (and on the Edit of a legacy failed send,
// §9.5), each row becomes a `File` and a composer record with the same id,
// whose staging entry is uploaded only when the server does not already hold
// it. The row is deleted once the entry is ready; after a failure it stays,
// and the next start migrates it again.
//
// This module holds the decoding and the decisions. Every effect (records,
// storage, `status/`, uploads) comes from injected dependencies: the composer
// actions (`createComposerAttachments().migrateLegacy`) provide them.

import { isClientIdOfTab } from './uploads/ids.js'

const DEFAULT_MIME_TYPES = Object.freeze({
    image: 'application/octet-stream',
    pdf: 'application/pdf',
    txt: 'text/plain',
})

const BASE64_PATTERN = /^[A-Za-z0-9+/]*={0,2}$/

function decodeBase64(data) {
    const raw = String(data || '').replace(/^data:[^,]*;base64,/, '').replace(/\s+/g, '')
    if (!BASE64_PATTERN.test(raw) || raw.length % 4 === 1) throw new Error('Invalid base64 media data')
    const binary = atob(raw)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
    return bytes
}

/**
 * The `File` of a legacy draft media: `image` and `pdf` rows hold base64,
 * `txt` rows hold plain text (UTF-8). The original name and MIME type are
 * kept (the family default when none was stored).
 *
 * @param {{name: string, type: 'image'|'pdf'|'txt', mimeType?: string, data: string, createdAt?: number}} media
 * @returns {File}
 * @throws {Error} on an unknown type or undecodable data
 */
export function legacyMediaToFile(media) {
    if (!Object.hasOwn(DEFAULT_MIME_TYPES, media?.type)) throw new Error(`Unknown legacy media type: ${media?.type}`)
    const type = media.mimeType || DEFAULT_MIME_TYPES[media.type]
    const content = media.type === 'txt' ? String(media.data ?? '') : decodeBase64(media.data)
    const options = { type }
    if (Number.isFinite(media.createdAt)) options.lastModified = media.createdAt
    return new File([content], media.name || 'attachment', options)
}

/**
 * Legacy rows in migration order: the draft's `mediaIds` order, then the
 * remaining rows by `createdAt`.
 *
 * @param {object[]} medias
 * @param {string[]} [mediaIds]
 * @returns {object[]}
 */
export function orderLegacyMedias(medias, mediaIds) {
    const byId = new Map((medias || []).map(media => [media.id, media]))
    const ordered = []
    for (const id of mediaIds || []) {
        const media = byId.get(id)
        if (!media) continue
        ordered.push(media)
        byId.delete(id)
    }
    const rest = [...byId.values()].sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0))
    return [...ordered, ...rest]
}

/**
 * The legacy rows of each session, and the rows a composer still shows as
 * legacy chips (no composer record holds their id yet).
 *
 * @param {object[]} medias
 * @param {Set<string>} recordIds - ids of the stored composer records
 * @returns {{bySession: Map<string, object[]>, visible: object[]}}
 */
export function groupLegacyMedias(medias, recordIds) {
    const bySession = new Map()
    const visible = []
    for (const media of medias || []) {
        if (!media?.id || !media.sessionId) continue
        if (!bySession.has(media.sessionId)) bySession.set(media.sessionId, [])
        bySession.get(media.sessionId).push(media)
        if (!recordIds.has(media.id)) visible.push(media)
    }
    return { bySession, visible }
}

/**
 * What the migration does with one entry, from its `status/` answer (§9.6):
 * - `ready`: `ready` / `promoted`, no upload (an earlier start did it);
 * - `defer`: `uploading` by another tab (taking it over would cancel that
 *   tab's upload), retried at the next start;
 * - `start`: `uploading` by this tab before a reload (the settle rule
 *   cancels the stalled attempt), or `missing`: a new attempt.
 *
 * @param {{state: string, client_id?: string}} status
 * @param {{tabId: string}} ctx
 * @returns {'ready'|'defer'|'start'}
 */
export function migrationDecision(status, { tabId }) {
    switch (status?.state) {
        case 'ready':
        case 'promoted':
            return 'ready'
        case 'uploading':
            return isClientIdOfTab(status.client_id, tabId) ? 'start' : 'defer'
        default:
            return 'start'
    }
}

/**
 * Migrate the legacy medias of one session (§9.6). Idempotent: a record
 * already holding a media id is reused (same position and bucket), never
 * duplicated; an attachment this tab is uploading starts nothing.
 *
 * New records get the media id, `bucket = sessionId` (the original migration
 * session id, kept afterwards) and the next positions of the composer.
 *
 * @param {object} options
 * @param {string} options.sessionId
 * @param {object[]} options.medias - legacy `draftMedias` rows
 * @param {string[]} [options.mediaIds] - the draft's media order
 * @param {object} options.dependencies
 * @param {string} options.dependencies.tabId - this tab's id
 * @param {(sessionId: string, entries: Array<{media: object, file: File}>) => Promise<object[]>} options.dependencies.adoptRecords -
 *     reuse or create (and persist) the records, File in memory; rejects on a storage failure
 * @param {(id: string) => boolean} options.dependencies.hasLiveLocalUpload
 * @param {(record: object) => boolean} options.dependencies.isCurrent - false once removed
 * @param {(id: string, cleanup: () => unknown) => void} options.dependencies.whenReady -
 *     readiness cleanup (dropped with the record)
 * @param {(id: string) => Promise<void>} options.dependencies.deleteLegacyMedia
 * @param {(refs: Array<{bucket: string, id: string}>) => Promise<object[]>} options.dependencies.statusRefs
 * @param {(record: object, status: object) => void} options.dependencies.applyStatus
 * @param {(id: string) => void} options.dependencies.markFailed
 * @param {(record: object) => Promise<void>} options.dependencies.startUpload - new attempt, new client id
 * @param {(records: object[]) => void} options.dependencies.finish - end of the migration of these records
 * @returns {Promise<void>} rejects when the records cannot be stored
 */
export async function migrateLegacyAttachments({ sessionId, medias, mediaIds, dependencies: deps }) {
    const entries = []
    for (const media of orderLegacyMedias(medias, mediaIds)) {
        try {
            entries.push({ media, file: legacyMediaToFile(media) })
        } catch (error) {
            // Kept as a legacy row (and chip): the user can still remove it.
            console.warn('Legacy draft media not migrated', media?.id, error)
        }
    }
    if (!entries.length) return
    const records = await deps.adoptRecords(sessionId, entries)
    if (!records.length) return
    try {
        // The row goes once the entry is ready, whoever makes it ready.
        for (const record of records) deps.whenReady(record.id, () => deps.deleteLegacyMedia(record.id))
        const targets = records.filter(record => !deps.hasLiveLocalUpload(record.id))
        if (!targets.length) return
        let statuses
        try {
            statuses = await deps.statusRefs(targets.map(record => ({ bucket: record.bucket, id: record.id })))
        } catch (error) {
            console.warn('Legacy media migration: status failed', error)
            // Failed with Retry (the File is in memory); the row stays.
            for (const record of targets) {
                if (deps.isCurrent(record) && !deps.hasLiveLocalUpload(record.id)) deps.markFailed(record.id)
            }
            return
        }
        const starts = []
        targets.forEach((record, index) => {
            const status = statuses[index]
            if (!deps.isCurrent(record) || deps.hasLiveLocalUpload(record.id)) return
            if (status?.bucket !== record.bucket || status?.id !== record.id) {
                deps.markFailed(record.id)
                return
            }
            if (migrationDecision(status, { tabId: deps.tabId }) === 'start') {
                starts.push(deps.startUpload(record))
            } else {
                deps.applyStatus(record, status)
            }
        })
        await Promise.all(starts)
    } finally {
        deps.finish(records)
    }
}
