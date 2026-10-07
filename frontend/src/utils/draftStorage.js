// frontend/src/utils/draftStorage.js
// IndexedDB wrapper for draft messages, draft sessions, draft medias and
// draft attachment records persistence

const DB_NAME = 'twicc'
// v9 (draftAttachments) and v10 (asyncQuestionDrafts) were created
// independently; v11 is above both and creates whichever store is missing.
const DB_VERSION = 11
const DRAFT_MESSAGES_STORE = 'draftMessages'
const DRAFT_SESSIONS_STORE = 'draftSessions'
const DRAFT_MEDIAS_STORE = 'draftMedias'
const CODE_COMMENTS_STORE = 'codeComments'
const INFLIGHT_SENDS_STORE = 'inflightSends'
const PENDING_REQUEST_DRAFTS_STORE = 'pendingRequestDrafts'
const DRAFT_ATTACHMENTS_STORE = 'draftAttachments'
const ASYNC_QUESTION_DRAFTS_STORE = 'asyncQuestionDrafts'

let dbPromise = null

// Blocked-upgrade report (the bootstrap shows a notice while it is true).
let storageBlocked = false
const blockedSubscribers = new Set()

function setStorageBlocked(blocked) {
    if (storageBlocked === blocked) return
    storageBlocked = blocked
    for (const callback of [...blockedSubscribers]) {
        try {
            callback(blocked)
        } catch (error) {
            console.error('Draft storage blocked subscriber failed', error)
        }
    }
}

/**
 * Subscribe to the blocked state of the database upgrade: true while another
 * tab keeps an older version open, false once the upgrade or open completes.
 * The callback receives the current state at once.
 *
 * @param {(blocked: boolean) => void} callback
 * @returns {() => void} unsubscribe
 */
export function subscribeDraftStorageBlocked(callback) {
    blockedSubscribers.add(callback)
    callback(storageBlocked)
    return () => blockedSubscribers.delete(callback)
}

/**
 * Opens/initializes the IndexedDB database (lazy singleton).
 *
 * A blocked upgrade (an older tab keeps its connection open) keeps the open
 * request pending: it completes by itself once the user closes that tab. A
 * newer version opened elsewhere closes this connection, so that tab's
 * upgrade is never blocked by this one; the next call opens again.
 * @returns {Promise<IDBDatabase>}
 */
export function getDb() {
    if (!dbPromise) {
        const opening = new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION)

            request.onerror = () => {
                setStorageBlocked(false)
                reject(request.error)
            }
            request.onblocked = () => setStorageBlocked(true)
            request.onsuccess = () => {
                const db = request.result
                db.onversionchange = () => {
                    db.close()
                    if (dbPromise === opening) dbPromise = null
                }
                setStorageBlocked(false)
                resolve(db)
            }

            request.onupgradeneeded = (event) => {
                const db = event.target.result
                if (!db.objectStoreNames.contains('ephemeralControls')) {
                    db.createObjectStore('ephemeralControls')
                }
                // Create draftMessages store if not exists (v1)
                if (!db.objectStoreNames.contains(DRAFT_MESSAGES_STORE)) {
                    db.createObjectStore(DRAFT_MESSAGES_STORE)
                }
                // Create draftSessions store if not exists (v2)
                if (!db.objectStoreNames.contains(DRAFT_SESSIONS_STORE)) {
                    db.createObjectStore(DRAFT_SESSIONS_STORE)
                }
                // Create draftMedias store if not exists (v3)
                if (!db.objectStoreNames.contains(DRAFT_MEDIAS_STORE)) {
                    const store = db.createObjectStore(DRAFT_MEDIAS_STORE, { keyPath: 'id' })
                    // Index on sessionId to retrieve all medias for a session
                    store.createIndex('sessionId', 'sessionId', { unique: false })
                }
                // Recreate codeComments store with compound keyPath (v5).
                // Guarded on oldVersion: an unconditional delete+recreate
                // would wipe existing comments on every later upgrade.
                if (event.oldVersion < 5) {
                    if (db.objectStoreNames.contains(CODE_COMMENTS_STORE)) {
                        db.deleteObjectStore(CODE_COMMENTS_STORE)
                    }
                    db.createObjectStore(CODE_COMMENTS_STORE, {
                        keyPath: ['projectId', 'sessionId', 'filePath', 'source', 'sourceRef', 'lineNumber']
                    })
                }
                // Create inflightSends store if not exists (v6) — in-flight
                // send snapshots for the send-failure recovery audit
                // (see utils/inflightStorage.js)
                if (!db.objectStoreNames.contains(INFLIGHT_SENDS_STORE)) {
                    db.createObjectStore(INFLIGHT_SENDS_STORE)
                }
                // Create pendingRequestDrafts store if not exists (v7) —
                // in-progress answers to a pending request (question widget,
                // elicitation form, deny reason, edited tool input). Compound
                // key: a request id is already unique on its own, the session
                // id makes a cross-session collision structurally impossible
                // and scopes the per-session sweep.
                // (see utils/pendingRequestDraftStorage.js)
                if (!db.objectStoreNames.contains(PENDING_REQUEST_DRAFTS_STORE)) {
                    db.createObjectStore(PENDING_REQUEST_DRAFTS_STORE, {
                        keyPath: ['sessionId', 'requestId']
                    })
                }
                // Create draftAttachments store if not exists (v9) — one
                // record per composer attachment staged on the server, by
                // reference only (no file content). See composerAttachments.js.
                if (!db.objectStoreNames.contains(DRAFT_ATTACHMENTS_STORE)) {
                    const store = db.createObjectStore(DRAFT_ATTACHMENTS_STORE, { keyPath: 'id' })
                    store.createIndex('sessionId', 'sessionId', { unique: false })
                }
                // Create asyncQuestionDrafts store if not exists (v10). Checked
                // independently of draftAttachments: a v9 database from either
                // lineage lacks exactly one of the two stores.
                if (!db.objectStoreNames.contains(ASYNC_QUESTION_DRAFTS_STORE)) {
                    db.createObjectStore(ASYNC_QUESTION_DRAFTS_STORE)
                }
            }
        })
        dbPromise = opening
    }
    return dbPromise
}

// =============================================================================
// Draft Messages (message text and title for sessions)
// =============================================================================

/**
 * Save a draft message for a session.
 * @param {string} sessionId - The session ID
 * @param {Object} draft - The draft object { message?: string, title?: string, mediaIds?: string[] }
 * @returns {Promise<void>}
 */
export async function saveDraftMessage(sessionId, draft) {
    // Callers hand over store state (Vue reactive proxies), which structured clone rejects.
    const record = plainRecord(draft)
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MESSAGES_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_MESSAGES_STORE)
        const request = store.put(record, sessionId)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get a draft message for a session.
 * @param {string} sessionId - The session ID
 * @returns {Promise<Object|null>} The draft object or null if not found
 */
export async function getDraftMessage(sessionId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MESSAGES_STORE, 'readonly')
        const store = tx.objectStore(DRAFT_MESSAGES_STORE)
        const request = store.get(sessionId)
        request.onsuccess = () => resolve(request.result || null)
        request.onerror = () => reject(request.error)
    })
}

/**
 * Delete a draft message for a session.
 * @param {string} sessionId - The session ID
 * @returns {Promise<void>}
 */
export async function deleteDraftMessage(sessionId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MESSAGES_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_MESSAGES_STORE)
        const request = store.delete(sessionId)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get all draft messages (used at app startup to hydrate the store).
 * @returns {Promise<Object>} Object mapping sessionId to draft { message?, title?, mediaIds? }
 */
export async function getAllDraftMessages() {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MESSAGES_STORE, 'readonly')
        const store = tx.objectStore(DRAFT_MESSAGES_STORE)
        const drafts = {}

        const request = store.openCursor()
        request.onsuccess = (event) => {
            const cursor = event.target.result
            if (cursor) {
                drafts[cursor.key] = cursor.value
                cursor.continue()
            } else {
                resolve(drafts)
            }
        }
        request.onerror = () => reject(request.error)
    })
}

// =============================================================================
// Draft Sessions (session metadata before first message is sent)
// =============================================================================

/**
 * Save a draft session.
 * @param {string} sessionId - The session ID
 * @param {Object} data - The draft session data { projectId, title? }
 * @returns {Promise<void>}
 */
export async function saveDraftSession(sessionId, data) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_SESSIONS_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_SESSIONS_STORE)
        const request = store.put(data, sessionId)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get a draft session.
 * @param {string} sessionId - The session ID
 * @returns {Promise<Object|null>} The draft session data or null if not found
 */
export async function getDraftSession(sessionId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_SESSIONS_STORE, 'readonly')
        const store = tx.objectStore(DRAFT_SESSIONS_STORE)
        const request = store.get(sessionId)
        request.onsuccess = () => resolve(request.result || null)
        request.onerror = () => reject(request.error)
    })
}

/**
 * Delete a draft session.
 * @param {string} sessionId - The session ID
 * @returns {Promise<void>}
 */
export async function deleteDraftSession(sessionId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_SESSIONS_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_SESSIONS_STORE)
        const request = store.delete(sessionId)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get all draft sessions (used at app startup to hydrate the store).
 * @returns {Promise<Object>} Object mapping sessionId to { projectId }
 */
export async function getAllDraftSessions() {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_SESSIONS_STORE, 'readonly')
        const store = tx.objectStore(DRAFT_SESSIONS_STORE)
        const sessions = {}

        const request = store.openCursor()
        request.onsuccess = (event) => {
            const cursor = event.target.result
            if (cursor) {
                sessions[cursor.key] = cursor.value
                cursor.continue()
            } else {
                resolve(sessions)
            }
        }
        request.onerror = () => reject(request.error)
    })
}

// =============================================================================
// Draft Medias (file attachments for messages)
// =============================================================================

/**
 * @typedef {Object} DraftMedia
 * @property {string} id - UUID
 * @property {string} sessionId - Session this media belongs to
 * @property {string} name - Original filename
 * @property {'image' | 'txt' | 'pdf'} type - File type category
 * @property {string} mimeType - Original MIME type
 * @property {string} data - Encoded data (base64 for images/pdf, plain text for txt)
 * @property {number} createdAt - Timestamp
 */

/**
 * Save a draft media.
 * @param {DraftMedia} media - The media object to save
 * @returns {Promise<void>}
 */
export async function saveDraftMedia(media) {
    // IndexedDB cannot clone Vue reactive proxies from restored send snapshots.
    const record = plainRecord(media)
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MEDIAS_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_MEDIAS_STORE)
        const request = store.put(record)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get a draft media by ID.
 * @param {string} mediaId - The media ID
 * @returns {Promise<DraftMedia|null>} The media object or null if not found
 */
export async function getDraftMedia(mediaId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MEDIAS_STORE, 'readonly')
        const store = tx.objectStore(DRAFT_MEDIAS_STORE)
        const request = store.get(mediaId)
        request.onsuccess = () => resolve(request.result || null)
        request.onerror = () => reject(request.error)
    })
}

/**
 * Delete a draft media by ID.
 * @param {string} mediaId - The media ID
 * @returns {Promise<void>}
 */
export async function deleteDraftMedia(mediaId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MEDIAS_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_MEDIAS_STORE)
        const request = store.delete(mediaId)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get all draft medias for a session.
 * @param {string} sessionId - The session ID
 * @returns {Promise<DraftMedia[]>} Array of media objects
 */
export async function getDraftMediasBySession(sessionId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MEDIAS_STORE, 'readonly')
        const store = tx.objectStore(DRAFT_MEDIAS_STORE)
        const index = store.index('sessionId')
        const request = index.getAll(sessionId)
        request.onsuccess = () => resolve(request.result || [])
        request.onerror = () => reject(request.error)
    })
}

/**
 * Delete all draft medias for a session.
 * @param {string} sessionId - The session ID
 * @returns {Promise<void>}
 */
export async function deleteAllDraftMediasForSession(sessionId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MEDIAS_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_MEDIAS_STORE)
        const index = store.index('sessionId')

        // Use cursor to delete all matching entries
        const request = index.openCursor(sessionId)
        request.onsuccess = (event) => {
            const cursor = event.target.result
            if (cursor) {
                cursor.delete()
                cursor.continue()
            } else {
                resolve()
            }
        }
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get all draft medias (used at app startup to hydrate the store).
 * @returns {Promise<DraftMedia[]>} Array of all media objects
 */
export async function getAllDraftMedias() {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MEDIAS_STORE, 'readonly')
        const store = tx.objectStore(DRAFT_MEDIAS_STORE)
        const request = store.getAll()
        request.onsuccess = () => resolve(request.result || [])
        request.onerror = () => reject(request.error)
    })
}

// =============================================================================
// Draft Attachments (composer attachment records, spec 2026-10-03 §9.1)
// =============================================================================

/**
 * @typedef {Object} DraftAttachment
 * @property {string} id - Attachment UUID (the staging entry id)
 * @property {string} sessionId - Draft/session the attachment is shown in
 * @property {string} bucket - Staging bucket, set once and never re-derived
 * @property {number} position - Order in the composer
 * @property {string} name - Original filename
 * @property {number} size - Size in bytes
 * @property {string} mimeType - Browser MIME type ('' when unknown)
 * @property {string} kind - Display kind (image, PDF, text, video, audio, other)
 */

/**
 * Save (insert or replace) a draft attachment record.
 * @param {DraftAttachment} record
 * @returns {Promise<void>}
 */
export async function saveDraftAttachment(record) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_ATTACHMENTS_STORE, 'readwrite')
        const request = tx.objectStore(DRAFT_ATTACHMENTS_STORE).put(record)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Save (insert or replace) several draft attachment records in one readwrite
 * transaction (an ownership change is all-or-nothing). Resolves once the
 * transaction completes.
 * @param {DraftAttachment[]} records
 * @returns {Promise<void>}
 */
export async function saveDraftAttachments(records) {
    if (!records.length) return
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_ATTACHMENTS_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_ATTACHMENTS_STORE)
        for (const record of records) store.put(record)
        tx.oncomplete = () => resolve()
        tx.onerror = () => reject(tx.error)
        tx.onabort = () => reject(tx.error)
    })
}

/**
 * Delete every draft attachment record of one session (local forget of a
 * purged session, including records not hydrated yet).
 * @param {string} sessionId
 * @returns {Promise<void>}
 */
export async function deleteDraftAttachmentsBySession(sessionId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_ATTACHMENTS_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_ATTACHMENTS_STORE)
        const request = store.index('sessionId').getAllKeys(sessionId)
        request.onsuccess = () => {
            for (const id of request.result || []) store.delete(id)
        }
        request.onerror = () => reject(request.error)
        tx.oncomplete = () => resolve()
        tx.onerror = () => reject(tx.error)
        tx.onabort = () => reject(tx.error)
    })
}

/**
 * Get every draft attachment record (used at app startup to hydrate the store).
 * @returns {Promise<DraftAttachment[]>}
 */
export async function getAllDraftAttachments() {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_ATTACHMENTS_STORE, 'readonly')
        const request = tx.objectStore(DRAFT_ATTACHMENTS_STORE).getAll()
        request.onsuccess = () => resolve(request.result || [])
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get the draft attachment records of one session.
 * @param {string} sessionId
 * @returns {Promise<DraftAttachment[]>}
 */
export async function getDraftAttachmentsBySession(sessionId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_ATTACHMENTS_STORE, 'readonly')
        const request = tx.objectStore(DRAFT_ATTACHMENTS_STORE).index('sessionId').getAll(sessionId)
        request.onsuccess = () => resolve(request.result || [])
        request.onerror = () => reject(request.error)
    })
}

/**
 * Delete one draft attachment record.
 * @param {string} id
 * @returns {Promise<void>}
 */
export async function deleteDraftAttachment(id) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_ATTACHMENTS_STORE, 'readwrite')
        const request = tx.objectStore(DRAFT_ATTACHMENTS_STORE).delete(id)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

export { CODE_COMMENTS_STORE, INFLIGHT_SENDS_STORE, PENDING_REQUEST_DRAFTS_STORE }

/** Move a local session entry without a crash window between delete and save. */
export async function rekeyDraftSession(oldId, newId, record) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_SESSIONS_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_SESSIONS_STORE)
        store.put(record, newId)
        if (oldId !== newId) store.delete(oldId)
        tx.oncomplete = resolve
        tx.onerror = () => reject(tx.error)
        tx.onabort = () => reject(tx.error)
    })
}

// Async question writes use transaction completion as their durable boundary.
const plainRecord = record => JSON.parse(JSON.stringify(record))

function commitTransaction(db, stores, write) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction(stores, 'readwrite')
        tx.oncomplete = () => resolve()
        tx.onerror = () => reject(tx.error || new Error('Draft transaction failed'))
        tx.onabort = () => reject(tx.error || new Error('Draft transaction aborted'))
        try { write(tx) }
        catch (error) { tx.abort(); reject(error) }
    })
}

export async function saveAsyncQuestionDraft(sessionId, draft, openDb = getDb) {
    const record = plainRecord(draft)
    return commitTransaction(await openDb(), ASYNC_QUESTION_DRAFTS_STORE,
        tx => tx.objectStore(ASYNC_QUESTION_DRAFTS_STORE).put(record, sessionId))
}

export async function deleteAsyncQuestionDraft(sessionId, openDb = getDb) {
    return commitTransaction(await openDb(), ASYNC_QUESTION_DRAFTS_STORE,
        tx => tx.objectStore(ASYNC_QUESTION_DRAFTS_STORE).delete(sessionId))
}

/** Remove deleted-session question drafts and recovery snapshots in one commit. */
export async function deleteAsyncQuestionRecovery(sessionId, openDb = getDb) {
    return commitTransaction(await openDb(), [ASYNC_QUESTION_DRAFTS_STORE, INFLIGHT_SENDS_STORE], tx => {
        tx.objectStore(ASYNC_QUESTION_DRAFTS_STORE).delete(sessionId)
        const request = tx.objectStore(INFLIGHT_SENDS_STORE).openCursor()
        request.onsuccess = () => {
            const cursor = request.result
            if (!cursor) return
            if (cursor.value.sessionId === sessionId && (cursor.value.asyncQuestions || cursor.value.async_questions)) cursor.delete()
            cursor.continue()
        }
    })
}

export async function getAllAsyncQuestionDrafts(openDb = getDb) {
    const db = await openDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(ASYNC_QUESTION_DRAFTS_STORE, 'readonly')
        const drafts = {}
        const request = tx.objectStore(ASYNC_QUESTION_DRAFTS_STORE).openCursor()
        request.onsuccess = () => {
            const cursor = request.result
            if (cursor) { drafts[cursor.key] = cursor.value; cursor.continue() }
        }
        tx.oncomplete = () => resolve(drafts)
        tx.onerror = () => reject(tx.error)
        tx.onabort = () => reject(tx.error || new Error('Draft read aborted'))
    })
}

/** Commit recovered text and consumed choices together. A reload sees both or neither. */
export async function saveAsyncQuestionRecovery(sessionId, draft, questionDraft, openDb = getDb) {
    const messageRecord = plainRecord(draft), questionRecord = plainRecord(questionDraft)
    return commitTransaction(await openDb(), [DRAFT_MESSAGES_STORE, ASYNC_QUESTION_DRAFTS_STORE], tx => {
        tx.objectStore(DRAFT_MESSAGES_STORE).put(messageRecord, sessionId)
        tx.objectStore(ASYNC_QUESTION_DRAFTS_STORE).put(questionRecord, sessionId)
    })
}

/**
 * The stored form of an in-flight send snapshot, shared by every persistence
 * route (plain saves and the multi-store question-send commits).
 *
 * - `attachments` ({bucket, id, name, size, mimeType, kind}): composer sends,
 *   staged refs with metadata only (no bytes), kept whole (spec 2026-10-03 §9.5);
 * - `medias`: legacy sends (Retry of an older snapshot), capped at 8 MiB of
 *   encoded data, above which they are dropped (`mediasDropped: true`) and only
 *   the text can be restored after a reload;
 * - `images` / `documents` (processed payload copies) never bypass that cap.
 *
 * The record is a plain structured-clone-safe copy (the store hands out Vue
 * reactive proxies). `mediaCount` survives the drop: a message made only of
 * attachments has no text, and the count identifies it when the store matches
 * a rediscovered snapshot against the session's user_message lines.
 *
 * @param {Object} snapshot
 * @returns {Object}
 */
export function inflightSnapshotRecord(snapshot) {
    const record = plainRecord(snapshot)
    delete record.images
    delete record.documents
    if (Array.isArray(record.attachments)) {
        record.attachments = record.attachments.map(attachment => ({
            bucket: attachment.bucket,
            id: attachment.id,
            name: attachment.name,
            size: attachment.size,
            mimeType: attachment.mimeType,
            kind: attachment.kind,
        }))
    }
    const medias = record.medias || []
    // A re-saved snapshot whose medias were already dropped keeps its count.
    const mediaCount = medias.length || record.mediaCount || 0
    return medias.reduce((sum, media) => sum + (media.data?.length || 0), 0) > 8 * 1024 * 1024
        ? { ...record, medias: [], mediaCount: medias.length || mediaCount, mediasDropped: true }
        : { ...record, medias, mediaCount }
}

/** Persist the outgoing snapshot and consume its active draft in one commit. */
export async function stageAsyncQuestionSend(requestId, snapshot, nextDraft, nextQuestionDraft, openDb = getDb) {
    const record = inflightSnapshotRecord({ ...snapshot, status: 'staged' })
    const draft = plainRecord(nextDraft), questions = plainRecord(nextQuestionDraft)
    return commitTransaction(await openDb(), [DRAFT_MESSAGES_STORE, ASYNC_QUESTION_DRAFTS_STORE, INFLIGHT_SENDS_STORE], tx => {
        tx.objectStore(INFLIGHT_SENDS_STORE).put(record, requestId)
        if (snapshot.retryRequestId) tx.objectStore(INFLIGHT_SENDS_STORE).delete(snapshot.retryRequestId)
        tx.objectStore(DRAFT_MESSAGES_STORE).put(draft, snapshot.sessionId)
        tx.objectStore(ASYNC_QUESTION_DRAFTS_STORE).put(questions, snapshot.sessionId)
    })
}

/** Read-modify-write: an acknowledgement can delete the snapshot before this runs. */
export async function markAsyncQuestionSendDispatched(requestId, openDb = getDb) {
    return commitTransaction(await openDb(), INFLIGHT_SENDS_STORE, tx => {
        const store = tx.objectStore(INFLIGHT_SENDS_STORE)
        const request = store.get(requestId)
        request.onsuccess = () => {
            if (request.result?.status === 'staged' && !request.result.failed) store.put({ ...request.result, status: 'dispatched' }, requestId)
        }
    })
}

/** Restore before deleting the staged identity. Aborts leave the snapshot available. */
export async function restoreStagedAsyncQuestionSend(requestId, sessionId, draft, questionDraft, openDb = getDb) {
    const message = plainRecord(draft), questions = plainRecord(questionDraft)
    return commitTransaction(await openDb(), [DRAFT_MESSAGES_STORE, ASYNC_QUESTION_DRAFTS_STORE, INFLIGHT_SENDS_STORE], tx => {
        tx.objectStore(DRAFT_MESSAGES_STORE).put(message, sessionId)
        tx.objectStore(ASYNC_QUESTION_DRAFTS_STORE).put(questions, sessionId)
        tx.objectStore(INFLIGHT_SENDS_STORE).delete(requestId)
    })
}
