// frontend/src/utils/draftStorage.js
// IndexedDB wrapper for draft messages persistence

const DB_NAME = 'twicc'
const DB_VERSION = 1
const STORE_NAME = 'draftMessages'

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
                if (!db.objectStoreNames.contains(STORE_NAME)) {
                    db.createObjectStore(STORE_NAME)
                }
            }
        })
    }
    return dbPromise
}

/**
 * Save a draft for a session.
 * @param {string} sessionId - The session ID
 * @param {Object} draft - The draft object { message?: string, title?: string }
 * @returns {Promise<void>}
 */
export async function saveDraft(sessionId, draft) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readwrite')
        const store = tx.objectStore(STORE_NAME)
        const request = store.put(draft, sessionId)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get a draft for a session.
 * @param {string} sessionId - The session ID
 * @returns {Promise<Object|null>} The draft object or null if not found
 */
export async function getDraft(sessionId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readonly')
        const store = tx.objectStore(STORE_NAME)
        const request = store.get(sessionId)
        request.onsuccess = () => resolve(request.result || null)
        request.onerror = () => reject(request.error)
    })
}

/**
 * Delete a draft for a session.
 * @param {string} sessionId - The session ID
 * @returns {Promise<void>}
 */
export async function deleteDraft(sessionId) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readwrite')
        const store = tx.objectStore(STORE_NAME)
        const request = store.delete(sessionId)
        request.onsuccess = () => resolve()
        request.onerror = () => reject(request.error)
    })
}

/**
 * Get all drafts (used at app startup to hydrate the store).
 * @returns {Promise<Object>} Object mapping sessionId to draft { message?, title? }
 */
export async function getAllDrafts() {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readonly')
        const store = tx.objectStore(STORE_NAME)
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
