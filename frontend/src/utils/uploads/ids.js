// frontend/src/utils/uploads/ids.js
// `clientId` and file fingerprint helpers (spec §6.2, §6.8).

import { generateUUID } from '../crypto.js'
import { hashBytes } from '../hash.js'

/** Number of bytes read from the start of a file for its fingerprint. */
export const FINGERPRINT_BYTES = 1024 * 1024

/** Number of random hex characters after the tab id in a `clientId`. */
export const CLIENT_ID_RANDOM_LENGTH = 16

/**
 * `n` random hex characters, from `generateUUID()` with the dashes removed
 * (it works in a non-secure context too).
 *
 * @param {number} n - at most 32
 * @returns {string}
 */
export function randomHexFromUUID(n) {
    let hex = ''
    while (hex.length < n) hex += generateUUID().replace(/-/g, '')
    return hex.slice(0, n)
}

/**
 * A new `clientId`: the tab id, `:`, and 16 random hex characters. Unique per
 * upload; 53 characters with a UUID tab id (the server accepts 64).
 *
 * @param {string} tabId
 * @param {(n: number) => string} randomHex
 * @returns {string}
 */
export function makeClientId(tabId, randomHex) {
    return `${tabId}:${randomHex(CLIENT_ID_RANDOM_LENGTH)}`
}

/**
 * True when `clientId` was made by the tab `tabId`.
 *
 * @param {string|null|undefined} clientId
 * @param {string} tabId
 * @returns {boolean}
 */
export function isClientIdOfTab(clientId, tabId) {
    return typeof clientId === 'string' && !!tabId && clientId.startsWith(tabId + ':')
}

/**
 * Fingerprint of a file: `hashBytes` of its first `min(size, 1 MiB)` bytes,
 * then `:` and the size. Guards against a wrong pick, not an attacker.
 *
 * @param {Blob} file
 * @returns {Promise<string>} rejects when the file cannot be read
 */
export async function fingerprintFile(file) {
    const head = file.slice(0, Math.min(file.size, FINGERPRINT_BYTES))
    const bytes = new Uint8Array(await head.arrayBuffer())
    return `${hashBytes(bytes)}:${file.size}`
}
