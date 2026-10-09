/** Original canonical text coordinates, independent of transcript formatting. */
import { parseInlineArtifactBlocks, publicationKey } from './publications.js'
import { artifactKey } from './context.js'
import { commandToText } from '../utils/command.js'

export function createInlineTextContext(context, textBlocks) {
    if (!context?.publicationAllowed || !context.finalized) return null
    let offset = 0
    const recognizedSpans = []
    for (const { textBlockIndex, text } of textBlocks) {
        for (const span of parseInlineArtifactBlocks(text)) {
            recognizedSpans.push({ ...span, start: offset + span.start, end: offset + span.end,
                textBlockIndex, tag_offset: span.start })
        }
        offset += Array.from(text).length
    }
    return { sessionId: context.sessionId, lineNum: context.lineNum, sourceOffset: 0,
        finalized: true, publicationAllowed: true, recognizedSpans }
}

export function segmentInlineTextContext(context, offset) {
    return context ? { ...context, sourceOffset: context.sourceOffset + offset } : null
}

export function displayInlineText(text, context) {
    const trimmed = text.trim()
    const command = commandToText(trimmed)
    if (command !== null) return { source: command, inlineContext: null }
    const offset = Array.from(text.slice(0, text.length - text.trimStart().length)).length
    return { source: trimmed, inlineContext: segmentInlineTextContext(context, offset) }
}

export function inlineArtifactPlacement(context, span, runtime) {
    if (span.error) return { status: 'invalid', error: span.error }
    const descriptor = span.descriptor
    const key = artifactKey(context.sessionId, descriptor.artifact_id)
    const placementKey = publicationKey(context.sessionId, { line_num: context.lineNum,
        text_block_index: span.textBlockIndex, tag_offset: span.tag_offset })
    const entry = runtime?.entries.get(key)
    const status = !entry?.present ? 'absent'
        : entry.descriptor.publicationKey === placementKey ? entry.descriptor.status : 'superseded'
    return { status, artifactKey: key, publicationKey: placementKey, title: descriptor.title }
}
