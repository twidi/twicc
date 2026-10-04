// frontend/src/utils/fileUtils.js
// Legacy draft medias (`DraftMedia`, base64 in IndexedDB): SDK format
// conversion, send-time resize, and the normalized MediaItem shape shared by
// the media preview components. New composer attachments are staged uploads
// (utils/composerAttachments.js); these helpers serve legacy drafts, legacy
// failed-send snapshots, and the rendering of SDK blocks.

// =============================================================================
// Constants
// =============================================================================

/**
 * Long edge in pixels of the images stored in legacy draft medias (2576 px,
 * Claude Opus 4.7's native resolution). ``resizeMediasForSend`` may resize
 * them down at send time for the active (provider, model, num_images) combo.
 */
export const MAX_IMAGE_DIMENSION = 2576

/** File type categories */
export const FILE_TYPES = {
    IMAGE: 'image',
    PDF: 'pdf',
    TXT: 'txt'
}

/**
 * A legacy draft media, as stored in the IndexedDB `draftMedias` store.
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
