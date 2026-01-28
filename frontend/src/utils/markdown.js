// Markdown rendering engine
// Uses markdown-it-async + shiki (syntax highlighting) + DOMPurify (XSS protection)
// Mermaid diagrams are rendered post-parse by the MarkdownContent component.

import MarkdownItAsync from 'markdown-it-async'
import { fromAsyncCodeToHtml } from '@shikijs/markdown-it/async'
import { codeToHtml } from 'shiki'
import DOMPurify from 'dompurify'

// Configure markdown-it with all features enabled
const md = MarkdownItAsync({
    html: false,         // disable raw HTML input (security)
    linkify: true,       // auto-detect URLs
    typographer: true,   // smart quotes, dashes
    breaks: true,        // convert \n to <br> (matches pre-wrap behavior)
})

// Register the shiki async highlighter plugin
md.use(
    fromAsyncCodeToHtml(codeToHtml, {
        theme: 'github-light',
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
               'aria-roledescription', 'role', 'aria-label', 'tabindex'],
    // Allow data: URIs for mermaid inline images
    ALLOW_DATA_ATTR: true,
}

/**
 * Render a markdown string to sanitized HTML.
 * Async because shiki highlighting is async.
 *
 * @param {string} source - Raw markdown text
 * @returns {Promise<string>} Sanitized HTML string
 */
export async function renderMarkdown(source) {
    if (!source) return ''

    const rawHtml = await md.renderAsync(source)
    return DOMPurify.sanitize(rawHtml, DOMPURIFY_CONFIG)
}

/**
 * Check if a string likely contains markdown formatting.
 * Used to decide whether to render as markdown or plain text.
 */
export function hasMarkdownSyntax(text) {
    if (!text) return false
    // Check for common markdown patterns
    return /(?:^|\n)#{1,6}\s|```|`[^`]+`|\*\*|__|\[.+\]\(.+\)|^\s*[-*+]\s|^\s*\d+\.\s|^\s*>\s|\|.*\|/m.test(text)
}
