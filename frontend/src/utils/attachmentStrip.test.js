// Tests for the ordered history strip and the attachment count matching
// (spec 2026-10-03 §9.4, §9.5, §10.2). Run with `node --test`.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
    ATTACHMENT_SHARE_MODE,
    artifactNavigationTarget,
    attachmentCountForMessage,
    attachmentKindIcon,
    attachmentMatchKey,
    buildAttachmentStrip,
    inflightAttachmentCount,
    leadingMediaSlots,
    matchableUserMessage,
    messageAttachmentLayout,
    nativeImageSrc,
    optimisticAttachmentStrip,
    queuedAttachmentDisplay,
    stripItemArtifactRequest,
    userMessageResendText,
} from './attachmentStrip.js'
import {
    userMessageAttachmentCount as codexUserMessageAttachmentCount,
    userMessageContent as codexUserMessageContent,
    userMessageText as codexUserMessageText,
} from '../providers/codex/canonical.js'

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

const firstNativeImage = 'data:image/png;base64,QUFB'
const secondNativeImage = 'data:image/png;base64,QkJC'

function claudeImage(data) {
    return { type: 'image', source: { type: 'base64', media_type: 'image/png', data } }
}
const pdfBlock = { type: 'document', title: 'spec.pdf', source: { type: 'base64', media_type: 'application/pdf', data: 'UERG' } }
const textDocBlock = { type: 'document', title: 'notes.txt', source: { type: 'text', media_type: 'text/plain', data: 'hello' } }
const placeholderBlock = { type: 'text', text: '[Image could not be processed: invalid image]' }

function entry(n, name, kind, mode, artifactName = null) {
    return { n, name, kind, rank: 1, of: 1, mode, artifact_name: mode === 'file' ? artifactName ?? name : null }
}

const mixedMetadata = {
    owner: 'session-1',
    entries: [
        entry(1, 'one.png', 'image', 'inline'),
        entry(2, 'movie.mp4', 'video', 'file'),
        entry(3, 'two.png', 'image', 'inline'),
    ],
}

// ---------------------------------------------------------------------------
// buildAttachmentStrip
// ---------------------------------------------------------------------------

test('the strip follows the manifest order and maps inline images to native blocks in order', () => {
    const strip = buildAttachmentStrip(mixedMetadata, [claudeImage('QUFB'), claudeImage('QkJC')])
    assert.deepEqual(strip.map(item => item.name), ['one.png', 'movie.mp4', 'two.png'])
    assert.equal(strip[0].src, firstNativeImage)
    assert.equal(strip[2].src, secondNativeImage)
    assert.equal(strip[1].src, undefined)
    assert.deepEqual(strip.map(item => item.mode), ['inline', 'file', 'inline'])
    assert.deepEqual(strip.map(item => item.kind), ['image', 'video', 'image'])
    assert.equal(new Set(strip.map(item => item.id)).size, 3)
})

test('documents and failed-image placeholders take their media slot between images', () => {
    const metadata = {
        owner: 'session-1',
        entries: [
            entry(1, 'a.png', 'image', 'inline'),
            entry(2, 'spec.pdf', 'PDF', 'inline'),
            entry(3, 'notes.txt', 'text', 'inline'),
            entry(4, 'broken.png', 'image', 'inline'),
            entry(5, 'c.png', 'image', 'inline'),
        ],
    }
    const strip = buildAttachmentStrip(metadata, [claudeImage('QUFB'), pdfBlock, textDocBlock, placeholderBlock, claudeImage('QkJC')])
    assert.deepEqual(strip.map(item => item.name), ['a.png', 'spec.pdf', 'notes.txt', 'broken.png', 'c.png'])
    assert.equal(strip[0].src, firstNativeImage)
    assert.equal(strip[1].src, undefined, 'an inline document is a chip')
    assert.equal(strip[2].src, undefined, 'an inline text document is a chip')
    assert.equal(strip[3].src, undefined, 'a failed image is a chip without thumbnail')
    assert.equal(strip[4].src, secondNativeImage)
})

test('file chips carry the metadata owner and artifact name; share mode never links', () => {
    const strip = buildAttachmentStrip(mixedMetadata, [claudeImage('QUFB'), claudeImage('QkJC')])
    const shareStrip = buildAttachmentStrip(mixedMetadata, [claudeImage('QUFB'), claudeImage('QkJC')], { share: true })
    assert.equal(strip[1].artifactOwner, 'session-1')
    assert.equal(strip[1].artifactName, 'movie.mp4')
    assert.equal(strip[1].canOpenArtifact, true)
    assert.equal(strip[0].canOpenArtifact, false, 'inline entries are not artifacts')
    assert.equal(shareStrip[1].canOpenArtifact, false)
    assert.deepEqual(shareStrip.map(item => item.name), strip.map(item => item.name), 'same order and names in a share')
    assert.equal(shareStrip[2].src, secondNativeImage, 'shares keep the native thumbnails')
})

test('a file chip keeps its final name on disk and a fork-parent owner', () => {
    const metadata = { owner: 'parent-session', entries: [entry(1, 'notes (1).txt', 'text', 'file')] }
    const [chip] = buildAttachmentStrip(metadata, [])
    assert.equal(chip.name, 'notes (1).txt')
    assert.equal(chip.artifactOwner, 'parent-session')
    assert.equal(chip.artifactName, 'notes (1).txt')
    assert.equal(chip.canOpenArtifact, true)
})

test('a file entry without artifact name or owner cannot open', () => {
    const noName = buildAttachmentStrip({ owner: 's', entries: [{ ...entry(1, 'x.bin', 'other', 'file'), artifact_name: null }] }, [])
    const noOwner = buildAttachmentStrip({ entries: [entry(1, 'x.bin', 'other', 'file')] }, [])
    assert.equal(noName[0].canOpenArtifact, false)
    assert.equal(noOwner[0].canOpenArtifact, false)
})

test('hybrid inline entries are chips with no native thumbnail dependency', () => {
    const metadata = { owner: 's', entries: [entry(1, 'shot.png', 'image', 'inline'), entry(2, 'log.txt', 'text', 'file')] }
    const strip = buildAttachmentStrip(metadata, [claudeImage('QUFB')], { hybrid: true })
    assert.equal(strip[0].src, undefined)
    assert.equal(strip[0].mode, 'inline')
    assert.equal(strip[1].canOpenArtifact, true)
})

test('a strip stores no filesystem path', () => {
    const strip = buildAttachmentStrip(mixedMetadata, [claudeImage('QUFB'), claudeImage('QkJC')])
    for (const item of strip) {
        assert.deepEqual(
            Object.keys(item).filter(key => !['id', 'name', 'kind', 'mode', 'src', 'artifactOwner', 'artifactName', 'canOpenArtifact'].includes(key)),
            [],
        )
        assert.ok(!String(item.artifactName ?? '').includes('/'))
    }
})

test('a missing or malformed manifest gives an empty strip', () => {
    assert.deepEqual(buildAttachmentStrip(null, []), [])
    assert.deepEqual(buildAttachmentStrip({ owner: 's' }, []), [])
    assert.deepEqual(buildAttachmentStrip({ owner: 's', entries: 'nope' }, []), [])
})

test('native image sources: Claude base64, Codex canonical and response images; other blocks none', () => {
    assert.equal(nativeImageSrc(claudeImage('QUFB')), firstNativeImage)
    assert.equal(nativeImageSrc({ type: 'image', image_url: secondNativeImage }), secondNativeImage)
    assert.equal(nativeImageSrc({ type: 'input_image', image_url: secondNativeImage }), secondNativeImage)
    assert.equal(nativeImageSrc({ type: 'local_image', path: '/tmp/x.png' }), null)
    assert.equal(nativeImageSrc(pdfBlock), null)
    assert.equal(nativeImageSrc(placeholderBlock), null)
    assert.equal(nativeImageSrc({ type: 'image', source: { type: 'base64' } }), null)
})

test('leading media slots stop at the first user text block', () => {
    const blocks = [claudeImage('QUFB'), pdfBlock, placeholderBlock, { type: 'text', text: 'hello' }, claudeImage('QkJC')]
    assert.deepEqual(leadingMediaSlots(blocks).map(slot => slot.index), [0, 1, 2])
    const codex = [{ type: 'image', image_url: firstNativeImage }, { type: 'local_image', path: '/x' }, { type: 'text', text: 'hi' }]
    assert.deepEqual(leadingMediaSlots(codex).map(slot => slot.index), [0, 1])
    assert.deepEqual(leadingMediaSlots(null), [])
})

// ---------------------------------------------------------------------------
// messageAttachmentLayout (what a bubble renders)
// ---------------------------------------------------------------------------

test('with metadata the native media blocks are not rendered on their own', () => {
    const metadata = {
        owner: 's',
        entries: [
            entry(1, 'a.png', 'image', 'inline'),
            entry(2, 'spec.pdf', 'PDF', 'inline'),
            entry(3, 'broken.png', 'image', 'inline'),
            entry(4, 'movie.mp4', 'video', 'file'),
            entry(5, 'c.png', 'image', 'inline'),
        ],
    }
    const blocks = [claudeImage('QUFB'), pdfBlock, placeholderBlock, claudeImage('QkJC'), { type: 'text', text: 'hello' }]
    const layout = messageAttachmentLayout({ twicc_attachments: metadata }, blocks)
    assert.deepEqual(layout.strip.map(item => item.name), ['a.png', 'spec.pdf', 'broken.png', 'movie.mp4', 'c.png'])
    assert.deepEqual(layout.hiddenIndices, [0, 1, 2, 3], 'no image group, no document placeholder, no placeholder text')
    assert.equal(layout.strip[4].src, secondNativeImage)
})

test('an all-file message without text renders only the strip', () => {
    const metadata = { owner: 's', entries: [entry(1, 'a.zip', 'other', 'file'), entry(2, 'b.mp3', 'audio', 'file')] }
    const sdk = messageAttachmentLayout({ twicc_attachments: metadata }, [])
    assert.equal(sdk.strip.length, 2)
    assert.deepEqual(sdk.hiddenIndices, [])
    // Hybrid: the stored string is empty once the block is extracted.
    const hybrid = messageAttachmentLayout({ twicc_attachments: metadata }, [{ type: 'text', text: '' }], { hybrid: true })
    assert.equal(hybrid.strip.length, 2)
    assert.deepEqual(hybrid.hiddenIndices, [0], 'the blank text block is not rendered')
})

test('hybrid messages keep their typed text and never consume a block', () => {
    const metadata = { owner: 's', entries: [entry(1, 'shot.png', 'image', 'inline')] }
    const layout = messageAttachmentLayout({ twicc_attachments: metadata }, [{ type: 'text', text: 'look' }], { hybrid: true })
    assert.deepEqual(layout.hiddenIndices, [])
    assert.equal(layout.strip[0].src, undefined)
})

test('metadata-free legacy messages keep the current rendering', () => {
    const blocks = [claudeImage('QUFB'), pdfBlock, { type: 'text', text: 'hello' }]
    assert.deepEqual(messageAttachmentLayout({}, blocks), { strip: null, hiddenIndices: [] })
    assert.deepEqual(messageAttachmentLayout(null, blocks), { strip: null, hiddenIndices: [] })
})

test('optimistic and failed bubbles render their attachment items with local URLs only', () => {
    const parsed = {
        attachmentCount: 3,
        attachmentItems: [
            { id: 'a', name: 'one.png', kind: 'image', src: 'blob:http://x/1' },
            { id: 'b', name: 'movie.mp4', kind: 'video', src: null },
            { id: 'c', name: 'two.png', kind: 'image', src: '/api/attachments/staging/x/content/' },
        ],
    }
    const layout = messageAttachmentLayout(parsed, [{ type: 'text', text: 'hi' }])
    assert.deepEqual(layout.strip.map(item => item.name), ['one.png', 'movie.mp4', 'two.png'])
    assert.equal(layout.strip[0].src, 'blob:http://x/1')
    assert.equal(layout.strip[2].src, undefined, 'never a staging preview URL')
    assert.ok(layout.strip.every(item => item.canOpenArtifact === false))
    assert.deepEqual(layout.hiddenIndices, [])
    assert.deepEqual(optimisticAttachmentStrip(parsed.attachmentItems), layout.strip)
    // Attachments-only optimistic bubble: no text block at all.
    assert.equal(messageAttachmentLayout({ ...parsed }, []).strip.length, 3)
})

// ---------------------------------------------------------------------------
// Artifact navigation target (derived in the current instance)
// ---------------------------------------------------------------------------

test('a file chip requests attachments/<artifact_name> of its owner; others request nothing', () => {
    const strip = buildAttachmentStrip({ owner: 'parent-session', entries: [entry(1, 'a.png', 'image', 'inline'), entry(2, 'notes (1).txt', 'text', 'file')] }, [claudeImage('QUFB')])
    assert.deepEqual(stripItemArtifactRequest(strip[1]), { owner: 'parent-session', relativePath: 'attachments/notes (1).txt' })
    assert.equal(stripItemArtifactRequest(strip[0]), null)
    const shared = buildAttachmentStrip({ owner: 'parent-session', entries: [entry(1, 'b.txt', 'text', 'file')] }, [], { share: true })
    assert.equal(stripItemArtifactRequest(shared[0]), null)
})

test('a file chip opens in the current session artifacts dir or in its owner session', () => {
    const own = artifactNavigationTarget(
        { owner: 'session-1', relativePath: 'attachments/movie.mp4' },
        { sessionId: 'session-1', artifactsDir: '/data/artifacts/session-1' },
    )
    assert.deepEqual(own, { type: 'current', absolutePath: '/data/artifacts/session-1/attachments/movie.mp4', relativePath: 'attachments/movie.mp4' })
    const fork = artifactNavigationTarget(
        { owner: 'parent-session', relativePath: 'attachments/movie.mp4' },
        { sessionId: 'fork-session', artifactsDir: '/data/artifacts/fork-session' },
    )
    assert.deepEqual(fork, { type: 'session', sessionId: 'parent-session', relativePath: 'attachments/movie.mp4' })
    const unloaded = artifactNavigationTarget(
        { owner: 'session-1', relativePath: 'attachments/movie.mp4' },
        { sessionId: 'session-1', artifactsDir: null },
    )
    assert.deepEqual(unloaded, { type: 'session', sessionId: 'session-1', relativePath: 'attachments/movie.mp4' })
})

// ---------------------------------------------------------------------------
// queuedAttachmentDisplay (Claude mid-turn prompts)
// ---------------------------------------------------------------------------

const queuedMetadata = {
    owner: 'session-1',
    entries: [
        entry(1, 'shot.png', 'image', 'inline'),
        entry(2, 'spec.pdf', 'PDF', 'inline'),
        entry(3, 'movie.mp4', 'video', 'file'),
    ],
}

function queued(prompt, extra = {}) {
    return {
        type: 'attachment',
        attachment: { type: 'queued_command', commandMode: 'prompt', prompt, ...extra },
        twicc_attachments: queuedMetadata,
    }
}

test('a queued prompt shows a native image, a document chip and a file chip, then its text', () => {
    const display = queuedAttachmentDisplay(queued([claudeImage('QUFB'), pdfBlock, { type: 'text', text: 'please check' }]))
    assert.deepEqual(display.items.map(item => item.name), ['shot.png', 'spec.pdf', 'movie.mp4'])
    assert.equal(display.items[0].src, firstNativeImage)
    assert.equal(display.items[1].src, undefined)
    assert.equal(display.items[2].canOpenArtifact, true)
    assert.equal(display.items[2].artifactName, 'movie.mp4')
    assert.equal(display.text, 'please check')
})

test('a queued prompt in share mode has no artifact link', () => {
    const display = queuedAttachmentDisplay(queued([claudeImage('QUFB'), pdfBlock]), { share: true })
    assert.equal(display.items[2].canOpenArtifact, false)
    assert.equal(display.text, '')
})

test('a queued string prompt is a hybrid-style message: chips and its text', () => {
    const display = queuedAttachmentDisplay(queued('typed text'))
    assert.equal(display.text, 'typed text')
    assert.equal(display.items[0].src, undefined)
})

test('other unknown entries get no queued display', () => {
    assert.equal(queuedAttachmentDisplay(null), null)
    assert.equal(queuedAttachmentDisplay({ type: 'attachment', attachment: { type: 'queued_command', commandMode: 'prompt', prompt: 'x' } }), null)
    assert.equal(queuedAttachmentDisplay(queued('x', { commandMode: 'bash' })), null)
    assert.equal(queuedAttachmentDisplay({ ...queued('x'), attachment: { type: 'edited_text_file', commandMode: 'prompt', prompt: 'x' } }), null)
    assert.equal(queuedAttachmentDisplay({ ...queued('x'), twicc_attachments: { owner: 's', entries: [] } }), null)
    assert.equal(queuedAttachmentDisplay(queued(42)), null)
})

// ---------------------------------------------------------------------------
// Counts and match keys
// ---------------------------------------------------------------------------

test('total counts: metadata entries, optimistic count, legacy fallback; text keys unchanged', () => {
    const parsed = { twicc_attachments: { owner: 's', entries: [entry(1, 'a', 'image', 'inline'), entry(2, 'b', 'video', 'file'), entry(3, 'c', 'other', 'file')] } }
    assert.equal(attachmentMatchKey('', attachmentCountForMessage(parsed, 1)), 'a:3')
    assert.equal(attachmentMatchKey('', attachmentCountForMessage({ attachmentCount: 2 }, 0)), 'a:2')
    assert.equal(attachmentMatchKey('', attachmentCountForMessage({}, 1)), 'a:1')
    assert.equal(attachmentMatchKey('hello', attachmentCountForMessage(parsed, 1)), 't:hello')
    assert.equal(attachmentMatchKey('  hello  ', 0), 't:hello')
    assert.equal(attachmentMatchKey('', 0), null)
})

test('in-flight snapshot counts: new attachments, else legacy medias, else mediaCount', () => {
    assert.equal(inflightAttachmentCount({ attachments: [{}, {}, {}] }), 3)
    assert.equal(inflightAttachmentCount({ medias: [{}, {}] }), 2)
    assert.equal(inflightAttachmentCount({ medias: [], mediaCount: 4 }), 4)
    assert.equal(inflightAttachmentCount({}), 0)
    // Same key on both sides of an attachments-only send.
    assert.equal(
        attachmentMatchKey('', inflightAttachmentCount({ attachments: [{}, {}, {}] })),
        attachmentMatchKey('', attachmentCountForMessage({ twicc_attachments: { entries: [{}, {}, {}] } }, 0)),
    )
})

test('placeholder blocks of failed images are not user text when metadata binds them', () => {
    const parsed = {
        twicc_attachments: { owner: 's', entries: [entry(1, 'a.png', 'image', 'inline'), entry(2, 'b.png', 'image', 'inline')] },
        message: { content: [placeholderBlock, claudeImage('QUFB'), { type: 'text', text: 'hello' }] },
    }
    const matchable = matchableUserMessage(parsed)
    assert.deepEqual(matchable.message.content.map(block => block.type), ['image', 'text'])
    assert.equal(parsed.message.content.length, 3, 'the stored item is not modified')
    // Without metadata, nothing changes (legacy semantics).
    const legacy = { message: { content: [placeholderBlock, { type: 'text', text: 'hello' }] } }
    assert.equal(matchableUserMessage(legacy), legacy)
    // A string (hybrid) or Codex item is returned as is.
    const hybrid = { twicc_attachments: parsed.twicc_attachments, message: { content: 'hello' } }
    assert.equal(matchableUserMessage(hybrid), hybrid)
})

test('kind icons cover every manifest kind', () => {
    assert.equal(attachmentKindIcon('image'), 'file-image')
    assert.equal(attachmentKindIcon('PDF'), 'file-pdf')
    assert.equal(attachmentKindIcon('text'), 'file-lines')
    assert.equal(attachmentKindIcon('video'), 'file-video')
    assert.equal(attachmentKindIcon('audio'), 'file-audio')
    assert.equal(attachmentKindIcon('other'), 'file')
    assert.equal(attachmentKindIcon('weird'), 'file')
})

// ---------------------------------------------------------------------------
// Wiring contracts (thin components over the pure functions above)
// ---------------------------------------------------------------------------

test('wiring: renderers use the shared layout, share mode is explicit, item content is never parsed by hand', () => {
    const claudeMessage = source('../components/session/detail/items/claude_code/Message.vue')
    const contentList = source('../components/session/detail/items/claude_code/ContentList.vue')
    const codexMessage = source('../components/session/detail/items/codex/Message.vue')
    const codexUser = source('../components/session/detail/items/codex/UserMessage.vue')
    const unknown = source('../components/session/detail/items/UnknownEntry.vue')
    const shareList = source('../share-session/ShareItemsList.vue')
    const strip = source('../components/media/AttachmentStrip.vue')
    const context = source('../composables/useAttachmentStripContext.js')

    assert.match(claudeMessage, /messageAttachmentLayout\(/)
    assert.match(claudeMessage, /<AttachmentStrip/)
    assert.match(contentList, /hiddenIndices/)
    assert.match(codexMessage, /messageAttachmentLayout\(/)
    assert.match(codexUser, /<AttachmentStrip/)
    assert.match(unknown, /queuedAttachmentDisplay\(/)
    assert.match(unknown, /<JsonHumanView/, 'the generic fallback stays')
    assert.match(shareList, /provide\(ATTACHMENT_SHARE_MODE, true\)/)
    assert.match(context, /inject\(ATTACHMENT_SHARE_MODE, false\)/)
    assert.match(context, /await import\('\.\.\/router'\)/, 'lazy router access')
    assert.doesNotMatch(context, /^import .*router/m)
    assert.match(strip, /'open-artifact'/)
    assert.match(strip, /inject\(ATTACHMENT_SHARE_MODE, false\)/, 'the strip itself never links in a share')
    for (const file of [claudeMessage, contentList, codexMessage, codexUser, unknown, strip, context]) {
        assert.doesNotMatch(file, /JSON\.parse|_parsedContent|item\.content\b/)
    }
    assert.equal(ATTACHMENT_SHARE_MODE.length > 0, true)
})

// ---------------------------------------------------------------------------
// Edge cases: manifest entries vs native slots, unknown kinds
// ---------------------------------------------------------------------------

test('more inline entries than native slots: the extra entries are chips without thumbnail', () => {
    const metadata = {
        owner: 's',
        entries: [entry(1, 'a.png', 'image', 'inline'), entry(2, 'b.png', 'image', 'inline'), entry(3, 'c.png', 'image', 'inline')],
    }
    const strip = buildAttachmentStrip(metadata, [claudeImage('QUFB')])
    assert.deepEqual(strip.map(item => item.name), ['a.png', 'b.png', 'c.png'])
    assert.equal(strip[0].src, firstNativeImage)
    assert.equal(Object.hasOwn(strip[1], 'src'), false)
    assert.equal(Object.hasOwn(strip[2], 'src'), false)
    const layout = messageAttachmentLayout({ twicc_attachments: metadata }, [claudeImage('QUFB'), { type: 'text', text: 'hi' }])
    assert.deepEqual(layout.hiddenIndices, [0], 'the user text is never consumed as a media slot')
})

test('fewer inline entries than native slots: the extra blocks keep the legacy rendering', () => {
    const metadata = { owner: 's', entries: [entry(1, 'a.png', 'image', 'inline'), entry(2, 'movie.mp4', 'video', 'file')] }
    const blocks = [claudeImage('QUFB'), claudeImage('QkJC'), pdfBlock, { type: 'text', text: 'hi' }]
    const layout = messageAttachmentLayout({ twicc_attachments: metadata }, blocks)
    assert.deepEqual(layout.strip.map(item => item.name), ['a.png', 'movie.mp4'])
    assert.equal(layout.strip[0].src, firstNativeImage)
    assert.deepEqual(layout.hiddenIndices, [0], 'only the bound slot is hidden; blocks 1 and 2 render as before')
})

test('an unknown manifest kind keeps its order, takes its inline slot, and renders as a generic chip', () => {
    const metadata = {
        owner: 's',
        entries: [entry(1, 'mystery.bin', 'weird', 'inline'), entry(2, 'b.png', 'image', 'inline'), entry(3, 'x.dat', 'strange', 'file')],
    }
    const strip = buildAttachmentStrip(metadata, [claudeImage('QUFB'), claudeImage('QkJC')])
    assert.deepEqual(strip.map(item => item.name), ['mystery.bin', 'b.png', 'x.dat'])
    assert.equal(Object.hasOwn(strip[0], 'src'), false, 'a non-image kind never gets a thumbnail')
    assert.equal(strip[1].src, secondNativeImage, 'the unknown inline entry consumed the first slot')
    assert.equal(attachmentKindIcon(strip[0].kind), 'file')
    assert.equal(strip[2].canOpenArtifact, true)
    const missingKind = buildAttachmentStrip({ owner: 's', entries: [{ n: 1, name: 'n', mode: 'file', artifact_name: 'n' }] }, [])
    assert.equal(missingKind[0].kind, 'other')
})

// ---------------------------------------------------------------------------
// Resend after an API error: the text of the failed turn's user message
// ---------------------------------------------------------------------------

// Mirrors ClaudeCodeHelpers.extractUserMessageText (not importable under node).
function claudeText(parsed) {
    const content = parsed?.message?.content
    if (typeof content === 'string') return content.trim() || null
    if (!Array.isArray(content)) return null
    const text = content.filter(block => block?.type === 'text' && typeof block.text === 'string').map(block => block.text).join('\n').trim()
    return text || null
}

test('resend text leaves out the placeholder of a failed image the manifest binds', () => {
    const parsed = {
        twicc_attachments: { owner: 's', entries: [entry(1, 'a.png', 'image', 'inline'), entry(2, 'movie.mp4', 'video', 'file')] },
        message: { content: [placeholderBlock, { type: 'text', text: '  check this  ' }] },
    }
    assert.equal(userMessageResendText(claudeText, parsed), 'check this')
    // Attachments only: nothing to resend as text (the caller falls back).
    const onlyPlaceholder = { ...parsed, message: { content: [placeholderBlock] } }
    assert.equal(userMessageResendText(claudeText, onlyPlaceholder), '')
    // Legacy message: unchanged semantics, the text block stays text.
    const legacy = { message: { content: [placeholderBlock, { type: 'text', text: 'hi' }] } }
    assert.equal(userMessageResendText(claudeText, legacy), `${placeholderBlock.text}\nhi`)
    assert.equal(userMessageResendText(null, parsed), '')
})

test('wiring: the API-error Resend reads its text through userMessageResendText', () => {
    const apiError = source('../components/session/detail/items/ApiError.vue')
    assert.match(apiError, /userMessageResendText\(/)
    assert.doesNotMatch(apiError, /extractUserMessageText\(getParsedContent/)
})

// ---------------------------------------------------------------------------
// Cross-layer round trip: records produced by the real backend pipeline
// (tests/test_attachment_pipeline_roundtrip.py → shared JSON fixture)
// ---------------------------------------------------------------------------

const roundtrip = JSON.parse(readFileSync(new URL('../../../tests/fixtures/attachment_roundtrip_records.json', import.meta.url), 'utf8'))

function visibleText(blocks, hiddenIndices) {
    const hidden = new Set(hiddenIndices)
    return blocks.filter((block, index) => !hidden.has(index) && TEXT_TYPES.has(block?.type)).map(block => block.text).join('')
}
const TEXT_TYPES = new Set(['text', 'input_text'])

test('round trip (Claude SDK): the backend record renders the sent files in order, natives as thumbnails', () => {
    const { sent, record } = roundtrip.claude_mixed
    const blocks = record.message.content
    const layout = messageAttachmentLayout(record, blocks)
    assert.deepEqual(layout.strip.map(item => item.name), sent.names)
    assert.deepEqual(layout.strip.map(item => item.mode), ['inline', 'file', 'inline', 'inline'])
    assert.equal(layout.strip[0].src, nativeImageSrc(blocks[0]))
    assert.equal(layout.strip[2].src, undefined, 'an inline PDF is a chip')
    assert.equal(layout.strip[3].src, nativeImageSrc(blocks[2]))
    assert.deepEqual(stripItemArtifactRequest(layout.strip[1]), { owner: record.twicc_attachments.owner, relativePath: 'attachments/capture.mp4' })
    assert.deepEqual(layout.hiddenIndices, [0, 1, 2])
    assert.equal(visibleText(blocks, layout.hiddenIndices), sent.text)
    // Share mode: same order, no artifact link.
    const shared = messageAttachmentLayout(record, blocks, { share: true })
    assert.deepEqual(shared.strip.map(item => item.name), sent.names)
    assert.ok(shared.strip.every(item => !item.canOpenArtifact))
})

test('round trip (Claude SDK): the stored line matches the optimistic bubble of the same send', () => {
    for (const name of ['claude_mixed', 'claude_file_only']) {
        const { sent, record } = roundtrip[name]
        const optimisticKey = attachmentMatchKey(sent.text, inflightAttachmentCount({ attachments: sent.names.map(() => ({})) }))
        const storedKey = attachmentMatchKey(
            claudeText(matchableUserMessage(record)),
            attachmentCountForMessage(record, record.message.content.filter(b => b.type === 'image' || b.type === 'document').length),
        )
        assert.equal(storedKey, optimisticKey, name)
        assert.notEqual(storedKey, null)
    }
})

test('round trip (Claude SDK): an all-file follow-up without text shows the strip alone', () => {
    const { sent, record } = roundtrip.claude_file_only
    const layout = messageAttachmentLayout(record, record.message.content)
    // The fixture sends `capture.mp4` a second time into the same session: a
    // file entry shows its final (deduplicated) name on disk, never an overwrite.
    assert.deepEqual(sent.names, ['capture.mp4', 'archive.zip'])
    assert.deepEqual(layout.strip.map(item => item.name), ['capture (1).mp4', 'archive.zip'])
    assert.ok(layout.strip.every(item => item.mode === 'file' && item.canOpenArtifact))
    assert.deepEqual(layout.strip.map(item => item.artifactName), ['capture (1).mp4', 'archive.zip'])
    assert.equal(claudeText(record), null)
    assert.equal(attachmentMatchKey(claudeText(record), attachmentCountForMessage(record, 0)), `a:${sent.attachmentCount}`)
})

test('round trip (Codex): images inline in order, PDF and video as files, text and key preserved', () => {
    const { sent, record } = roundtrip.codex_mixed
    const content = codexUserMessageContent(record)
    const layout = messageAttachmentLayout(record, content)
    assert.deepEqual(layout.strip.map(item => item.name), sent.names)
    assert.deepEqual(layout.strip.map(item => item.mode), ['inline', 'file', 'file', 'inline'])
    assert.equal(layout.strip[0].src, content[0].image_url)
    assert.equal(layout.strip[3].src, content[1].image_url)
    assert.deepEqual(layout.hiddenIndices, [0, 1])
    assert.equal(codexUserMessageText(record), sent.text)
    assert.equal(
        attachmentMatchKey(codexUserMessageText(matchableUserMessage(record)), attachmentCountForMessage(record, codexUserMessageAttachmentCount(record))),
        attachmentMatchKey(sent.text, sent.attachmentCount),
    )
})

test('round trip: no stored record carries the manifest block or an absolute path', () => {
    for (const { record } of [roundtrip.claude_mixed, roundtrip.claude_file_only, roundtrip.codex_mixed]) {
        const serialized = JSON.stringify(record)
        assert.doesNotMatch(serialized, /<twicc:attachments>/)
        assert.doesNotMatch(serialized, /\/artifacts\//)
    }
})
