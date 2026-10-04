// frontend/src/utils/attachmentStrip.js
// Attachments of a user message (spec 2026-10-03 §9.5, §10.2): the total
// attachment count and the match key that identifies a send across the
// optimistic bubble, the in-flight snapshot and the stored user_message line;
// and the ordered history strip a user bubble renders from the
// `twicc_attachments` manifest extracted at ingestion (§10.1).
//
// Pure functions only: the components (`AttachmentStrip.vue` and the provider
// renderers) stay thin over them.

/**
 * Provide/inject key of the share viewer's explicit share mode. The standalone
 * share viewer provides `true`; everywhere else the injection defaults to
 * `false`. In share mode no file chip links to the owner's artifacts.
 */
export const ATTACHMENT_SHARE_MODE = 'twiccAttachmentShareMode'

// The bundled Claude CLI replaces, in place, an image it cannot process by a
// text block starting with this (same rule as the backend extraction).
const IMAGE_PLACEHOLDER_PREFIX = '[Image could not be processed:'

// Native media entry types across providers: Claude SDK content, Codex
// canonical `UserMessage` content, Codex `response_item` content.
const MEDIA_BLOCK_TYPES = new Set(['image', 'document', 'local_image', 'input_image'])
const TEXT_BLOCK_TYPES = new Set(['text', 'input_text'])

const KIND_ICONS = Object.freeze({
    image: 'file-image',
    PDF: 'file-pdf',
    text: 'file-lines',
    video: 'file-video',
    audio: 'file-audio',
    other: 'file',
})

/** The icon name of a display kind (manifest kinds). */
export function attachmentKindIcon(kind) {
    return KIND_ICONS[kind] || KIND_ICONS.other
}

/**
 * @typedef {Object} StripItem
 * @property {string} id - stable within one message
 * @property {string} name - the manifest name (inline: original name; file:
 *   final name on disk)
 * @property {string} kind - `image`, `PDF`, `text`, `video`, `audio`, `other`
 * @property {string|null} mode - `inline` or `file`; null for an optimistic or
 *   failed-send bubble (the server decides at send)
 * @property {string} [src] - image thumbnail URL (a native block's data URL, or
 *   a local `blob:` URL for an optimistic bubble)
 * @property {string} [artifactOwner] - file entries: the session whose
 *   artifacts folder holds the file (the message's session or a fork parent)
 * @property {string} [artifactName] - file entries: the name inside that
 *   session's `attachments/` folder
 * @property {boolean} canOpenArtifact - the chip links to the Artifacts tab
 */

/**
 * True when a content entry is a native media slot: an image or document
 * block, or the placeholder text block of an image the CLI could not process.
 */
function isMediaSlot(block) {
    if (!block || typeof block !== 'object') return false
    if (MEDIA_BLOCK_TYPES.has(block.type)) return true
    return TEXT_BLOCK_TYPES.has(block.type)
        && typeof block.text === 'string'
        && block.text.startsWith(IMAGE_PLACEHOLDER_PREFIX)
}

/**
 * The leading native media slots of a content array (TwiCC sends the native
 * blocks first, in manifest order), each with its index in the array.
 *
 * @param {Array|null} blocks
 * @returns {Array<{index: number, block: object}>}
 */
export function leadingMediaSlots(blocks) {
    const slots = []
    if (!Array.isArray(blocks)) return slots
    for (let index = 0; index < blocks.length; index++) {
        if (!isMediaSlot(blocks[index])) break
        slots.push({ index, block: blocks[index] })
    }
    return slots
}

/**
 * The browser-loadable source of a native image block, or null (documents,
 * placeholders, Codex `local_image` paths on the agent's machine).
 *
 * @param {object|null} block
 * @returns {string|null}
 */
export function nativeImageSrc(block) {
    if (!block || typeof block !== 'object') return null
    if (block.type === 'image' && block.source?.type === 'base64' && block.source.data) {
        return `data:${block.source.media_type || 'image/png'};base64,${block.source.data}`
    }
    if ((block.type === 'image' || block.type === 'input_image') && typeof block.image_url === 'string' && block.image_url) {
        return block.image_url
    }
    return null
}

function inlineEntryCount(entries) {
    return entries.filter(entry => entry?.mode === 'inline').length
}

function manifestEntries(metadata) {
    return Array.isArray(metadata?.entries) ? metadata.entries : null
}

/**
 * Build the ordered history strip of a message from its manifest (§10.2).
 *
 * Entries keep the manifest order. Inline entries take the native media
 * blocks in order (one per inline entry; documents and failed-image
 * placeholders take their own slot): an image gets its block as thumbnail,
 * any other inline entry is a chip. Hybrid inline entries are chips (the CLI
 * stores the `@`-mentioned files in separate records). File entries are chips
 * that open `attachments/<artifact_name>` in the owner's artifacts — never in
 * share mode.
 *
 * @param {{owner?: string, entries?: Array}|null} metadata - `twicc_attachments`
 * @param {Array<object>} nativeBlocks - the media slots, in content order
 * @param {{hybrid?: boolean, share?: boolean}} [options]
 * @returns {StripItem[]}
 */
export function buildAttachmentStrip(metadata, nativeBlocks, { hybrid = false, share = false } = {}) {
    const entries = manifestEntries(metadata)
    if (!entries) return []
    const owner = typeof metadata.owner === 'string' && metadata.owner ? metadata.owner : null
    const blocks = Array.isArray(nativeBlocks) ? nativeBlocks : []
    let inlineIndex = 0
    return entries.map((entry, index) => {
        const item = {
            id: `${index}:${entry?.n ?? index + 1}`,
            name: String(entry?.name ?? ''),
            kind: entry?.kind || 'other',
            mode: entry?.mode === 'file' ? 'file' : 'inline',
            canOpenArtifact: false,
        }
        if (item.mode === 'file') {
            const artifactName = typeof entry.artifact_name === 'string' && entry.artifact_name ? entry.artifact_name : null
            if (owner) item.artifactOwner = owner
            if (artifactName) item.artifactName = artifactName
            item.canOpenArtifact = !share && !!owner && !!artifactName
            return item
        }
        if (hybrid) return item
        const block = blocks[inlineIndex++]
        const src = item.kind === 'image' ? nativeImageSrc(block) : null
        if (src) item.src = src
        return item
    })
}

/**
 * The strip of an optimistic or failed-send bubble (§9.4): names and kinds in
 * send order, a thumbnail only from a local `blob:` URL (never from the
 * staging content endpoint, released at delivery), no artifact link.
 *
 * @param {Array<{id: string, name: string, kind: string, src?: string|null}>} attachmentItems
 * @returns {StripItem[]}
 */
export function optimisticAttachmentStrip(attachmentItems) {
    return (attachmentItems || []).map((attachment, index) => {
        const item = {
            id: String(attachment?.id ?? index),
            name: String(attachment?.name ?? ''),
            kind: attachment?.kind || 'other',
            mode: null,
            canOpenArtifact: false,
        }
        if (typeof attachment?.src === 'string' && attachment.src.startsWith('blob:')) item.src = attachment.src
        return item
    })
}

function blankTextIndices(blocks) {
    const indices = []
    ;(blocks || []).forEach((block, index) => {
        if (TEXT_BLOCK_TYPES.has(block?.type) && !(block.text || '').trim()) indices.push(index)
    })
    return indices
}

/**
 * What a user bubble renders for its attachments.
 *
 * - With `twicc_attachments`: the strip, and the indices of the content
 *   entries it replaces (the native media slots bound to inline entries, so no
 *   separate image group or document placeholder) plus blank text entries (an
 *   all-file message without text shows the strip alone).
 * - With optimistic/failed `attachmentItems`: their strip, blank text hidden.
 * - Otherwise `{strip: null, hiddenIndices: []}`: the current (legacy)
 *   rendering stays.
 *
 * @param {object|null} parsed - the parsed item
 * @param {Array<object>} blocks - its content entries (a hybrid string is
 *   passed as one text entry)
 * @param {{hybrid?: boolean, share?: boolean}} [options]
 * @returns {{strip: StripItem[]|null, hiddenIndices: number[]}}
 */
export function messageAttachmentLayout(parsed, blocks, { hybrid = false, share = false } = {}) {
    const entries = manifestEntries(parsed?.twicc_attachments)
    if (entries?.length) {
        const consumed = hybrid ? [] : leadingMediaSlots(blocks).slice(0, inlineEntryCount(entries))
        const strip = buildAttachmentStrip(parsed.twicc_attachments, consumed.map(slot => slot.block), { hybrid, share })
        const hidden = new Set([...consumed.map(slot => slot.index), ...blankTextIndices(blocks)])
        return { strip, hiddenIndices: [...hidden].sort((a, b) => a - b) }
    }
    if (Array.isArray(parsed?.attachmentItems) && parsed.attachmentItems.length) {
        return { strip: optimisticAttachmentStrip(parsed.attachmentItems), hiddenIndices: blankTextIndices(blocks) }
    }
    return { strip: null, hiddenIndices: [] }
}

/**
 * The display of a Claude mid-turn prompt recorded as a `queued_command`
 * attachment (`commandMode: 'prompt'`) whose manifest was extracted at
 * ingestion: its strip and its cleaned user text. Null for any other record.
 *
 * @param {object|null} parsed
 * @param {{share?: boolean}} [options]
 * @returns {{items: StripItem[], text: string}|null}
 */
export function queuedAttachmentDisplay(parsed, { share = false } = {}) {
    const attachment = parsed?.attachment
    if (attachment?.type !== 'queued_command' || attachment.commandMode !== 'prompt') return null
    const entries = manifestEntries(parsed.twicc_attachments)
    if (!entries?.length) return null
    const prompt = attachment.prompt
    if (typeof prompt === 'string') {
        return {
            items: buildAttachmentStrip(parsed.twicc_attachments, [], { hybrid: true, share }),
            text: prompt.trim(),
        }
    }
    if (!Array.isArray(prompt)) return null
    const consumed = leadingMediaSlots(prompt).slice(0, inlineEntryCount(entries))
    const consumedIndices = new Set(consumed.map(slot => slot.index))
    const text = prompt
        .filter((block, index) => !consumedIndices.has(index) && block?.type === 'text' && typeof block.text === 'string')
        .map(block => block.text)
        .join('\n')
        .trim()
    return {
        items: buildAttachmentStrip(parsed.twicc_attachments, consumed.map(slot => slot.block), { share }),
        text,
    }
}

/**
 * The `open-artifact` request of a strip item: its owner session and the
 * path relative to that session's artifacts dir. Null when the item cannot
 * open (inline entries, share mode, missing owner or name).
 *
 * @param {StripItem} item
 * @returns {{owner: string, relativePath: string}|null}
 */
export function stripItemArtifactRequest(item) {
    if (!item?.canOpenArtifact || !item.artifactOwner || !item.artifactName) return null
    return { owner: item.artifactOwner, relativePath: `attachments/${item.artifactName}` }
}

/**
 * Where a file chip opens, derived in the current instance (no stored path):
 * the current session's own artifacts dir (absolute path for the in-session
 * reveal), else the owner session's Artifacts tab (a fork parent, or a
 * session whose row is not loaded yet).
 *
 * @param {{owner: string, relativePath: string}} request
 * @param {{sessionId: string, artifactsDir: string|null}} current
 * @returns {{type: 'current', absolutePath: string, relativePath: string}|{type: 'session', sessionId: string, relativePath: string}}
 */
export function artifactNavigationTarget({ owner, relativePath }, { sessionId, artifactsDir }) {
    if (owner === sessionId && artifactsDir) {
        return { type: 'current', absolutePath: `${artifactsDir.replace(/\/+$/, '')}/${relativePath}`, relativePath }
    }
    return { type: 'session', sessionId: owner, relativePath }
}

/**
 * The parsed item to compute a match key from (§9.5). When the manifest binds
 * the leading media slots, the placeholder text blocks of failed images among
 * them are media, not user text: they are left out so the arriving line keeps
 * the optimistic bubble's key. Without metadata, or for a non-array content,
 * the item is returned unchanged (legacy semantics). Never mutates `parsed`.
 *
 * @param {object|null} parsed
 * @returns {object|null}
 */
export function matchableUserMessage(parsed) {
    const entries = manifestEntries(parsed?.twicc_attachments)
    const content = parsed?.message?.content
    if (!entries?.length || !Array.isArray(content)) return parsed
    const placeholders = new Set(
        leadingMediaSlots(content)
            .slice(0, inlineEntryCount(entries))
            .filter(slot => TEXT_BLOCK_TYPES.has(slot.block.type))
            .map(slot => slot.index),
    )
    if (!placeholders.size) return parsed
    return {
        ...parsed,
        message: { ...parsed.message, content: content.filter((_, index) => !placeholders.has(index)) },
    }
}

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
