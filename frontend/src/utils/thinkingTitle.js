// frontend/src/utils/thinkingTitle.js
// Title of a Thinking block (Claude Code ``thinking``, Codex ``reasoning``),
// shown as the description in the block's summary bar.

// A markdown heading: 1-6 ``#``, then the text, minus optional closing ``#``s.
const HEADING_RE = /^#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/
// A line made only of one bold span: ``**text**``, no ``**`` inside.
const BOLD_RE = /^\*\*((?:(?!\*\*).)+)\*\*$/

/**
 * Title of a thinking text: its first non-blank line when that line is a
 * markdown heading (``# Title``) or one bold span (``**Title**``).
 *
 * Only the first line is read — the scan stops at its line break — so the
 * cost does not grow with the text, which matters while it streams in.
 * A streaming ``**Title`` without its closing ``**`` yet gives no title (no
 * half-parsed markup in the summary); a streaming heading grows with it.
 *
 * @param {string} text
 * @returns {string|null} the title, or null when the first line is neither
 */
export function extractThinkingTitle(text) {
    if (!text) return null
    let start = 0
    while (start < text.length) {
        const code = text.charCodeAt(start)
        // space, tab, \n, \r
        if (code !== 32 && code !== 9 && code !== 10 && code !== 13) break
        start++
    }
    const end = text.indexOf('\n', start)
    const line = text.slice(start, end === -1 ? text.length : end).trimEnd()
    const title = (HEADING_RE.exec(line) ?? BOLD_RE.exec(line))?.[1].trim()
    return title || null
}
