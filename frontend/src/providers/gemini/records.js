// frontend/src/providers/gemini/records.js
//
// Pure readers over Gemini CLI session records (one JSONL record per
// SessionItem). No Vue, Pinia or i18n import: shared by the helpers, the
// stream matching and the item renderers, and importable under node:test.
//
// Record shapes (Gemini CLI session file):
//   user:      { id, timestamp, type: 'user', content: string | Part[],
//                twicc_attachments? }  (the attachment manifest TwiCC
//                extracted from the prompt at ingestion)
//   assistant: { id, timestamp, type: 'gemini', content: string,
//                thoughts?: [{ subject, description, timestamp }],
//                toolCalls?: [{ id, name, args, result, status, displayName,
//                               description, resultDisplay, … }],
//                tokens?, model? }
//   error:     { id, timestamp, type: 'error', content: string }
// A Part is ``{ text }``, ``{ functionResponse }``, ``{ inlineData }``, …

import { AGENT_TOOL_NAME, TERMINAL_TOOL_STATUSES } from './constants.js'

function isObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/** Parts of a record's ``content``: a plain string becomes one text part. */
function contentParts(parsed) {
    const content = parsed?.content
    if (typeof content === 'string') return [{ text: content }]
    return Array.isArray(content) ? content.filter(isObject) : []
}

/**
 * Text of a user record: its text parts joined with "" (Gemini splits one
 * prompt into consecutive parts). ``functionResponse`` and media parts are
 * ignored. Returns '' when there is no text.
 */
export function userMessageText(parsed) {
    if (!isObject(parsed)) return ''
    return contentParts(parsed)
        .filter(part => typeof part.text === 'string')
        .map(part => part.text)
        .join('')
}

function isMediaPart(part) {
    return isObject(part.inlineData) || isObject(part.fileData)
}

/** Number of media parts (``inlineData`` / ``fileData``) of a user record. */
export function userMessageAttachmentCount(parsed) {
    if (!isObject(parsed)) return 0
    return contentParts(parsed).filter(isMediaPart).length
}

function partMediaType(media) {
    return typeof media.mimeType === 'string' ? media.mimeType.split(';')[0].trim().toLowerCase() : ''
}

/** Last segment of a URI or path, or null. */
function uriBasename(uri) {
    if (typeof uri !== 'string') return null
    const name = uri.split(/[\\/]/).pop().trim()
    return name || null
}

/**
 * Media parts of a user record as the provider-neutral content blocks the
 * shared attachment strip reads (``utils/attachmentStrip.js``), in content
 * order:
 *  - ``inlineData`` image → ``{ type: 'image', source: { type: 'base64', … } }``
 *    (a thumbnail);
 *  - any other ``inlineData`` → ``{ type: 'document', source: { type:
 *    'base64', … } }`` (a PDF / text / file tile);
 *  - ``fileData`` (a URI the browser cannot load) → an image tile
 *    (``local_image``) or a document tile named after the URI.
 */
export function userMessageMediaBlocks(parsed) {
    if (!isObject(parsed)) return []
    return contentParts(parsed).filter(isMediaPart).map(part => {
        if (isObject(part.inlineData)) {
            const mediaType = partMediaType(part.inlineData) || 'application/octet-stream'
            const source = { type: 'base64', media_type: mediaType, data: typeof part.inlineData.data === 'string' ? part.inlineData.data : '' }
            return mediaType.startsWith('image/') ? { type: 'image', source } : { type: 'document', source }
        }
        const mediaType = partMediaType(part.fileData)
        const name = uriBasename(part.fileData.fileUri)
        if (mediaType.startsWith('image/')) return { type: 'local_image', path: part.fileData.fileUri ?? '' }
        const block = { type: 'document', source: { type: 'base64', media_type: mediaType || 'application/octet-stream' } }
        if (name) block.title = name
        return block
    })
}

/**
 * Native user record shape for an optimistic bubble: the text part, then
 * one ``inlineData`` part per legacy media block (SDK-format ``images`` /
 * ``documents``), so the bubble's attachment count matches the stored
 * record's. Only images decode their data (thumbnails); a text document
 * keeps its raw text, never read. Composer attachments are not parts: the
 * generic store adds them as ``attachmentItems`` / ``attachmentCount``.
 */
export function buildUserRecord(text, attachments = null) {
    const content = text ? [{ text }] : []
    const blocks = [...(attachments?.images ?? []), ...(attachments?.documents ?? [])]
    for (const block of blocks) {
        const source = block?.source
        if (typeof source?.data !== 'string' || !source.data) continue
        content.push({ inlineData: { mimeType: source.media_type || 'application/octet-stream', data: source.data } })
    }
    return { type: 'user', content }
}

/** Whether a tool name runs a subagent (``invoke_agent``). */
export function isAgentToolName(name) {
    return name === AGENT_TOOL_NAME
}

/** The agent name of an ``invoke_agent`` call's arguments, or null. */
export function invokedAgentName(args) {
    const value = isObject(args) ? args.agent_name : null
    return typeof value === 'string' && value.trim() ? value.trim() : null
}

/** Native assistant record shape carrying a final text. */
export function buildAssistantRecord(text) {
    return { type: 'gemini', id: 'twicc-ephemeral-result', content: text ?? '' }
}

/** Final markdown text of an assistant record ('' when none). */
export function assistantText(parsed) {
    if (!isObject(parsed) || parsed.type !== 'gemini') return ''
    return typeof parsed.content === 'string' ? parsed.content : userMessageText(parsed)
}

/** Thoughts of an assistant record with a subject or a description. */
export function recordThoughts(parsed) {
    const thoughts = isObject(parsed) ? parsed.thoughts : null
    if (!Array.isArray(thoughts)) return []
    return thoughts.filter(thought => isObject(thought)
        && ((typeof thought.subject === 'string' && thought.subject.trim())
            || (typeof thought.description === 'string' && thought.description.trim())))
}

/**
 * Markdown source for a list of thoughts: each thought is its bold subject
 * followed by its description, thoughts separated by a blank line. This is
 * also the shape of a streamed thinking block (``**Subject**\ndescription``),
 * so the persisted and the live renderings read the same.
 */
export function thoughtsMarkdown(thoughts) {
    return (thoughts ?? [])
        .map(thought => {
            const subject = typeof thought.subject === 'string' ? thought.subject.trim() : ''
            const description = typeof thought.description === 'string' ? thought.description.trim() : ''
            return [subject ? `**${subject}**` : '', description].filter(Boolean).join('\n\n')
        })
        .filter(Boolean)
        .join('\n\n')
}

/** Whether a tool call reached a terminal status. */
export function isToolCallFinished(call) {
    return isObject(call) && TERMINAL_TOOL_STATUSES.includes(call.status)
}

/**
 * Finished tool calls of an assistant record. The backend only stores
 * terminal calls; still-running ones are skipped defensively.
 */
export function finishedToolCalls(parsed) {
    const calls = isObject(parsed) ? parsed.toolCalls : null
    if (!Array.isArray(calls)) return []
    return calls.filter(isToolCallFinished)
}

/**
 * Output and error texts of a tool call, from its ``functionResponse``
 * results (``response.output`` / ``response.error``), joined when several.
 * Falls back to a plain-string ``resultDisplay``.
 */
export function toolCallOutput(call) {
    const outputs = []
    const errors = []
    for (const entry of Array.isArray(call?.result) ? call.result : []) {
        const response = entry?.functionResponse?.response
        if (!isObject(response)) continue
        if (response.output != null) outputs.push(stringify(response.output))
        if (response.error != null) errors.push(stringify(isObject(response.error) && typeof response.error.message === 'string' ? response.error.message : response.error))
    }
    if (!outputs.length && !errors.length && typeof call?.resultDisplay === 'string' && call.resultDisplay) {
        outputs.push(call.resultDisplay)
    }
    return {
        output: outputs.filter(Boolean).join('\n\n') || null,
        error: errors.filter(Boolean).join('\n\n') || null,
    }
}

function stringify(value) {
    if (typeof value === 'string') return value
    try {
        return JSON.stringify(value, null, 2)
    } catch {
        return String(value)
    }
}

/** File diff carried by a tool call's ``resultDisplay`` (file edits), or null. */
export function toolCallFileDiff(call) {
    const display = call?.resultDisplay
    if (!isObject(display) || typeof display.fileDiff !== 'string') return null
    return {
        fileDiff: display.fileDiff,
        fileName: typeof display.fileName === 'string' ? display.fileName : null,
        filePath: typeof display.filePath === 'string' ? display.filePath : null,
        originalContent: typeof display.originalContent === 'string' ? display.originalContent : null,
        newContent: typeof display.newContent === 'string' ? display.newContent : null,
    }
}

/**
 * Data for the shared ``ApiError`` component. A native ``error`` record is
 * reshaped into the generic ``twicc_provider_error`` form; anything else
 * (e.g. a backend-written ``twicc_provider_error``) passes through.
 */
export function apiErrorData(parsed) {
    if (isObject(parsed) && parsed.type === 'error') {
        return {
            ...parsed,
            type: 'twicc_provider_error',
            provider: 'gemini',
            error: { type: 'error', message: typeof parsed.content === 'string' ? parsed.content : stringify(parsed.content) },
        }
    }
    return parsed
}
