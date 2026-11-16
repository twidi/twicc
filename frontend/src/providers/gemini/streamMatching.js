// frontend/src/providers/gemini/streamMatching.js
//
// Match a live streaming block against the persisted Gemini record that
// replaces it. Gemini streams over ACP, whose chunks carry no record id: the
// streaming ``messageId`` is a TwiCC-minted turn segment id, so the match is
// by content, never by identity.
//
//  - text block     ↔ ``assistant_message`` whose final text equals the
//                     streamed text (or one is a prefix of the other).
//  - thinking block ↔ ``reasoning`` / ``assistant_message`` / ``tool_use``
//                     whose first thought appears in the streamed text
//                     (thinking chunks read ``**Subject**\ndescription``).
//
// Pure module (no helpers import): see scripts/audit_stream_matching_imports.py.

import { assistantText, recordThoughts } from './records.js'

// Length of the first thought's text looked up in the streamed thinking.
// Long enough to be specific, short enough to survive small formatting
// differences later in the description.
const THOUGHT_PROBE_LENGTH = 80

const THINKING_KINDS = new Set(['reasoning', 'assistant_message', 'tool_use'])

// Whitespace-insensitive form: streamed chunks and persisted records can
// differ in line breaks and spacing around the same words.
function normalize(text) {
    return typeof text === 'string' ? text.replace(/\s+/g, ' ').trim() : ''
}

// The block's accumulated text: the raw text the store appended from the
// deltas (``displayedText`` lags behind it while the buffer drains).
function blockText(block) {
    return normalize(typeof block?.text === 'string' ? block.text : block?.displayedText)
}

export function matchesStreamingBlock(parsed, itemKind, messageId, block) {
    if (!block || !parsed || typeof parsed !== 'object' || parsed.type !== 'gemini') return false
    const streamed = blockText(block)
    if (!streamed) return false

    if (block.blockType === 'text') {
        if (itemKind !== 'assistant_message') return false
        const persisted = normalize(assistantText(parsed))
        if (!persisted) return false
        return persisted === streamed || persisted.startsWith(streamed) || streamed.startsWith(persisted)
    }

    if (block.blockType === 'thinking') {
        if (!THINKING_KINDS.has(itemKind)) return false
        const [first] = recordThoughts(parsed)
        if (!first) return false
        const probe = normalize(first.description) || normalize(first.subject)
        return !!probe && streamed.includes(probe.slice(0, THOUGHT_PROBE_LENGTH).trim())
    }

    return false
}
