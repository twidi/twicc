// frontend/src/utils/uploads/display.js
// Display helpers of the in-tab indicator (spec §6.6, §6.7). Pure: the
// components only call them.

import { bytesDone, isStalled } from './rules.js'

/**
 * State text and buttons of one strip line (§6.7).
 *
 * Buttons:
 * - `retry`: a local `paused` entry → `controller.retry(key)`;
 * - `retryFinalization`: a non-local `active` entry whose transfer is
 *   complete → `controller.retryFinalization(key)` (no file pick);
 * - `resume`: a stalled entry → the strip's single-file picker, then
 *   `controller.resume(key, file)`;
 * - `cancel`: every entry except `finalizing` and `cancelling`.
 *
 * @param {object} entry
 * @param {{tabId: string, now: number}} ctx
 * @returns {{text: string|null, error: string|null, buttons: string[]}}
 */
export function entryActions(entry, ctx) {
    const record = entry.server
    if (entry.localState === 'cancelling') {
        return { text: 'Cancelling', error: null, buttons: [] }
    }
    if (record?.state === 'finalizing') {
        return { text: 'Finalizing', error: null, buttons: [] }
    }
    if (entry.localState === 'queued') {
        return { text: 'Queued', error: null, buttons: ['cancel'] }
    }
    if (entry.localState === 'paused') {
        return { text: 'Paused', error: null, buttons: ['retry', 'cancel'] }
    }
    if (!entry.local && record?.state === 'active' && record.offset === record.size) {
        return { text: null, error: record.error || null, buttons: ['retryFinalization', 'cancel'] }
    }
    if (isStalled(entry, ctx)) {
        return { text: 'Interrupted', error: null, buttons: ['resume', 'cancel'] }
    }
    return { text: null, error: null, buttons: ['cancel'] }
}

/**
 * Copy the files of a file input, then reset it (so the same file can be
 * picked again). The copy comes first: resetting the input empties its live
 * `FileList`.
 *
 * @param {{files: FileList|File[]|null, value: string}} input
 * @returns {File[]}
 */
export function takePickedFiles(input) {
    const files = Array.from(input.files || [])
    input.value = ''
    return files
}

/**
 * Progress of one strip line, in whole percent: same bytes-done rule as the
 * tab label aggregate (§6.11); 100 for a zero-byte file.
 *
 * @param {object} entry
 * @returns {number} 0 to 100
 */
export function entryPercent(entry) {
    if (!entry.size) return 100
    return Math.min(100, Math.floor((100 * bytesDone(entry)) / entry.size))
}
