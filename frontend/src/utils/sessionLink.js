// Markdown links to sessions, inserted into the composer by dragging a sidebar
// session onto the chat or through the row menu "Insert link in message".

import { getSessionDisplayName } from './sessionTitle.js'

// Characters that change inline rendering inside link text. `_` is handled apart:
// inside a word (`snake_case`) it never opens emphasis, so it stays as it is.
const ALWAYS_ESCAPED = /[\\[\]`*<>~&]/g
const WORD_CHAR = /[\p{L}\p{N}]/u

/**
 * Escape a session title for use as Markdown link text: line breaks become one
 * space, and the characters that would change the rendering get a backslash.
 */
export function escapeLinkLabel(title) {
    const flat = String(title ?? '').replace(/\s*[\r\n]+\s*/g, ' ')
    let result = ''
    for (let i = 0; i < flat.length; i++) {
        const char = flat[i]
        if (char === '_') {
            const inWord = WORD_CHAR.test(flat[i - 1] ?? '') && WORD_CHAR.test(flat[i + 1] ?? '')
            result += inWord ? '_' : '\\_'
        } else {
            result += char.replace(ALWAYS_ESCAPED, '\\$&')
        }
    }
    return result
}

/**
 * The Markdown link to a session: `[title](/project/<project_id>/session/<id>)`.
 * Always the canonical session route, whatever the current sidebar mode, so the
 * link stays valid when read later.
 */
export function buildSessionLinkMarkdown(session, router) {
    const { href } = router.resolve({
        name: 'session',
        params: { projectId: session.project_id, sessionId: session.id },
    })
    return `[${escapeLinkLabel(getSessionDisplayName(session))}](${href})`
}

/**
 * Surround `text` with one space on each side where it would otherwise touch a
 * character that is not whitespace (a line break counts as whitespace).
 */
export function padInsertion(before, text, after) {
    const lead = before && !/\s$/.test(before) ? ' ' : ''
    const tail = after && !/^\s/.test(after) ? ' ' : ''
    return `${lead}${text}${tail}`
}
