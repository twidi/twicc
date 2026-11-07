import { i18n } from '../../i18n/index.js'

// frontend/src/utils/timeline/parts.js
//
// The segments a timeline wording is made of. The row component renders each type its own way; nothing here is
// ever HTML. Pure.
//   - `{type: 'text', text}`: prose.
//   - `{type: 'value', text}`: a value from data (a session title, a setting value), styled apart from the prose,
//     without quotes.
//   - `{type: 'arrow', label}`: the arrow between an old and a new value; `label` is its text alternative.
//   - `{type: 'session', mention}`: a session mention (built by sessions.js).

/** The text alternative of the arrow: "Renamed A to B". */
export const ARROW_LABEL = 'to'

export function textPart(text) {
    return { type: 'text', text }
}

export function valuePart(text) {
    return { type: 'value', text }
}

export function arrowPart() {
    return { type: 'arrow', label: i18n.global.t('session.timeline.to') }
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
 *   trailing spaces); null when the last part is not text nor a value, or holds no word: `lead` is then `parts`.
 */
export function splitLastWord(parts) {
    const last = parts[parts.length - 1]
    if (!last || (last.type !== 'text' && last.type !== 'value')) return { lead: parts, last: null }
    const match = /^([\s\S]*\s)?(\S+\s*)$/.exec(last.text)
    if (!match) return { lead: parts, last: null }
    const head = match[1] ?? ''
    const lead = parts.slice(0, -1)
    if (head) lead.push({ ...last, text: head })
    return { lead, last: { ...last, text: match[2] } }
}

/** Build localized prose around typed rich parts without exposing data as message syntax. */
export function messageParts(key, slots = {}, count) {
    const markers = Object.fromEntries(Object.keys(slots).map(name => [name, `\uFFF0${name}\uFFF1`]))
    const message = i18n.global.t(key, markers, count)
    return message.split(/(\uFFF0[^\uFFF1]+\uFFF1)/u).flatMap(piece => {
        if (!piece) return []
        if (piece.startsWith('\uFFF0')) {
            const value = slots[piece.slice(1, -1)]
            return Array.isArray(value) ? value : [value]
        }
        return [textPart(piece)]
    })
}
