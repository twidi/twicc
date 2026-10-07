import { attachmentKindIcon } from './attachmentStrip.js'
import { formatAttachmentSize, getDisplayKind } from './composerAttachments.js'

const MARKDOWN_CONFIRM_BYTES = 64 * 1024
const ATTACHMENTS_CONFIRM_BYTES = 1024 * 1024

export function shouldConfirmPeerMarkdown(textBytes) {
    return Number.isFinite(textBytes) && textBytes >= MARKDOWN_CONFIRM_BYTES
}

export function peerAttachmentBytes(metadata) {
    if (!Array.isArray(metadata)) return 0
    return metadata.reduce((total, item) => {
        const bytes = item?.bytes
        return total + (Number.isFinite(bytes) && bytes >= 0 ? bytes : 0)
    }, 0)
}

export function shouldConfirmPeerAttachments(metadata) {
    return peerAttachmentBytes(metadata) >= ATTACHMENTS_CONFIRM_BYTES
}

export function formatPeerContentBytes(bytes) {
    const safeBytes = Number.isFinite(bytes) && bytes > 0 ? bytes : 0
    if (safeBytes >= 1024 * 1024) return `${(safeBytes / (1024 * 1024)).toFixed(1)} MiB`
    if (safeBytes >= 1024) return `${(safeBytes / 1024).toFixed(1)} KiB`
    return `${safeBytes} B`
}

/**
 * Delivery state of a chosen target: disabled until the content is ready, or
 * without a target (with `missingTargetError` once the content is ready).
 * Every attachment is accepted by every provider: the server decides how each
 * file is sent (spec 2026-10-03 §9.7).
 */
export function peerDeliveryTargetState(target, contentReady, missingTargetError = '') {
    if (!target) {
        return { disabled: true, error: contentReady ? missingTargetError : '' }
    }
    return { disabled: !contentReady, error: '' }
}

export function mergePeerAttachments(detail, attachments) {
    return {
        ...detail,
        payload: {
            ...(detail?.payload || {}),
            attachments: Array.isArray(attachments?.attachments) ? attachments.attachments : [],
        },
    }
}

function attachmentEntries(payload) {
    return Array.isArray(payload?.attachments) ? payload.attachments : []
}

function base64DecodedSize(data) {
    const padding = data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0
    return Math.max(0, Math.floor(data.length / 4) * 3 - padding)
}

/**
 * A `File` from one peer attachment entry `{name, media_type, data}`, for the
 * composer's attachment pipeline: its real name and media type. Null for an
 * entry without name or data.
 *
 * @param {{name: string, media_type: string, data: string}} entry
 * @returns {File|null}
 */
export function peerEntryToFile(entry) {
    if (typeof entry?.data !== 'string' || typeof entry?.name !== 'string' || !entry.name) return null
    const type = typeof entry.media_type === 'string' && entry.media_type ? entry.media_type : 'application/octet-stream'
    const bytes = Uint8Array.from(atob(entry.data), c => c.charCodeAt(0))
    return new File([bytes], entry.name, { type })
}

/**
 * One review-dialog preview row for `MediaThumbnailGroup`, like a ready composer
 * chip: an image thumbnail for an `image/*` entry, the kind icon for the others.
 *
 * @param {{name: string, media_type: string, data: string}} entry
 * @param {number} index - position of the entry in the message
 * @returns {object|null}
 */
export function peerEntryToMediaItem(entry, index) {
    if (typeof entry?.data !== 'string') return null
    const name = typeof entry.name === 'string' && entry.name ? entry.name : `attachment-${index + 1}`
    const mediaType = typeof entry.media_type === 'string' ? entry.media_type : ''
    const kind = getDisplayKind({ name, type: mediaType })
    const size = base64DecodedSize(entry.data)
    const image = kind === 'image' && mediaType.toLowerCase().startsWith('image/')
    return {
        id: `peer-attachment-${index}`,
        name,
        size,
        sizeLabel: formatAttachmentSize(size),
        kind,
        state: 'ready',
        progress: 100,
        retryable: false,
        statusText: '',
        icon: attachmentKindIcon(kind),
        type: image ? 'image' : kind === 'PDF' ? 'pdf' : 'other',
        src: image ? `data:${mediaType};base64,${entry.data}` : null,
        textUrl: null,
    }
}

export async function addPeerAttachmentsToDraft(payload, entryToFile, addAttachment) {
    for (const entry of attachmentEntries(payload)) {
        try {
            const file = entryToFile(entry)
            if (!file) throw new Error('Invalid Peer attachment')
            await addAttachment(file)
        } catch {
            return 'TwiCC could not add all attachments to the draft. '
                + 'The Peer message is still available for delivery to another session.'
        }
    }
    return ''
}

export function peerContentAllowsDelivery(detailReady, markdownState, attachmentsState) {
    return detailReady && markdownState === 'ready' && attachmentsState === 'ready'
}
