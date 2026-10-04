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

export function mergePeerAttachments(detail, attachments) {
    return {
        ...detail,
        payload: {
            ...(detail?.payload || {}),
            images: Array.isArray(attachments?.images) ? attachments.images : [],
            documents: Array.isArray(attachments?.documents) ? attachments.documents : [],
        },
    }
}

function attachmentBlocks(payload) {
    return [
        ...(Array.isArray(payload?.images) ? payload.images : []),
        ...(Array.isArray(payload?.documents) ? payload.documents : []),
    ]
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

function extensionForMediaType(mediaType) {
    if (mediaType === 'application/pdf') return 'pdf'
    if (mediaType === 'text/plain') return 'txt'
    const subtype = (mediaType.split('/')[1] || '').split(/[;+]/)[0]
    return /^[a-z0-9.-]+$/i.test(subtype) ? subtype : 'bin'
}

/**
 * A `File` from one peer attachment block (image or document), for the
 * composer's attachment pipeline: its title (or a numbered default name) and
 * its media type. Null for a block with no inline content.
 *
 * @param {object} block - SDK `image` / `document` block
 * @param {number} index - position of the block in the message
 * @returns {File|null}
 */
export function peerBlockToFile(block, index) {
    const source = block?.source || {}
    const title = typeof block?.title === 'string' && block.title.trim() ? block.title.trim() : ''
    if (source.type === 'text' && typeof source.data === 'string') {
        return new File([source.data], title || `peer-attachment-${index + 1}.txt`, { type: 'text/plain' })
    }
    if (source.type === 'base64' && typeof source.data === 'string') {
        const mime = source.media_type || 'application/octet-stream'
        const bytes = Uint8Array.from(atob(source.data), c => c.charCodeAt(0))
        return new File([bytes], title || `peer-attachment-${index + 1}.${extensionForMediaType(mime)}`, { type: mime })
    }
    return null
}

export async function addPeerAttachmentsToDraft(payload, blockToFile, addAttachment) {
    for (const [index, block] of attachmentBlocks(payload).entries()) {
        try {
            const file = blockToFile(block, index)
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
