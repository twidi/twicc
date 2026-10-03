// frontend/src/utils/fileUtils.js
// File validation, base64 encoding, and type detection for document uploads

import { generateUUID } from './crypto.js'

// =============================================================================
// Constants
// =============================================================================

/** Supported image MIME types */
export const SUPPORTED_IMAGE_TYPES = [
    'image/png',
    'image/jpeg',
    'image/gif',
    'image/webp'
]

/** Supported document MIME types (PDF) */
export const SUPPORTED_DOCUMENT_TYPES = ['application/pdf']

/** Supported text MIME types */
export const SUPPORTED_TEXT_TYPES = ['text/plain']

/**
 * Maximum image dimension in pixels (long edge) used as the storage
 * target across all providers. 2576px matches Claude Opus 4.7's native
 * resolution — the most generous supported by any model we target —
 * so an image resized at upload time is ready to be sent to any
 * provider without re-encoding loss when the active model can exploit
 * the full resolution.
 *
 * At send time, ``MessageInput.handleSend`` may re-resize down to the
 * effective target of the active (provider, model, num_images) combo
 * (e.g. 1568 for Sonnet/Haiku, 2000 when >20 images, no resize for
 * Codex / Opus 4.7+ which accept the stored size as-is). Two-pass
 * resize is accepted in exchange for a single canonical IndexedDB
 * representation that survives provider/model switches.
 *
 * Providers opt in to the storage-time resize via
 * ``getAttachmentSupport().resizeImages``.
 */
export const MAX_IMAGE_DIMENSION = 2576

/**
 * Hard cap on the number of attachments allowed per draft. Matches the
 * lowest documented Anthropic per-request limit (100 images, on 200k-
 * context models) so a draft built up to this ceiling will always pass
 * the API check on either provider.
 */
export const MAX_FILES_PER_DRAFT = 100

/**
 * Hard cap on the cumulative size of all attachments in a draft, in
 * bytes. Matches Anthropic's 32 MB per-request payload ceiling for
 * standard endpoints — once stored attachments approach this size, no
 * further uploads are accepted. The check uses each new file's raw
 * source size (pre-resize) summed with the already-stored post-resize
 * total: conservative, but avoids running the resize pipeline for an
 * upload that would push the draft over the limit anyway.
 */
export const MAX_TOTAL_BYTES_PER_DRAFT = 32 * 1024 * 1024

/** File type categories */
export const FILE_TYPES = {
    IMAGE: 'image',
    PDF: 'pdf',
    TXT: 'txt'
}

// =============================================================================
// Type Detection
// =============================================================================

/**
 * Determine file type category from MIME type.
 * @param {string} mimeType - The MIME type of the file
 * @returns {'image' | 'pdf' | 'txt'} The file type category
 */
export function getFileType(mimeType) {
    if (mimeType.startsWith('image/')) return FILE_TYPES.IMAGE
    if (mimeType === 'application/pdf') return FILE_TYPES.PDF
    return FILE_TYPES.TXT
}

// =============================================================================
// Validation
// =============================================================================

/**
 * Validate a file against a provider's attachment capabilities.
 *
 * ``capabilities`` is the object returned by the active provider's
 * ``getAttachmentSupport()`` helper:
 *   { images, documents, maxBytes, acceptedMimeTypes, resizeImages }
 *
 * Only ``acceptedMimeTypes`` and ``maxBytes`` are consulted here — the
 * ``images`` / ``documents`` booleans are an artifact of the helper API
 * for the UI shell; whitelist enforcement happens through the explicit
 * MIME list (which is the union of the supported types this provider
 * actually accepts).
 *
 * @param {File} file - The file to validate
 * @param {Object} capabilities - Provider attachment capabilities
 * @returns {{ valid: boolean, error?: string }} Validation result
 */
export function validateFile(file, capabilities) {
    const accepted = capabilities?.acceptedMimeTypes ?? []
    if (!accepted.includes(file.type)) {
        const extension = file.name.split('.').pop()?.toLowerCase() || 'unknown'
        return {
            valid: false,
            error: `Unsupported file type: .${extension} (${file.type || 'unknown type'})`
        }
    }

    const maxBytes = capabilities?.maxBytes ?? 0
    if (maxBytes > 0 && file.size > maxBytes) {
        const sizeMB = (file.size / 1024 / 1024).toFixed(1)
        const maxMB = (maxBytes / 1024 / 1024).toFixed(0)
        return {
            valid: false,
            error: `File too large: ${sizeMB} MB (max ${maxMB} MB)`
        }
    }

    return { valid: true }
}

// =============================================================================
// File Reading
// =============================================================================

/**
 * Convert a File to base64 encoded string (without data URL prefix).
 * @param {File} file - The file to encode
 * @returns {Promise<string>} The base64 encoded string
 */
export function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => {
            const result = reader.result
            if (typeof result === 'string') {
                // Remove data URL prefix (e.g., "data:image/png;base64,")
                const base64 = result.split(',')[1]
                resolve(base64 || '')
            } else {
                reject(new Error('Failed to read file as base64'))
            }
        }
        reader.onerror = () => reject(new Error('Failed to read file'))
        reader.readAsDataURL(file)
    })
}

/**
 * Convert a File to plain text.
 * @param {File} file - The file to read
 * @returns {Promise<string>} The file content as text
 */
export function fileToText(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => {
            const result = reader.result
            if (typeof result === 'string') {
                resolve(result)
            } else {
                reject(new Error('Failed to read file as text'))
            }
        }
        reader.onerror = () => reject(new Error('Failed to read file'))
        reader.readAsText(file)
    })
}

// =============================================================================
// Image Format Detection
// =============================================================================

/**
 * Detect actual image format from base64-encoded data using magic bytes.
 * Falls back to the provided mimeType if detection fails.
 * @param {string} base64Data - Base64 encoded image data (without data URL prefix)
 * @param {string} fallbackMimeType - MIME type to use if detection fails
 * @returns {string} Detected MIME type
 */
export function detectImageMimeType(base64Data, fallbackMimeType) {
    if (base64Data.startsWith('iVBOR')) return 'image/png'   // \x89PNG\r
    if (base64Data.startsWith('/9j/'))  return 'image/jpeg'  // \xFF\xD8\xFF
    if (base64Data.startsWith('UklGR')) return 'image/webp'  // RIFF.
    if (base64Data.startsWith('R0lGO')) return 'image/gif'   // GIF8.
    return fallbackMimeType
}

// =============================================================================
// Image Resizing
// =============================================================================

/**
 * Resize an image if either dimension exceeds ``maxDim``. Preserves
 * aspect ratio. Output format: JPEG stays JPEG, everything else becomes
 * PNG (lossless — ideal for screenshots with text/UI).
 *
 * @param {string} base64Data - The base64 encoded image data (no data URL prefix)
 * @param {string} mimeType - The original MIME type (e.g., 'image/png')
 * @param {number} [maxDim=MAX_IMAGE_DIMENSION] - Long-edge cap in pixels.
 *   Defaults to the storage ceiling; the send-time pipeline passes a
 *   smaller value (e.g. 1568, 2000) when the active (provider, model,
 *   num_images) combo requires a tighter cap than the stored size.
 * @returns {Promise<{ data: string, mimeType: string }>} Resized base64 data and output MIME type
 */
export function resizeImageIfNeeded(base64Data, mimeType, maxDim = MAX_IMAGE_DIMENSION) {
    return new Promise((resolve, reject) => {
        const img = new Image()
        img.onload = () => {
            const { width, height } = img

            // No resize needed — return original data unchanged
            if (width <= maxDim && height <= maxDim) {
                resolve({ data: base64Data, mimeType })
                return
            }

            // Compute new dimensions preserving aspect ratio
            const scale = Math.min(maxDim / width, maxDim / height)
            const newWidth = Math.round(width * scale)
            const newHeight = Math.round(height * scale)

            // Draw on canvas at new size
            const canvas = document.createElement('canvas')
            canvas.width = newWidth
            canvas.height = newHeight
            const ctx = canvas.getContext('2d')
            ctx.drawImage(img, 0, 0, newWidth, newHeight)

            // JPEG in → JPEG out ; everything else → PNG (lossless)
            const outputMimeType = mimeType === 'image/jpeg' ? 'image/jpeg' : 'image/png'
            const quality = outputMimeType === 'image/jpeg' ? 0.92 : undefined
            const dataUrl = canvas.toDataURL(outputMimeType, quality)
            const resizedBase64 = dataUrl.split(',')[1]

            resolve({ data: resizedBase64, mimeType: outputMimeType })
        }
        img.onerror = () => reject(new Error('Failed to load image for resizing'))
        img.src = `data:${mimeType};base64,${base64Data}`
    })
}

/**
 * Re-resize the images of a draft for the model that will receive them.
 *
 * Stored images are at ``MAX_IMAGE_DIMENSION`` (Opus 4.7's native size). The
 * provider helper decides the send-time cap from (model, image count):
 * Sonnet/Haiku want 1568 px, Anthropic caps requests with >20 images at
 * 2000 px, Codex re-resizes server-side (``null`` = ship the stored blob).
 * Shared by every path that builds an SDK payload from stored medias (send,
 * failed-send retry) so they cannot drift apart.
 *
 * @param {DraftMedia[]} medias - Stored medias
 * @param {{ getEffectiveImageDimension?: Function }|null} helpers - Provider helpers
 * @param {string|null|undefined} model - Model that will receive the medias
 * @param {Function} [resize=resizeImageIfNeeded] - Resize implementation (injectable for tests)
 * @returns {Promise<DraftMedia[]>} Medias, images replaced by their resized copy when needed
 */
export async function resizeMediasForSend(medias, helpers, model, resize = resizeImageIfNeeded) {
    const imageCount = medias.filter(m => m.type === FILE_TYPES.IMAGE).length
    const targetDim = helpers?.getEffectiveImageDimension?.({ model, numImages: imageCount }) ?? null
    if (targetDim === null) return medias
    return Promise.all(medias.map(async media => {
        if (media.type !== FILE_TYPES.IMAGE) return media
        const { data, mimeType } = await resize(media.data, media.mimeType, targetDim)
        if (data === media.data && mimeType === media.mimeType) return media
        return { ...media, data, mimeType }
    }))
}

// =============================================================================
// Processing
// =============================================================================

/**
 * Process a file and return a DraftMedia object ready for storage.
 * - Images and PDFs are encoded to base64
 * - Text files are read as plain text
 *
 * Validation, max-size, and resize behaviour are all driven by the
 * provider's ``capabilities`` bundle (see ``validateFile`` above).
 *
 * @param {File} file - The file to process
 * @param {string} sessionId - The session this media belongs to
 * @param {Object} capabilities - Provider attachment capabilities
 * @returns {Promise<DraftMedia>} The processed media object
 * @throws {Error} If validation fails or file cannot be read
 *
 * @typedef {Object} DraftMedia
 * @property {string} id - UUID
 * @property {string} sessionId - Session this media belongs to
 * @property {string} name - Original filename
 * @property {'image' | 'txt' | 'pdf'} type - File type category
 * @property {string} mimeType - Original MIME type
 * @property {string} data - Encoded data (base64 for images/pdf, plain text for txt)
 * @property {number} createdAt - Timestamp
 */
export async function processFile(file, sessionId, capabilities) {
    // Validate first
    const validation = validateFile(file, capabilities)
    if (!validation.valid) {
        throw new Error(validation.error)
    }

    const type = getFileType(file.type)
    let data
    let mimeType = file.type

    if (type === FILE_TYPES.TXT) {
        // Text files: store as plain text (will be sent as type: 'text')
        data = await fileToText(file)
    } else {
        // Images and PDFs: store as base64
        data = await fileToBase64(file)

        if (type === FILE_TYPES.IMAGE) {
            // Detect actual format from magic bytes — file.type can be wrong
            // (e.g., WebP file saved with .png extension)
            mimeType = detectImageMimeType(data, mimeType)
            if (capabilities?.resizeImages) {
                const resized = await resizeImageIfNeeded(data, mimeType)
                data = resized.data
                // Re-detect after resize — canvas.toDataURL() may not honor
                // the requested output format in all browsers
                mimeType = detectImageMimeType(resized.data, resized.mimeType)
            }
        }
    }

    return {
        id: generateUUID(),
        sessionId,
        name: file.name || `file.${type}`,
        type,
        mimeType,
        data,
        createdAt: Date.now()
    }
}

// =============================================================================
// SDK Format Conversion
// =============================================================================

/**
 * Convert a DraftMedia object to Claude SDK image block format.
 * @param {DraftMedia} media - The media object (must be type 'image')
 * @returns {Object} SDK ImageBlockParam
 */
export function mediaToImageBlock(media) {
    if (media.type !== FILE_TYPES.IMAGE) {
        throw new Error(`Cannot convert ${media.type} to image block`)
    }
    return {
        type: 'image',
        source: {
            type: 'base64',
            media_type: media.mimeType,
            data: media.data
        }
    }
}

/**
 * Convert a DraftMedia object to Claude SDK document block format.
 * @param {DraftMedia} media - The media object (must be type 'pdf' or 'txt')
 * @returns {Object} SDK DocumentBlockParam
 */
export function mediaToDocumentBlock(media) {
    if (media.type === FILE_TYPES.IMAGE) {
        throw new Error('Cannot convert image to document block')
    }

    if (media.type === FILE_TYPES.TXT) {
        // Text files use type: 'text' with raw content
        return {
            type: 'document',
            source: {
                type: 'text',
                media_type: 'text/plain',
                data: media.data
            }
        }
    }

    // PDF files use type: 'base64'
    return {
        type: 'document',
        source: {
            type: 'base64',
            media_type: 'application/pdf',
            data: media.data
        }
    }
}

/**
 * Convert an array of DraftMedia objects to SDK format.
 * Separates images and documents as required by the SDK.
 * @param {DraftMedia[]} medias - Array of media objects
 * @returns {{ images: Object[], documents: Object[] }} SDK-formatted blocks
 */
export function mediasToSdkFormat(medias) {
    const images = []
    const documents = []

    for (const media of medias) {
        if (media.type === FILE_TYPES.IMAGE) {
            images.push(mediaToImageBlock(media))
        } else {
            documents.push(mediaToDocumentBlock(media))
        }
    }

    return { images, documents }
}

// =============================================================================
// Data URL Helpers
// =============================================================================

/**
 * Create a data URL from a DraftMedia object for display purposes.
 * @param {DraftMedia} media - The media object
 * @returns {string} Data URL for use in img src, etc.
 */
export function mediaToDataUrl(media) {
    if (media.type === FILE_TYPES.TXT) {
        // Text files: encode as base64 for data URL
        const base64 = btoa(unescape(encodeURIComponent(media.data)))
        return `data:text/plain;base64,${base64}`
    }
    // Images and PDFs: already base64
    return `data:${media.mimeType};base64,${media.data}`
}

// =============================================================================
// Size estimation
// =============================================================================

/**
 * Approximate the decoded size of a DraftMedia object in bytes.
 *
 * Used by the per-draft 32 MB ceiling check at upload time. The cost of
 * a single TextEncoder pass on a 5 MB text file is negligible so we do
 * the exact computation for ``txt``; for ``image`` / ``pdf`` we
 * approximate from the base64 string length (``length * 3 / 4`` minus
 * the 0-2 padding chars), since the encoded blob can be many MB and we
 * want a cheap lookup.
 *
 * @param {DraftMedia} media - The media object
 * @returns {number} Approximate decoded size in bytes
 */
export function getDraftMediaBytes(media) {
    if (!media) return 0
    if (media.type === FILE_TYPES.TXT) {
        return new TextEncoder().encode(media.data || '').byteLength
    }
    const data = media.data || ''
    if (!data) return 0
    // base64-decoded byte count: every 4 chars encode 3 bytes, minus
    // padding (each '=' is one byte less). Tolerates strings without
    // a trailing padding (uncommon, but cheap to guard).
    const padding = (data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0)
    return Math.max(0, Math.floor(data.length * 3 / 4) - padding)
}

// =============================================================================
// Normalized MediaItem conversion
// =============================================================================

/**
 * @typedef {Object} MediaItem
 * @property {'image' | 'pdf' | 'txt'} type - Media type category
 * @property {string} src - Full data URL for display (used as img src for images)
 * @property {string} [name] - Optional filename
 * @property {string} [textContent] - Raw text content (only for type 'txt', used for preview)
 */

/**
 * Convert a DraftMedia object to a normalized MediaItem for shared preview components.
 * @param {DraftMedia} media - The draft media object
 * @returns {MediaItem} Normalized media item
 */
export function draftMediaToMediaItem(media) {
    const item = {
        type: media.type,
        src: mediaToDataUrl(media),
        name: media.name
    }
    if (media.type === FILE_TYPES.TXT) {
        item.textContent = media.data
    }
    return item
}

/**
 * Convert a Claude SDK content block to a normalized MediaItem.
 * Handles both 'image' blocks and 'document' blocks (text, PDF).
 * @param {Object} block - SDK content block (type: 'image' or 'document')
 * @returns {MediaItem|null} Normalized media item, or null if not convertible
 */
export function sdkBlockToMediaItem(block) {
    if (block.type === 'image') {
        const mediaType = block.source?.media_type || 'image/png'
        if (!block.source?.data) return null
        return {
            type: FILE_TYPES.IMAGE,
            src: `data:${mediaType};base64,${block.source.data}`,
            name: undefined
        }
    }
    if (block.type === 'document') {
        const source = block.source
        if (source?.type === 'text') {
            const base64 = btoa(unescape(encodeURIComponent(source.data)))
            return {
                type: FILE_TYPES.TXT,
                src: `data:text/plain;base64,${base64}`,
                name: block.title,
                textContent: source.data
            }
        }
        if (source?.type === 'base64' && source?.data) {
            return {
                type: FILE_TYPES.PDF,
                src: `data:${source.media_type};base64,${source.data}`,
                name: block.title
            }
        }
    }
    return null
}
