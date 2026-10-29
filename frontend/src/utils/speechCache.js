// In-memory cache of generated speech audio, capped by total size and by age.

/**
 * Least-recently-used cache of audio blobs. The `Map` keeps the recency order:
 * the first entry is the least recently used. An entry expires `ttlMs` after it
 * was stored; reading it refreshes its recency, not its lifetime.
 */
export class SpeechCache {
    constructor({ maxBytes = 50 * 1024 * 1024, ttlMs = 10 * 60 * 1000, now = () => Date.now() } = {}) {
        this._maxBytes = maxBytes
        this._ttlMs = ttlMs
        this._now = now
        this._entries = new Map()
        this._totalBytes = 0
    }

    get totalBytes() {
        return this._totalBytes
    }

    /** The cached blob, or null when absent or expired. */
    get(key) {
        const entry = this._entries.get(key)
        if (!entry) return null
        this._entries.delete(key)
        if (this._now() - entry.storedAt >= this._ttlMs) {
            this._totalBytes -= entry.blob.size
            return null
        }
        this._entries.set(key, entry)
        return entry.blob
    }

    /** Store a blob. Returns false, storing nothing, when it alone exceeds the cap. */
    set(key, blob) {
        if (blob.size > this._maxBytes) return false
        this._remove(key)
        this._dropExpired()
        while (this._totalBytes + blob.size > this._maxBytes) {
            this._remove(this._entries.keys().next().value)
        }
        this._entries.set(key, { blob, storedAt: this._now() })
        this._totalBytes += blob.size
        return true
    }

    clear() {
        this._entries.clear()
        this._totalBytes = 0
    }

    _remove(key) {
        const entry = this._entries.get(key)
        if (!entry) return
        this._entries.delete(key)
        this._totalBytes -= entry.blob.size
    }

    _dropExpired() {
        const now = this._now()
        for (const [key, entry] of this._entries) {
            if (now - entry.storedAt >= this._ttlMs) this._remove(key)
        }
    }
}
