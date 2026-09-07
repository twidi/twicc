// frontend/src/utils/draftStorage.js
// IndexedDB wrapper for draft messages, draft sessions, and draft medias persistence

const DB_NAME = 'twicc'
const DB_VERSION = 8
const DRAFT_MESSAGES_STORE = 'draftMessages'
const DRAFT_SESSIONS_STORE = 'draftSessions'
const DRAFT_MEDIAS_STORE = 'draftMedias'
const CODE_COMMENTS_STORE = 'codeComments'
const INFLIGHT_SENDS_STORE = 'inflightSends'
const PENDING_REQUEST_DRAFTS_STORE = 'pendingRequestDrafts'

let dbPromise = null

/**
 * Opens/initializes the IndexedDB database (lazy singleton).
 * @returns {Promise<IDBDatabase>}
 */
export function getDb() {
    if (!dbPromise) {
        dbPromise = new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION)

            request.onerror = () => reject(request.error)
            request.onsuccess = () => resolve(request.result)

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
            }
        })
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
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MESSAGES_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_MESSAGES_STORE)
        const request = store.put(draft, sessionId)
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
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_MEDIAS_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_MEDIAS_STORE)
        const request = store.put(media)
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
