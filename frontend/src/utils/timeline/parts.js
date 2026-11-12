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
 * A word can span several parts when no space separates them: in "(Bash)" the value and the parentheses are three
 * parts, and the glued run must hold all of them, or the tag would drag the closing ")" alone to the next line.
 *
 * @param {Array<Object>} parts
 * @returns {{lead: Array<Object>, last: Array<Object>|null}} `last` is the run of parts holding the last word (and
 *   its trailing spaces), in order; null when the last part is not text nor a value, or holds no word: `lead` is
 *   then `parts`.
 */
export function splitLastWord(parts) {
    const lead = [...parts]
    const last = []
    while (lead.length) {
        const part = lead[lead.length - 1]
        if (part.type !== 'text' && part.type !== 'value') break
        // Past the first part, a space at its end ends the word: the run is complete.
        if (last.length && /\s$/.test(part.text)) break
        const match = /^([\s\S]*\s)?(\S+\s*)$/.exec(part.text)
        if (!match) break
        lead.pop()
        const head = match[1] ?? ''
        if (head) lead.push({ ...part, text: head })
        last.unshift({ ...part, text: match[2] })
        // A part not taken whole holds the start of the word: the run is complete.
        if (head) break
    }
    return { lead, last: last.length ? last : null }
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
