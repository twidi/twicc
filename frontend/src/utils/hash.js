// frontend/src/utils/hash.js
// Small non-cryptographic string hash, shared by every caller that needs a
// compact stable key for a piece of text.
//
// Deliberately not `crypto.subtle.digest`: that one is asynchronous AND
// secure-context only (`undefined` over plain http from a LAN IP), so it can't
// back a synchronous key computation in a self-hosted app served over http.

/**
 * FNV-1a hash of a string, base36-encoded.
 *
 * Non-cryptographic: use it for cache/render keys and change detection, never
 * for anything security-bearing. Callers must tolerate a collision (e.g. keep
 * the raw source as the real cache key).
 *
 * @param {string} str
 * @returns {string} base36 hash
 */
export function hashString(str) {
    let h = 0x811c9dc5
    for (let i = 0; i < str.length; i++) {
        h ^= str.charCodeAt(i)
        h = Math.imul(h, 0x01000193)
    }
    return (h >>> 0).toString(36)
}

/**
 * FNV-1a hash of a byte array, base36-encoded.
 *
 * Same algorithm and output format as `hashString`, over bytes instead of
 * UTF-16 code units (for an ASCII string, both give the same value). Same
 * caveat: non-cryptographic.
 *
 * @param {Uint8Array} bytes
 * @returns {string} base36 hash
 */
export function hashBytes(bytes) {
    let h = 0x811c9dc5
    for (let i = 0; i < bytes.length; i++) {
        h ^= bytes[i]
        h = Math.imul(h, 0x01000193)
    }
    return (h >>> 0).toString(36)
}
