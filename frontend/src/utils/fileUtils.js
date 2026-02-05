// frontend/src/utils/fileUtils.js
// File validation, base64 encoding, and type detection for document uploads

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

/** All supported MIME types for attachments */
export const SUPPORTED_MIME_TYPES = [
    ...SUPPORTED_IMAGE_TYPES,
    ...SUPPORTED_DOCUMENT_TYPES,
    ...SUPPORTED_TEXT_TYPES
]

/** Maximum file size in bytes (5 MB - Claude API limit) */
export const MAX_FILE_SIZE = 5 * 1024 * 1024

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

/**
 * Check if a MIME type is supported for upload.
 * @param {string} mimeType - The MIME type to check
 * @returns {boolean} True if the MIME type is supported
 */
export function isSupportedMimeType(mimeType) {
    return SUPPORTED_MIME_TYPES.includes(mimeType)
}

// =============================================================================
// Validation
// =============================================================================

/**
 * Validate a file for upload.
 * Checks MIME type and file size.
 * @param {File} file - The file to validate
 * @returns {{ valid: boolean, error?: string }} Validation result
 */
export function validateFile(file) {
    // Check MIME type
    if (!isSupportedMimeType(file.type)) {
        const extension = file.name.split('.').pop()?.toLowerCase() || 'unknown'
        return {
            valid: false,
            error: `Unsupported file type: .${extension} (${file.type || 'unknown type'})`
        }
    }

    // Check file size
    if (file.size > MAX_FILE_SIZE) {
        const sizeMB = (file.size / 1024 / 1024).toFixed(1)
        return {
            valid: false,
            error: `File too large: ${sizeMB} MB (max 5 MB)`
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
// Processing
// =============================================================================

/**
 * Process a file and return a DraftMedia object ready for storage.
 * - Images and PDFs are encoded to base64
 * - Text files are read as plain text
 *
 * @param {File} file - The file to process
 * @param {string} sessionId - The session this media belongs to
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
export async function processFile(file, sessionId) {
    // Validate first
    const validation = validateFile(file)
    if (!validation.valid) {
        throw new Error(validation.error)
    }

    const type = getFileType(file.type)
    let data

    if (type === FILE_TYPES.TXT) {
        // Text files: store as plain text (will be sent as type: 'text')
        data = await fileToText(file)
    } else {
        // Images and PDFs: store as base64
        data = await fileToBase64(file)
    }

    return {
        id: crypto.randomUUID(),
        sessionId,
        name: file.name || `file.${type}`,
        type,
        mimeType: file.type,
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
