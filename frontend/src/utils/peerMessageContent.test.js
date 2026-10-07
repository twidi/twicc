import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
    formatPeerContentBytes,
    mergePeerAttachments,
    peerAttachmentBytes,
    peerEntryToFile,
    peerEntryToStripItem,
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

test('merges the attachment entries without mutating the lightweight detail', () => {
    const detail = { id: 1, payload: { text: 'message', attachments: [] } }
    const attachments = { attachments: [{ name: 'a.txt', media_type: 'text/plain', data: 'YQ==' }] }

    const merged = mergePeerAttachments(detail, attachments)

    assert.notStrictEqual(merged, detail)
    assert.notStrictEqual(merged.payload, detail.payload)
    assert.deepEqual(merged.payload, { text: 'message', attachments: attachments.attachments })
    assert.deepEqual(detail.payload, { text: 'message', attachments: [] })
    assert.deepEqual(mergePeerAttachments(detail, {}).payload.attachments, [])
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

test('peerEntryToFile keeps the real name and media type', async () => {
    const patch = peerEntryToFile({ name: 'fix.patch', media_type: 'text/x-diff', data: 'ZGlmZg==' })
    assert.ok(patch instanceof File)
    assert.equal(patch.name, 'fix.patch')
    assert.equal(patch.type, 'text/x-diff')
    assert.equal(await patch.text(), 'diff')
    const empty = peerEntryToFile({ name: 'empty.bin', media_type: '', data: '' })
    assert.equal(empty.size, 0)
    assert.equal(empty.type, 'application/octet-stream')
    assert.equal(peerEntryToFile({ name: 'x', media_type: 'text/plain' }), null)
    assert.equal(peerEntryToFile({ name: '', media_type: 'text/plain', data: 'YQ==' }), null)
})

test('the review strip shows a thumbnail for images and an unlinked kind tile for the others', async () => {
    const { stripItemArtifactRequest } = await import('./attachmentStrip.js')
    const image = peerEntryToStripItem({ name: 'shot.png', media_type: 'image/png', data: 'iVBORw==' }, 0)
    assert.deepEqual(image, {
        id: 'peer-attachment-0',
        name: 'shot.png',
        kind: 'image',
        mode: null,
        canOpenArtifact: false,
        src: 'data:image/png;base64,iVBORw==',
    })
    const pdf = peerEntryToStripItem({ name: 'spec.pdf', media_type: 'application/pdf', data: 'JVBERi0=' }, 1)
    assert.equal(pdf.kind, 'PDF')
    assert.equal('src' in pdf, false)
    const text = peerEntryToStripItem({ name: 'notes.txt', media_type: 'text/plain', data: 'aGk=' }, 2)
    assert.equal(text.kind, 'text')
    const video = peerEntryToStripItem({ name: 'clip.mp4', media_type: 'video/mp4', data: 'AAAA' }, 3)
    assert.equal(video.kind, 'video')
    assert.equal('src' in video, false)
    const svg = peerEntryToStripItem({ name: 'logo.svg', media_type: 'image/svg+xml', data: 'PHN2Zz4=' }, 4)
    assert.equal(svg.kind, 'text', 'SVG is text: no raster thumbnail')
    assert.equal('src' in svg, false)
    assert.equal(new Set([image, pdf, text, video, svg].map(item => item.id)).size, 5)
    for (const item of [image, pdf, text, video, svg]) {
        assert.equal(stripItemArtifactRequest(item), null, 'a peer file is never a link')
    }
    assert.equal(peerEntryToStripItem({ name: 'x' }, 5), null)
})

test('a peer entry without name gets the default attachment name of its position', () => {
    assert.equal(peerEntryToStripItem({ media_type: 'image/png', data: 'iVBORw==' }, 0).name, 'attachment-1.png')
    assert.equal(peerEntryToStripItem({ name: '', media_type: 'application/pdf', data: 'JVBERi0=' }, 2).name, 'attachment-3.pdf')
    assert.equal(peerEntryToStripItem({ data: 'AAAA' }, 3).name, 'attachment-4.bin')
})

test('wiring: the review dialog renders the attachments through the shared strip', () => {
    const dialog = readFileSync(new URL('../components/peer/PeerMessageReviewDialog.vue', import.meta.url), 'utf8')
    assert.match(dialog, /<AttachmentStrip/)
    assert.match(dialog, /peerEntryToStripItem/)
    assert.doesNotMatch(dialog, /MediaThumbnailGroup/)
})

test('adds the entries to the draft in order and reports a failure instead of hiding it', async () => {
    const { addPeerAttachmentsToDraft } = await import('./peerMessageContent.js')
    const payload = { attachments: [{ name: 'one.txt' }, { name: 'two.png' }, { name: 'three.mp4' }] }
    const attempted = []

    const error = await addPeerAttachmentsToDraft(
        payload,
        entry => ({ name: entry.name }),
        async file => {
            attempted.push(file.name)
            if (file.name === 'two.png') throw new Error('IndexedDB failed')
        },
    )

    assert.deepEqual(attempted, ['one.txt', 'two.png'])
    assert.equal(
        error,
        'TwiCC could not add all attachments to the draft. The Peer message is still available for delivery to another session.',
    )
    const added = []
    assert.equal(await addPeerAttachmentsToDraft(payload, entry => ({ name: entry.name }), async f => { added.push(f.name) }), '')
    assert.deepEqual(added, ['one.txt', 'two.png', 'three.mp4'])
    assert.equal(await addPeerAttachmentsToDraft({ attachments: [{ name: 'x' }] }, () => null, async () => {}),
        'TwiCC could not add all attachments to the draft. The Peer message is still available for delivery to another session.')
})
