import { getDb } from './draftStorage.js'

const STORE = 'ephemeralControls'

export async function saveEphemeralControl(id, value) {
    const db = await getDb()
    const plain = JSON.parse(JSON.stringify(value))
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite')
        tx.objectStore(STORE).put(plain, id)
        tx.oncomplete = resolve
        tx.onerror = () => reject(tx.error)
        tx.onabort = () => reject(tx.error)
    })
}

export async function deleteEphemeralControl(id) {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite')
        tx.objectStore(STORE).delete(id)
        tx.oncomplete = resolve
        tx.onerror = () => reject(tx.error)
        tx.onabort = () => reject(tx.error)
    })
}

export async function loadEphemeralControls() {
    const db = await getDb()
    return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, 'readonly')
        const request = tx.objectStore(STORE).openCursor()
        const entries = {}
        request.onsuccess = () => {
            const cursor = request.result
            if (cursor) { entries[cursor.key] = cursor.value; cursor.continue() }
        }
        tx.oncomplete = () => resolve(entries)
        tx.onerror = () => reject(tx.error)
    })
}
