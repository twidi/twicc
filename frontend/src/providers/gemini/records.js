// frontend/src/providers/gemini/records.js
//
// Pure readers over Gemini CLI session records (one JSONL record per
// SessionItem). No Vue, Pinia or i18n import: shared by the helpers, the
// stream matching and the item renderers, and importable under node:test.
//
// Record shapes (Gemini CLI session file):
//   user:      { id, timestamp, type: 'user', content: string | Part[] }
//   assistant: { id, timestamp, type: 'gemini', content: string,
//                thoughts?: [{ subject, description, timestamp }],
//                toolCalls?: [{ id, name, args, result, status, displayName,
//                               description, resultDisplay, … }],
//                tokens?, model? }
//   error:     { id, timestamp, type: 'error', content: string }
// A Part is ``{ text }``, ``{ functionResponse }``, ``{ inlineData }``, …

import { TERMINAL_TOOL_STATUSES } from './constants.js'

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

/** Number of media parts (``inlineData`` / ``fileData``) of a user record. */
export function userMessageAttachmentCount(parsed) {
    if (!isObject(parsed)) return 0
    return contentParts(parsed).filter(part => isObject(part.inlineData) || isObject(part.fileData)).length
}

/** Native user record shape for an optimistic bubble (text only). */
export function buildUserRecord(text) {
    return { type: 'user', content: text ? [{ text }] : [] }
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
