// Markdown rendering engine
// Uses markdown-it-async + shiki (syntax highlighting) + DOMPurify (XSS protection)
// Mermaid diagrams are rendered post-parse by the MarkdownContent component.

import MarkdownItAsync from 'markdown-it-async'
import backtick from 'markdown-it/lib/rules_inline/backticks.mjs'
import image from 'markdown-it/lib/rules_inline/image.mjs'
import { fromAsyncCodeToHtml } from '@shikijs/markdown-it/async'
import { codeToHtml } from 'shiki'
import DOMPurify from 'dompurify'
import { installColonBlocks } from './markdownColonBlocks.js'
import { hashString } from './hash.js'

// Configure markdown-it with all features enabled
const md = MarkdownItAsync({
    html: false,         // disable raw HTML input (security)
    linkify: true,       // auto-detect URLs (only explicit http(s):// thanks to fuzzy* disabled below)
    typographer: true,   // smart quotes, dashes
    breaks: true,        // convert \n to <br> (matches pre-wrap behavior)
})

// Only auto-link URLs with an explicit protocol (http:// or https://).
// Without this, linkify-it treats any word followed by a known TLD as a link
// (e.g. "example.py" or "config.json" would become clickable links).
md.linkify.set({ fuzzyLink: false, fuzzyEmail: false, fuzzyIP: false })

// Open absolute links in a new tab for safety and UX (content stays in place).
// Relative links (e.g. /projects) stay in the same tab for SPA navigation.
const defaultLinkOpenRender = md.renderer.rules.link_open || function (tokens, idx, options, env, self) {
    return self.renderToken(tokens, idx, options)
}
md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
    const href = tokens[idx].attrGet('href') || ''
    const isAbsolute = /^(?:https?:)?\/\//i.test(href)
    if (isAbsolute) {
        tokens[idx].attrSet('target', '_blank')
        tokens[idx].attrSet('rel', 'noopener noreferrer')
    }
    return defaultLinkOpenRender(tokens, idx, options, env, self)
}

// Per-render opt-out of `breaks: true` for soft (single-newline) line breaks.
// The shared instance keeps newlines as <br> so chat messages preserve the
// line breaks a user typed. Tips, however, are authored with soft line wraps
// purely for source readability; a render that sets `env.softBreakAsSpace`
// collapses each such wrap to a space — standard CommonMark paragraph behavior
// (blank lines still start a new paragraph, and block constructs like lists,
// code fences, and tables are unaffected, since they don't go through
// softbreak). Hard breaks (two trailing spaces or a trailing `\`) still render
// as <br>, leaving authors an explicit escape hatch for a forced line break.
const defaultSoftbreakRender = md.renderer.rules.softbreak
    || ((tokens, idx, options) => (options.breaks ? (options.xhtmlOut ? '<br />\n' : '<br>\n') : '\n'))
md.renderer.rules.softbreak = function (tokens, idx, options, env, self) {
    if (env && env.softBreakAsSpace) return ' '
    return defaultSoftbreakRender(tokens, idx, options, env, self)
}

// A leading slash command at the very start of a message: `/` followed by a
// command name (namespaced names like `/twicc:twicc-info` included), ended by
// whitespace or end of text. A path like `/home/...` doesn't match (the second
// `/` is not a valid name character and doesn't qualify as a terminator).
export const LEADING_SLASH_COMMAND_RE = /^\/[A-Za-z0-9_:-]+(?=\s|$)/

// Render the leading `/command` of a user message as a tag. Opt-in per render
// via `env.tagLeadingSlashCommand` (set only for the first block of a user
// message). Runs after `text_join`, so inline children are in their final
// shape: if the first block is a paragraph whose first inline child is a text
// token starting with a slash command, the command is split off into a
// dedicated token rendered as a styled <span>.
md.core.ruler.push('slash_command_tag', (state) => {
    if (!state.env.tagLeadingSlashCommand) return
    const [open, inline] = state.tokens
    if (open?.type !== 'paragraph_open' || inline?.type !== 'inline') return
    const first = inline.children[0]
    if (!first || first.type !== 'text') return
    const match = first.content.match(LEADING_SLASH_COMMAND_RE)
    if (!match) return

    const tag = new state.Token('slash_command_tag', '', 0)
    tag.content = match[0]
    // Keep the whitespace after the command: selecting/copying the rendered
    // text must yield "/goal fix", not "/goalfix".
    first.content = first.content.slice(match[0].length)
    if (first.content) {
        inline.children.unshift(tag)
    } else {
        inline.children[0] = tag
    }
})

md.renderer.rules.slash_command_tag = (tokens, idx) =>
    `<span class="slash-command-tag">${md.utils.escapeHtml(tokens[idx].content)}</span>`

// Colon blocks (`:: line` / `::: container`) — see utils/markdownColonBlocks.js.
installColonBlocks(md)

// Hide HTML comments (`<!-- ... -->`) from the rendered output.
//
// With `html: false`, markdown-it treats a comment as plain text and renders it
// escaped (visibly), both as a standalone block and inline within a paragraph.
// We strip it instead — but only in real text. Comments inside a fenced/indented
// code block or inline code must stay visible (they're example content), which
// falls out for free: those constructs are tokenized by their own
// higher-priority rules, so the comment characters never reach these two.
//
// A naive `source.replace(/<!--.*?-->/gs, '')` preprocessing would wrongly wipe
// comments inside code as well; token-level rules are the surgical fix.

// Block rule: a comment that begins a line (after indentation), possibly spanning
// several lines (blank lines included). Registered before `paragraph` so fenced
// (`fence`) and indented (`code`) blocks — which run earlier — keep priority.
// It pushes a map-less hidden token: `splitMarkdownBlocks` collects only mapped
// tokens, so the comment forms no block at all (no empty wrapper), and the
// full-document render draws it as '' via the renderer rule below.
function htmlCommentBlock(state, startLine, endLine, silent) {
    const pos = state.bMarks[startLine] + state.tShift[startLine]
    if (state.src.charCodeAt(pos) !== 0x3C /* < */) return false
    if (state.src.slice(pos, pos + 4) !== '<!--') return false

    // Find the line holding the closing `-->`, scanning forward from the opener.
    let closeLine = -1
    for (let line = startLine; line < endLine; line++) {
        const lineText = state.src.slice(
            state.bMarks[line] + state.tShift[line],
            state.eMarks[line],
        )
        // On the opening line, start past `<!--` so `<!-->` isn't misread as closed.
        const closeIdx = lineText.indexOf('-->', line === startLine ? 4 : 0)
        if (closeIdx === -1) continue
        // Real text after `-->` means this isn't a standalone comment block; bail
        // to the paragraph rule so the inline rule strips only the comment and
        // keeps the trailing text.
        if (lineText.slice(closeIdx + 3).trim() !== '') return false
        closeLine = line
        break
    }
    // Unterminated: leave it as plain text rather than swallowing the rest.
    if (closeLine === -1) return false

    if (silent) return true

    state.line = closeLine + 1
    const token = state.push('html_comment', '', 0)
    token.hidden = true
    return true
}
md.block.ruler.before('paragraph', 'html_comment', htmlCommentBlock)

// Inline rule: a `<!-- ... -->` embedded in text. Surrounding text is preserved
// (only the comment span is consumed). An unterminated `<!--` is left as text.
function htmlCommentInline(state, silent) {
    const start = state.pos
    if (state.src.charCodeAt(start) !== 0x3C /* < */) return false
    if (state.src.slice(start, start + 4) !== '<!--') return false
    const closeIdx = state.src.indexOf('-->', start + 4)
    if (closeIdx === -1 || closeIdx + 3 > state.posMax) return false
    if (!silent) {
        const token = state.push('html_comment', '', 0)
        token.hidden = true
    }
    state.pos = closeIdx + 3
    return true
}
md.inline.ruler.before('html_inline', 'html_comment', htmlCommentInline)

// Syntax inspection records the native code rule's consumed source span.
// Ordinary renders delegate without adding metadata or changing parser options.
md.inline.ruler.at('backticks', (state, silent) => {
    const start = state.pos
    const tokenCount = state.tokens.length
    const matched = backtick(state, silent)
    if (state.env.recordInlineSourceSpans && !silent && state.tokens.length > tokenCount) {
        const token = state.tokens.at(-1)
        if (token.type === 'code_inline') token.meta = { sourceSpan: [start, state.pos] }
    }
    return matched
})

// Image labels parse a substring. Record its origin for nested code spans.
md.inline.ruler.at('image', (state, silent) => {
    const start = state.pos
    const tokenCount = state.tokens.length
    const matched = image(state, silent)
    if (state.env.recordInlineSourceSpans && !silent && state.tokens.length > tokenCount) {
        const token = state.tokens.at(-1)
        if (token.type === 'image') token.meta = { sourceLabelOffset: start + 2 }
    }
    return matched
})

md.renderer.rules.html_comment = () => ''

// Wrapper around codeToHtml that falls back to 'text' (plain) for unknown languages.
// Shiki throws an error when encountering unsupported languages (like 'env', 'dotenv', etc.)
// which would crash the entire markdown render. This wrapper catches those errors.
async function codeToHtmlWithFallback(code, options) {
    try {
        return await codeToHtml(code, options)
    } catch (error) {
        // If it's a language-not-found error, retry with 'text' (no highlighting)
        if (error.message?.includes('is not included in this bundle')) {
            return await codeToHtml(code, { ...options, lang: 'text' })
        }
        throw error
    }
}

// Register the shiki async highlighter plugin with dual light/dark themes
// Uses CSS variables that respond to .wa-dark class on <html>
md.use(
    fromAsyncCodeToHtml(codeToHtmlWithFallback, {
        themes: {
            light: 'github-light',
            dark: 'github-dark',
        },
    })
)

// Configure DOMPurify to allow shiki's output (style attributes + CSS variables)
// and mermaid SVG output
const DOMPURIFY_CONFIG = {
    ADD_TAGS: ['svg', 'path', 'g', 'circle', 'rect', 'line', 'polyline', 'polygon',
               'text', 'tspan', 'defs', 'clipPath', 'marker', 'foreignObject',
               'use', 'symbol', 'desc', 'title', 'image', 'pattern',
               'linearGradient', 'radialGradient', 'stop', 'ellipse'],
    ADD_ATTR: ['style', 'class', 'viewBox', 'xmlns', 'fill', 'stroke', 'stroke-width',
               'd', 'transform', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r',
               'rx', 'ry', 'width', 'height', 'points', 'text-anchor', 'dominant-baseline',
               'font-size', 'font-family', 'font-weight', 'marker-end', 'marker-start',
               'clip-path', 'id', 'href', 'xlink:href', 'gradientTransform',
               'gradientUnits', 'spreadMethod', 'offset', 'stop-color', 'stop-opacity',
               'opacity', 'fill-opacity', 'stroke-opacity', 'stroke-dasharray',
               'stroke-linecap', 'stroke-linejoin', 'preserveAspectRatio',
               'aria-roledescription', 'role', 'aria-label', 'tabindex',
               'target', 'rel'],
    // Allow data: URIs for mermaid inline images
    ALLOW_DATA_ATTR: true,
}

/**
 * Render a markdown string to sanitized HTML.
 * Async because shiki highlighting is async.
 *
 * @param {string} source - Raw markdown text
 * @param {object} [env] - Optional markdown-it env. Pass `{ softBreakAsSpace: true }`
 *   to collapse soft line breaks to spaces instead of <br> (see the softbreak rule above).
 * @returns {Promise<string>} Sanitized HTML string
 */
export async function renderMarkdown(source, env) {
    if (!source) return ''

    const rawHtml = await md.renderAsync(source, env)
    return DOMPurify.sanitize(rawHtml, DOMPURIFY_CONFIG)
}

function collectSourceCodeRanges(tokens, offset = 0) {
    const ranges = []
    for (const token of tokens) {
        if (token.type === 'code_inline' && token.meta?.sourceSpan) {
            ranges.push(token.meta.sourceSpan.map(position => offset + position))
        } else if (token.type === 'image' && token.meta?.sourceLabelOffset !== undefined) {
            ranges.push(...collectSourceCodeRanges(token.children || [], offset + token.meta.sourceLabelOffset))
        }
    }
    return ranges
}

/**
 * Return syntax tokens without rendering or enabling raw HTML.
 * Inline tokens carry `meta.sourceCodeRanges`: original-source UTF-16 spans,
 * including code nested inside image labels.
 * Replaying their raw mapped source preserves CRLF and nested block prefixes.
 */
export function parseMarkdownTokens(source) {
    const tokens = md.parse(source, {})
    const offsets = [0]
    for (const match of source.matchAll(/\r\n|\r|\n/g)) offsets.push(match.index + match[0].length)
    offsets.push(source.length)
    for (const token of tokens) {
        if (token.type !== 'inline' || !token.map) continue
        const [start, end] = token.map.map(line => offsets[line])
        const children = []
        md.inline.parse(source.slice(start, end), md, { recordInlineSourceSpans: true }, children)
        token.meta = {
            ...token.meta,
            sourceCodeRanges: collectSourceCodeRanges(children, start),
        }
    }
    return tokens
}

/**
 * Split a markdown document into its top-level blocks, using markdown-it's own
 * tokenization. Each root block token carries a `.map = [startLine, endLine]`
 * line range computed by markdown-it; we slice the source accordingly. We do not
 * reimplement any markdown parsing — only collect markdown-it's block boundaries.
 *
 * The returned `env` is filled by the parse (notably `env.references` for link
 * reference definitions) and MUST be passed back to `renderBlockToHtml`, so that
 * a reference defined in one block resolves when used in another block.
 *
 * @param {string} source - Raw markdown text
 * @returns {{ blocks: Array<{src: string, hash: string}>, env: object }}
 */
export function splitMarkdownBlocks(source) {
    const env = {}
    if (!source) return { blocks: [], env }
    const tokens = md.parse(source, env)
    const lines = source.split('\n')
    const blocks = []
    for (const token of tokens) {
        // Root-level block tokens (level 0) carry a source line range in `.map`.
        // Closing tokens (paragraph_close, etc.) are level 0 too but have no map.
        if (token.level === 0 && token.map) {
            const src = lines.slice(token.map[0], token.map[1]).join('\n')
            blocks.push({ src, hash: hashString(src) })
        }
    }
    return { blocks, env }
}

/**
 * True when `source` renders to nothing visible: empty, whitespace-only, or
 * only HTML comments (which the renderer hides) — in any combination.
 *
 * Reuses the renderer's own block tokenizer, so the distinction the
 * comment-hiding rules already draw is preserved: a comment inside a fenced or
 * indented code block still counts as content (it stays rendered) and yields a
 * block, while a standalone comment yields none.
 *
 * @param {string} source - Raw markdown text
 * @returns {boolean}
 */
export function isBlankMarkdown(source) {
    if (!source || !source.trim()) return true
    return splitMarkdownBlocks(source).blocks.length === 0
}

/**
 * Extract the heading outline of a markdown document, in document order.
 *
 * A single parse pass (same mechanism as splitMarkdownBlocks) — for each
 * `heading_open` token we record its level (1-6, from the h1..h6 tag) and a
 * plain-text label built from the following inline token's text/code children,
 * so inline markup (`**bold**`, `` `code` ``, links) is flattened to text.
 *
 * No ids or slugs are generated: consumers match this outline to the rendered
 * <h1>..<h6> elements positionally (both are in source order), which sidesteps
 * the block-level render cache (each block is rendered by a separate md call,
 * so a cross-block slug counter — e.g. markdown-it-anchor — would not compose).
 *
 * @param {string} source - Raw markdown text
 * @returns {Array<{level: number, text: string}>}
 */
export function extractHeadings(source) {
    if (!source) return []
    const tokens = md.parse(source, {})
    const headings = []
    for (let i = 0; i < tokens.length; i++) {
        const token = tokens[i]
        if (token.type !== 'heading_open') continue
        const level = Number(token.tag.slice(1))
        const inline = tokens[i + 1]
        let text = ''
        if (inline?.type === 'inline' && inline.children) {
            for (const child of inline.children) {
                if (child.type === 'text' || child.type === 'code_inline') {
                    text += child.content
                }
            }
        }
        // Keep every heading (even an empty-label one) so the positional match
        // with the rendered <h*> elements stays aligned.
        headings.push({ level, text: text.trim() })
    }
    return headings
}

// One blockquote marker: its indentation, the `>` itself, and the single space
// markdown-it consumes after it.
const QUOTE_MARKER_RE = /^[ \t]*>[ \t]?/

// Remove `depth` levels of quote markers from each line. A line carrying fewer
// markers than the quote is deep (a lazy continuation) stops early instead of
// losing text.
function stripQuoteMarkers(lines, depth) {
    return lines.map((line) => {
        let text = line
        for (let level = 0; level < depth; level++) {
            const stripped = text.replace(QUOTE_MARKER_RE, '')
            if (stripped === text) break
            text = stripped
        }
        return text
    }).join('\n').trimEnd()
}

/**
 * Extract the source markdown of every blockquote of a document, in document
 * order, with the quote markers removed.
 *
 * Same mechanism as extractHeadings: one parse pass, where each
 * `blockquote_open` token carries the `.map` line range of its quote. Consumers
 * match the result to the rendered <blockquote> elements positionally — both
 * lists are in document (pre-)order, so the pairing holds at any nesting depth.
 *
 * Exactly as many markers are stripped as the quote is deep, so a nested quote
 * keeps its own markers and a `>` inside a fenced code block is never mistaken
 * for one.
 *
 * @param {string} source - Raw markdown text
 * @returns {Array<string>} One dequoted source per blockquote, in document order
 */
export function extractBlockquoteSources(source) {
    if (!source) return []
    const tokens = md.parse(source, {})
    const lines = source.split('\n')
    const sources = []
    let depth = 0
    for (const token of tokens) {
        if (token.type === 'blockquote_close') {
            depth -= 1
        } else if (token.type === 'blockquote_open') {
            depth += 1
            // A blockquote always carries a map; the guard keeps the positional
            // pairing aligned rather than dropping an entry if one ever lacks it.
            sources.push(token.map ? stripQuoteMarkers(lines.slice(token.map[0], token.map[1]), depth) : '')
        }
    }
    return sources
}

/**
 * Render a single markdown block to sanitized HTML. Reuses the shared `env`
 * (from splitMarkdownBlocks) so cross-block link references resolve. Async
 * because shiki highlighting is async (same mechanism as renderMarkdown).
 *
 * @param {string} src - One block's raw markdown
 * @param {object} env - The shared markdown-it env from splitMarkdownBlocks
 * @returns {Promise<string>} Sanitized HTML for the block
 */
export async function renderBlockToHtml(src, env) {
    const rawHtml = await md.renderAsync(src, env)
    return DOMPurify.sanitize(rawHtml, DOMPURIFY_CONFIG)
}

/**
 * Check if a string likely contains markdown formatting.
 * Used to decide whether to render as markdown or plain text.
 */
export function hasMarkdownSyntax(text) {
    if (!text) return false
    // Check for common markdown patterns
    return /(?:^|\n)#{1,6}\s|```|`[^`]+`|\*\*|__|\[.+\]\(.+\)|^\s*[-*+]\s|^\s*\d+\.\s|^\s*>\s|^\s*:{2,}\s|\|.*\|/m.test(text)
}
