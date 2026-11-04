// Collapsible blocks written as HTML `<details>` / `<summary>` — a markdown-it
// block rule. They render as Web Awesome `<wa-details>` (same arrow, motion and
// look as the rest of the app); the sanitizer allows that one tag. Agents emit them in their replies, but the shared instance runs
// with `html: false`, so raw HTML would show up escaped. Instead of enabling
// raw HTML, this rule recognises exactly this one construct and nothing else.
//
//     <details open>                      (`open` is optional)
//     <summary>Title</summary>            (optional; same line as the opener works too)
//
//     Any markdown: paragraphs, lists, fences, nested <details>…
//
//     </details>
//
// Like a colon container (markdownColonBlocks.js), the body is tokenized by the
// normal block parser and the whole construct is one top-level token whose `map`
// spans it entirely, so `splitMarkdownBlocks` slices it as a single block. The
// summary is one line of inline markdown.
//
// Unterminated (typical while a reply streams): the block closes at the end of
// the current scope instead of leaking raw tags. Anything that doesn't fit the
// shape (text after `</summary>`, text after the opener) is left to the
// paragraph rule, as plain escaped text.

const OPENER_RE = /^<details((?:\s+[a-z-]+(?:="[^"]*")?)*)\s*>/i
const CLOSER_RE = /^<\/details>\s*$/i
const SUMMARY_RE = /^<summary>(.*?)<\/summary>(.*)$/i
const FENCE_RE = /^(`{3,}|~{3,})/

const DEFAULT_SUMMARY = 'Details'

/** The text of `line` without its indentation. */
function lineText(state, line) {
    return state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line])
}

/** True when `line` is indented enough to be an indented code block. */
function isCodeIndented(state, line) {
    return state.sCount[line] - state.blkIndent >= 4
}

function detailsBlock(state, startLine, endLine, silent) {
    // Hot path: this rule is tried at every line start of every message. Bail
    // on the first character, before slicing anything.
    if (state.src.charCodeAt(state.bMarks[startLine] + state.tShift[startLine]) !== 0x3C /* < */) return false
    if (isCodeIndented(state, startLine)) return false
    const first = lineText(state, startLine).trim()
    const opener = OPENER_RE.exec(first)
    if (!opener) return false

    // What follows the opener on its line: nothing, or the summary.
    const rest = first.slice(opener[0].length).trim()
    let summary = null
    let bodyStart = startLine + 1
    if (rest) {
        const match = SUMMARY_RE.exec(rest)
        if (!match || match[2].trim()) return false
        summary = match[1].trim()
    } else {
        // The summary, if any, is the first non-blank line after the opener.
        let line = startLine + 1
        while (line < endLine && state.isEmpty(line)) line++
        if (line < endLine) {
            const match = SUMMARY_RE.exec(lineText(state, line).trim())
            if (match && !match[2].trim()) {
                summary = match[1].trim()
                bodyStart = line + 1
            }
        }
    }

    // Find the matching closer, counting nested openers and skipping fences.
    let depth = 0
    let fence = null
    let closeLine = -1
    for (let line = bodyStart; line < endLine; line++) {
        const text = lineText(state, line).trim()
        const fenceMatch = FENCE_RE.exec(text)
        if (fence) {
            if (fenceMatch && fenceMatch[1][0] === fence[0] && fenceMatch[1].length >= fence.length) fence = null
            continue
        }
        if (fenceMatch) {
            fence = fenceMatch[1]
            continue
        }
        if (CLOSER_RE.test(text)) {
            if (depth === 0) {
                closeLine = line
                break
            }
            depth--
        } else if (OPENER_RE.test(text)) {
            depth++
        }
    }
    const closed = closeLine !== -1
    const bodyEnd = closed ? closeLine : endLine

    // Validation pass (paragraph interruption): the opener alone is enough.
    if (silent) return true

    const oldParent = state.parentType
    const oldLineMax = state.lineMax
    state.parentType = 'container'
    state.lineMax = bodyEnd

    const open = state.push('details_open', 'details', 1)
    open.markup = '<details>'
    open.info = summary || DEFAULT_SUMMARY
    open.meta = { open: /\bopen\b/i.test(opener[1]) }
    open.map = [startLine, closed ? closeLine + 1 : bodyEnd]

    state.md.block.tokenize(state, bodyStart, bodyEnd)

    state.push('details_close', 'details', -1).markup = '</details>'

    state.parentType = oldParent
    state.lineMax = oldLineMax
    state.line = closed ? closeLine + 1 : bodyEnd
    return true
}

/**
 * Register the `<details>` rule and renderers on a markdown-it instance.
 * @param {import('markdown-it')} md
 */
export function installDetailsBlocks(md) {
    md.block.ruler.before('fence', 'details_block', detailsBlock, {
        alt: ['paragraph', 'reference', 'blockquote', 'list'],
    })

    md.renderer.rules.details_open = (tokens, idx) => {
        const token = tokens[idx]
        const openAttr = token.meta.open ? ' open' : ''
        return `<wa-details class="md-details"${openAttr}><span slot="summary">${md.renderInline(token.info)}</span>`
    }
    md.renderer.rules.details_close = () => '</wa-details>'
}
