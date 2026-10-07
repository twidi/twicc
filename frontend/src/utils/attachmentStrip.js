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

// The basename TwiCC gives the copy it writes for the hybrid CLI (same shape as the backend).
const HYBRID_REFERENCE = /^att_[0-9a-f]{12}(?:\.[A-Za-z0-9]+)?$/

/**
 * Endpoint serving the image the CLI recorded for a hybrid inline entry (its own
 * `attachment` record after the user line, never loaded by the client).
 *
 * @param {{projectId: string, sessionId: string, lineNum: number, reference: string}} target
 * @returns {string|null} null for malformed input
 */
export function hybridAttachmentImageUrl({ projectId, sessionId, lineNum, reference } = {}) {
    if (!projectId || !sessionId || !Number.isInteger(lineNum) || lineNum < 1) return null
    if (typeof reference !== 'string' || !HYBRID_REFERENCE.test(reference)) return null
    return `/api/projects/${encodeURIComponent(projectId)}/sessions/${encodeURIComponent(sessionId)}/items/${lineNum}/attachments/${reference}`
}

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
 * any other inline entry is a chip. A hybrid inline image gets its thumbnail
 * from `imageUrl(reference)` (the CLI stores the `@`-mentioned files in separate
 * records the client does not load); without a resolver, or a `reference`
 * (messages ingested before it existed), it stays a chip. File entries are chips
 * that open `attachments/<artifact_name>` in the owner's artifacts — never in
 * share mode.
 *
 * @param {{owner?: string, entries?: Array}|null} metadata - `twicc_attachments`
 * @param {Array<object>} nativeBlocks - the media slots, in content order
 * @param {{hybrid?: boolean, share?: boolean, imageUrl?: ((reference: string) => string|null)|null}} [options]
 * @returns {StripItem[]}
 */
export function buildAttachmentStrip(metadata, nativeBlocks, { hybrid = false, share = false, imageUrl = null } = {}) {
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
        if (hybrid) {
            if (item.kind === 'image' && typeof imageUrl === 'function' && typeof entry?.reference === 'string') {
                const src = imageUrl(entry.reference)
                if (src) item.src = src
            }
            return item
        }
        const block = blocks[inlineIndex++]
        const src = item.kind === 'image' ? nativeImageSrc(block) : null
        if (src) item.src = src
        return item
    })
}

// Extensions of the default attachment name, by media type (the subset of
// Python's `mimetypes.guess_extension` the backend default name gets for the
// media types a message can carry).
const MEDIA_TYPE_EXTENSIONS = Object.freeze({
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/bmp': '.bmp',
    'image/svg+xml': '.svg',
    'application/pdf': '.pdf',
    'text/plain': '.txt',
    'text/markdown': '.md',
    'text/csv': '.csv',
    'text/html': '.html',
    'application/json': '.json',
})

/**
 * The name of an attachment that has none: `attachment-<n>` plus the
 * extension of its media type (`.bin` when unknown). Same rule as the
 * backend `default_name` (core/services/attachments/inline.py).
 *
 * @param {string|null|undefined} mediaType
 * @param {number} n - 1-based position of the attachment
 * @returns {string}
 */
export function defaultAttachmentName(mediaType, n) {
    const base = typeof mediaType === 'string' ? mediaType.split(';')[0].trim().toLowerCase() : ''
    return `attachment-${n}${MEDIA_TYPE_EXTENSIONS[base] || '.bin'}`
}

/** The media type of a `data:` URL, or null for any other source. */
function dataUrlMediaType(src) {
    if (typeof src !== 'string') return null
    const match = /^data:([^;,]+)[;,]/i.exec(src)
    return match ? match[1].toLowerCase() : null
}

// The native content entries a message without manifest shows in its strip:
// Claude `image` / `document` blocks, Codex canonical `image` / `local_image`
// entries.
const NATIVE_STRIP_BLOCK_TYPES = new Set(['image', 'document', 'local_image'])

/** True when a native content entry is an attachment of the strip of a message without manifest. */
export function isNativeStripMedia(block) {
    return !!block && typeof block === 'object' && NATIVE_STRIP_BLOCK_TYPES.has(block.type)
}

/**
 * The kind and media type of a Claude `document` block, from its source: a
 * `text` (or `content`) source is plain text, a `url` source is a PDF (the
 * only document type the API fetches by URL), a `base64` source has its own
 * media type.
 */
function nativeDocumentMedia(source) {
    const sourceType = source?.type
    if (sourceType === 'text' || sourceType === 'content') {
        const mediaType = typeof source.media_type === 'string' && source.media_type.toLowerCase().startsWith('text/')
            ? source.media_type
            : 'text/plain'
        return { kind: 'text', mediaType }
    }
    if (sourceType === 'url') return { kind: 'PDF', mediaType: 'application/pdf' }
    if (sourceType === 'base64' && typeof source.media_type === 'string') {
        const mediaType = source.media_type.split(';')[0].trim().toLowerCase()
        if (mediaType === 'application/pdf') return { kind: 'PDF', mediaType }
        if (mediaType.startsWith('text/')) return { kind: 'text', mediaType }
        return { kind: 'other', mediaType }
    }
    return { kind: 'other', mediaType: null }
}

/** The last segment of a local path (either separator), or null. */
function pathBasename(path) {
    if (typeof path !== 'string') return null
    const name = path.split(/[\\/]/).pop().trim()
    return name || null
}

/**
 * The strip of the native media of a message without attachment manifest
 * (older messages, CLI / MCP sends): one item per `isNativeStripMedia` entry
 * of `blocks`, in content order; any other entry is skipped.
 *
 * - An image gets its browser-loadable source as thumbnail (`nativeImageSrc`);
 *   without one (a Codex `local_image` path on the agent's machine, a URL
 *   source) it is an icon tile.
 * - A document is a PDF / text / other icon tile, never a thumbnail.
 * - Name: the document `title`, the `local_image` file name, else the default
 *   name of its position among the attachments (`attachment-<n>.<ext>`).
 * - No item links to the Artifacts tab: these are not artifacts.
 *
 * @param {Array<object>|null} blocks - the message content entries
 * @returns {StripItem[]}
 */
export function nativeMediaStripItems(blocks) {
    if (!Array.isArray(blocks)) return []
    return blocks.filter(isNativeStripMedia).map((block, index) => {
        let kind = 'image'
        let mediaType = null
        let src = null
        let ownName = null
        if (block.type === 'document') {
            ({ kind, mediaType } = nativeDocumentMedia(block.source))
            ownName = typeof block.title === 'string' && block.title.trim() ? block.title : null
        } else if (block.type === 'local_image') {
            ownName = pathBasename(block.path)
        } else {
            src = nativeImageSrc(block)
            mediaType = block.source?.type === 'base64' ? block.source.media_type || 'image/png' : dataUrlMediaType(src)
        }
        const item = {
            id: `native-${index}`,
            name: ownName || defaultAttachmentName(mediaType, index + 1),
            kind,
            mode: null,
            canOpenArtifact: false,
        }
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
 *   second image strip or document placeholder) plus blank text entries (an
 *   all-file message without text shows the strip alone).
 * - With optimistic/failed `attachmentItems`: their strip, blank text hidden.
 * - Otherwise `{strip: null, hiddenIndices: []}`: the provider renders the
 *   native media itself (a strip from `nativeMediaStripItems`).
 *
 * @param {object|null} parsed - the parsed item
 * @param {Array<object>} blocks - its content entries (a hybrid string is
 *   passed as one text entry)
 * @param {{hybrid?: boolean, share?: boolean, imageUrl?: ((reference: string) => string|null)|null}} [options]
 * @returns {{strip: StripItem[]|null, hiddenIndices: number[]}}
 */
export function messageAttachmentLayout(parsed, blocks, { hybrid = false, share = false, imageUrl = null } = {}) {
    const entries = manifestEntries(parsed?.twicc_attachments)
    if (entries?.length) {
        const consumed = hybrid ? [] : leadingMediaSlots(blocks).slice(0, inlineEntryCount(entries))
        const strip = buildAttachmentStrip(parsed.twicc_attachments, consumed.map(slot => slot.block), { hybrid, share, imageUrl })
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
 * How a tile of an editable strip (the composer: `AttachmentStrip` with
 * `editable`) shows its upload state. The item is a composer item
 * (`attachmentChipItem` / `legacyMediaStripItem` in utils/composerAttachments.js):
 * `state` is `uploading`, `ready`, `failed` or `missing` (empty for a legacy
 * media not converted yet), `progress` a percentage, `statusText` the state
 * message, `sizeLabel` the formatted size.
 *
 * - `uploading`: the progress bar on the tile, with its percentage (0 to 100).
 * - `error`: a failed or missing upload (danger border and badge).
 * - `retryable`: the tile shows Retry.
 * - `details`: the tooltip line under the name (size, status, percentage).
 *
 * @param {object} item
 * @returns {{uploading: boolean, error: boolean, progress: number|null, retryable: boolean, statusText: string, details: string}}
 */
export function editableTileState(item) {
    const state = item?.state || ''
    const uploading = state === 'uploading'
    const error = state === 'failed' || state === 'missing'
    const rawProgress = Number(item?.progress)
    const progress = uploading
        ? Math.round(Math.min(100, Math.max(0, Number.isFinite(rawProgress) ? rawProgress : 0)))
        : null
    const statusText = typeof item?.statusText === 'string' ? item.statusText : ''
    const status = uploading && statusText ? `${statusText} (${progress}%)` : statusText
    return {
        uploading,
        error,
        progress,
        retryable: !!item?.retryable,
        statusText,
        details: [item?.sizeLabel, status].filter(Boolean).join(' · '),
    }
}

/**
 * The id of the tile that keeps the focus after a Remove: the next tile, else
 * the previous one, else none (the strip is empty).
 *
 * @param {Array<{id: string}>} items
 * @param {string} removedId
 * @returns {string[]} the candidates, in order of preference
 */
export function focusCandidatesAfterRemove(items, removedId) {
    const list = items || []
    const index = list.findIndex(item => item?.id === removedId)
    if (index < 0) return []
    return [list[index + 1]?.id, list[index - 1]?.id].filter(id => typeof id === 'string')
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
 * The text an API-error Resend sends again: the user's own text of the
 * message that drove the failed turn, trimmed. The placeholder blocks of
 * failed images the manifest binds are media, not user text
 * (`matchableUserMessage`), so they are never resent as typed text.
 *
 * @param {((parsed: object) => string|null)|null} extractText - the
 *   provider's `extractUserMessageText`
 * @param {object|null} parsed
 * @returns {string} empty when the message has no user text
 */
export function userMessageResendText(extractText, parsed) {
    if (typeof extractText !== 'function') return ''
    return (extractText(matchableUserMessage(parsed)) || '').trim()
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
