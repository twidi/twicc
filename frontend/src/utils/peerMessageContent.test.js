import test from 'node:test'
import assert from 'node:assert/strict'

import {
    formatPeerContentBytes,
    mergePeerAttachments,
    peerAttachmentBytes,
    peerBlockToFile,
    peerContentAllowsDelivery,
    peerDeliveryTargetState,
    shouldConfirmPeerAttachments,
    shouldConfirmPeerMarkdown,
} from './peerMessageContent.js'

test('requires confirmation at the 64 KiB markdown boundary', () => {
    assert.equal(shouldConfirmPeerMarkdown(64 * 1024 - 1), false)
    assert.equal(shouldConfirmPeerMarkdown(64 * 1024), true)
})

test('requires confirmation at the 1 MiB total attachment boundary', () => {
    const below = [{ bytes: 512 * 1024 }, { bytes: 512 * 1024 - 1 }]
    const boundary = [...below, { bytes: 1 }]

    assert.equal(peerAttachmentBytes(below), 1024 * 1024 - 1)
    assert.equal(shouldConfirmPeerAttachments(below), false)
    assert.equal(peerAttachmentBytes(boundary), 1024 * 1024)
    assert.equal(shouldConfirmPeerAttachments(boundary), true)
})

test('ignores malformed attachment sizes', () => {
    const metadata = [{ bytes: -1 }, { bytes: '12' }, {}, null, { bytes: 7 }]

    assert.equal(peerAttachmentBytes(metadata), 7)
})

test('formats content sizes with binary units', () => {
    assert.equal(formatPeerContentBytes(141_846), '138.5 KiB')
    assert.equal(formatPeerContentBytes(5 * 1024 * 1024), '5.0 MiB')
})

test('merges attachment blocks without mutating the lightweight detail', () => {
    const detail = {
        id: 1,
        payload: { text: 'message', images: [], documents: [] },
    }
    const attachments = {
        images: [{ type: 'image' }],
        documents: [{ type: 'document' }],
    }

    const merged = mergePeerAttachments(detail, attachments)

    assert.notStrictEqual(merged, detail)
    assert.notStrictEqual(merged.payload, detail.payload)
    assert.deepEqual(merged.payload, {
        text: 'message',
        images: attachments.images,
        documents: attachments.documents,
    })
    assert.deepEqual(detail.payload, { text: 'message', images: [], documents: [] })
})

test('allows delivery only after detail, markdown, and attachments are ready', () => {
    assert.equal(peerContentAllowsDelivery(true, 'ready', 'ready'), true)
    for (const [detailReady, markdownState, attachmentsState] of [
        [false, 'ready', 'ready'],
        [true, 'loading', 'ready'],
        [true, 'confirm', 'ready'],
        [true, 'declined', 'ready'],
        [true, 'error', 'ready'],
        [true, 'ready', 'loading'],
        [true, 'ready', 'confirm'],
        [true, 'ready', 'declined'],
        [true, 'ready', 'error'],
    ]) {
        assert.equal(
            peerContentAllowsDelivery(detailReady, markdownState, attachmentsState),
            false,
        )
    }
})

test('every peer attachment is accepted: no provider is rejected for its attachment types', async () => {
    const content = await import('./peerMessageContent.js')
    for (const name of ['peerAttachmentCompatibilityError', 'firstCompatiblePeerProvider', 'firstCompatiblePeerProviderForMetadata']) {
        assert.equal(Object.hasOwn(content, name), false, name)
    }
})

test('derives the delivery target state from the target and the content readiness only', () => {
    assert.deepEqual(peerDeliveryTargetState(null, true), { disabled: true, error: '' })
    assert.deepEqual(
        peerDeliveryTargetState(null, true, 'No active provider is available.'),
        { disabled: true, error: 'No active provider is available.' },
    )
    assert.deepEqual(
        peerDeliveryTargetState(null, false, 'No active provider is available.'),
        { disabled: true, error: '' },
    )
    assert.deepEqual(peerDeliveryTargetState({ provider: 'codex' }, false), { disabled: true, error: '' })
    // A PDF, a text and a video go to Codex as well: the server decides at send.
    assert.deepEqual(peerDeliveryTargetState({ provider: 'codex' }, true), { disabled: false, error: '' })
})

test('peerBlockToFile converts every block kind to a File, keeping its name and type', async () => {
    const text = peerBlockToFile({ type: 'document', title: 'notes.md', source: { type: 'text', media_type: 'text/plain', data: 'hé' } }, 0)
    assert.ok(text instanceof File)
    assert.equal(text.name, 'notes.md')
    assert.equal(text.type, 'text/plain')
    assert.equal(await text.text(), 'hé')
    const untitled = peerBlockToFile({ type: 'document', source: { type: 'text', data: 'x' } }, 2)
    assert.equal(untitled.name, 'peer-attachment-3.txt')

    const pdf = peerBlockToFile({ type: 'document', source: { type: 'base64', media_type: 'application/pdf', data: 'JVBERi0=' } }, 0)
    assert.equal(pdf.name, 'peer-attachment-1.pdf')
    assert.equal(pdf.type, 'application/pdf')
    assert.equal(await pdf.text(), '%PDF-')
    const video = peerBlockToFile({ type: 'document', title: 'clip.mp4', source: { type: 'base64', media_type: 'video/mp4', data: 'AAAA' } }, 1)
    assert.equal(video.name, 'clip.mp4')
    assert.equal(video.type, 'video/mp4')
    const image = peerBlockToFile({ type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'iVBORw==' } }, 4)
    assert.equal(image.name, 'peer-attachment-5.png')
    assert.equal(peerBlockToFile({ type: 'image', source: { type: 'url', url: 'https://x' } }, 0), null)
})

test('reports a draft attachment failure instead of hiding it', async () => {
    const { addPeerAttachmentsToDraft } = await import('./peerMessageContent.js')
    assert.equal(typeof addPeerAttachmentsToDraft, 'function')
    const payload = {
        images: [{ id: 'image' }],
        documents: [{ id: 'document' }],
    }
    const attempted = []

    const error = await addPeerAttachmentsToDraft(
        payload,
        block => ({ name: block.id }),
        async file => {
            attempted.push(file.name)
            if (file.name === 'document') throw new Error('IndexedDB failed')
        },
    )

    assert.deepEqual(attempted, ['image', 'document'])
    assert.equal(
        error,
        'TwiCC could not add all attachments to the draft. The Peer message is still available for delivery to another session.',
    )

    const success = await addPeerAttachmentsToDraft(
        payload,
        block => ({ name: block.id }),
        async () => {},
    )
    assert.equal(success, '')
})
