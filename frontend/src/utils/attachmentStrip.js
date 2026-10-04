// frontend/src/utils/attachmentStrip.js
// Attachments of a user message (spec 2026-10-03 §9.5, §10.2): the total
// attachment count and the match key that identifies a send across the
// optimistic bubble, the in-flight snapshot and the stored user_message line.
// The ordered history strip itself is built here too (later).

/**
 * Total attachment count of a parsed user message.
 *
 * - `twicc_attachments` (extracted at ingestion): every entry, inline or file;
 * - `attachmentCount` (optimistic and failed-send bubbles): the total the
 *   composer sent;
 * - otherwise the provider's own count (`extractUserMessageAttachmentCount`),
 *   for legacy sends and CLI / MCP / peer sends.
 *
 * @param {object|null} parsed
 * @param {number} [legacyCount] - the provider's native block count
 * @returns {number}
 */
export function attachmentCountForMessage(parsed, legacyCount = 0) {
    const entries = parsed?.twicc_attachments?.entries
    if (Array.isArray(entries)) return entries.length
    const count = parsed?.attachmentCount
    if (Number.isInteger(count) && count >= 0) return count
    return legacyCount || 0
}

/**
 * Attachment count of an in-flight send snapshot: its staged refs for a new
 * send, else its legacy medias (`mediaCount` survives dropped medias).
 *
 * @param {object|null} entry
 * @returns {number}
 */
export function inflightAttachmentCount(entry) {
    if (Array.isArray(entry?.attachments) && entry.attachments.length) return entry.attachments.length
    return entry?.medias?.length || entry?.mediaCount || 0
}

/**
 * Identity of a user message for "is this the same send?" comparisons. Text
 * is the discriminant when there is any; a message made only of attachments
 * has none, so its total attachment count stands in (two attachment-only
 * sends of the same count are then indistinguishable, like two identical
 * texts).
 *
 * @param {string|null|undefined} text
 * @param {number} count
 * @returns {string|null} null when the message carries neither
 */
export function attachmentMatchKey(text, count) {
    const trimmed = (text || '').trim()
    if (trimmed) return `t:${trimmed}`
    return count > 0 ? `a:${count}` : null
}
