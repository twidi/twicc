// frontend/src/utils/draftStorage.js
// IndexedDB wrapper for draft messages and draft sessions persistence

const DB_NAME = 'twicc'
const DB_VERSION = 2
const DRAFT_MESSAGES_STORE = 'draftMessages'
const DRAFT_SESSIONS_STORE = 'draftSessions'

let dbPromise = null

/**
 * Opens/initializes the IndexedDB database (lazy singleton).
 * @returns {Promise<IDBDatabase>}
 */
function getDb() {
    if (!dbPromise) {
        dbPromise = new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION)

            request.onerror = () => reject(request.error)
            request.onsuccess = () => resolve(request.result)

            request.onupgradeneeded = (event) => {
                const db = event.target.result
                // Create draftMessages store if not exists (v1)
                if (!db.objectStoreNames.contains(DRAFT_MESSAGES_STORE)) {
                    db.createObjectStore(DRAFT_MESSAGES_STORE)
                }
                // Create draftSessions store if not exists (v2)
                if (!db.objectStoreNames.contains(DRAFT_SESSIONS_STORE)) {
                    db.createObjectStore(DRAFT_SESSIONS_STORE)
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
 * @param {Object} draft - The draft object { message?: string, title?: string }
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
 * @returns {Promise<Object>} Object mapping sessionId to draft { message?, title? }
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
 * @param {string} projectId - The project ID this session belongs to
 * @returns {Promise<void>}
 */
export async function saveDraftSession(sessionId, projectId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(DRAFT_SESSIONS_STORE, 'readwrite')
        const store = tx.objectStore(DRAFT_SESSIONS_STORE)
        const request = store.put({ projectId }, sessionId)
        request.onsuccess = () => resolve()
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
