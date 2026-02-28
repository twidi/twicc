// frontend/src/utils/crypto.js
// Cryptographic utilities with fallbacks for non-secure contexts (HTTP)

/**
 * Generate a UUID v4.
 *
 * Uses `crypto.randomUUID()` when available (secure contexts: HTTPS or localhost).
 * Falls back to `crypto.getRandomValues()` which works in all contexts including
 * plain HTTP, so the app remains functional when accessed over the network.
 */
export function generateUUID() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID()
    }
    // Fallback for non-secure contexts (e.g. http://192.168.x.x)
    return '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, c =>
        (+c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> +c / 4).toString(16)
    )
}
