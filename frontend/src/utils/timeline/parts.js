// frontend/src/utils/timeline/parts.js
//
// The segments a timeline wording is made of. The row component renders each type its own way; nothing here is
// ever HTML. Pure.
//   - `{type: 'text', text}`: prose.
//   - `{type: 'title', text}`: a value from data (a session title), styled apart from the prose, without quotes.
//   - `{type: 'arrow', label}`: the arrow between an old and a new value; `label` is its text alternative.
//   - `{type: 'session', mention}`: a session mention (built by sessions.js).

/** The text alternative of the arrow: "Renamed A to B". */
export const ARROW_LABEL = 'to'

export function textPart(text) {
    return { type: 'text', text }
}

export function titlePart(text) {
    return { type: 'title', text }
}

export function arrowPart() {
    return { type: 'arrow', label: ARROW_LABEL }
}

/** Glue adjacent text parts into one (the wording reads the same, the component renders fewer nodes). */
export function mergeTextParts(parts) {
    const merged = []
    for (const part of parts) {
        const previous = merged[merged.length - 1]
        if (part.type === 'text' && previous?.type === 'text') merged[merged.length - 1] = textPart(previous.text + part.text)
        else merged.push(part)
    }
    return merged
}

/**
 * The last word of the wording, apart from the rest, so the row can keep a tag glued to it (they wrap together).
 *
 * @param {Array<Object>} parts
 * @returns {{lead: Array<Object>, last: Object|null}} `last` is a part of the same type holding the last word (and its
 *   trailing spaces); null when the last part is not text nor a title, or holds no word: `lead` is then `parts`.
 */
export function splitLastWord(parts) {
    const last = parts[parts.length - 1]
    if (!last || (last.type !== 'text' && last.type !== 'title')) return { lead: parts, last: null }
    const match = /^([\s\S]*\s)?(\S+\s*)$/.exec(last.text)
    if (!match) return { lead: parts, last: null }
    const head = match[1] ?? ''
    const lead = parts.slice(0, -1)
    if (head) lead.push({ ...last, text: head })
    return { lead, last: { ...last, text: match[2] } }
}
