/** Standalone publication grammar. All returned offsets count Unicode code points. */
import { parseMarkdownTokens } from '../utils/markdown.js'

const TAG = /^<twicc:inline-artifact(?=\s|\/>)([^]*?)\/>$/
const COMPLETE_TAG = /^<twicc:inline-artifact(?=\s|\/>)(?:[^"<>]|"[^"]*")*\/>$/
const ATTRIBUTE = /\s+([A-Za-z_:][A-Za-z0-9_:.-]*)\s*=\s*"([^"]*)"/y

function lineOffsets(text) {
    const offsets = [0]
    for (const match of text.matchAll(/\r\n|\r|\n/g)) offsets.push(match.index + match[0].length)
    offsets.push(text.length)
    return offsets
}

function isEscaped(text, position) {
    let preceding = position - 1
    while (preceding >= 0 && text[preceding] === '\\') preceding--
    return (position - preceding - 1) % 2 === 1
}

/** Comments can cross Markdown paragraphs. Markdown maps keep code excluded. */
function commentRanges(text, tokens, offsets) {
    const codeRanges = []
    for (const token of tokens) {
        if (!token.map) continue
        const [start, end] = token.map.map(line => offsets[line])
        if (token.type === 'fence' || token.type === 'code_block') {
            codeRanges.push([start, end])
        } else if (token.type === 'inline') {
            if (COMPLETE_TAG.test(text.slice(start, end).trim())) {
                codeRanges.push([start, end])
                continue
            }
            codeRanges.push(...(token.meta?.sourceCodeRanges || []))
        }
    }
    codeRanges.sort((a, b) => a[0] - b[0])
    const ranges = []
    let pos = 0
    let codeIndex = 0
    while (pos < text.length) {
        while (codeIndex < codeRanges.length && codeRanges[codeIndex][1] <= pos) codeIndex++
        if (codeIndex < codeRanges.length && codeRanges[codeIndex][0] <= pos) {
            pos = codeRanges[codeIndex][1]
            continue
        }
        if (text.startsWith('<!--', pos) && !isEscaped(text, pos)) {
            const close = text.indexOf('-->', pos + 4)
            const end = close < 0 ? text.length : close + 3
            ranges.push([pos, end])
            pos = end
        } else {
            pos++
        }
    }
    return ranges
}

function descriptor(source) {
    const match = TAG.exec(source)
    if (!match) return [null, 'invalid_attributes']
    const attributes = new Map()
    const content = match[1]
    let pos = 0
    while (pos < content.length) {
        if (!content.slice(pos).trim()) break
        ATTRIBUTE.lastIndex = pos
        const attribute = ATTRIBUTE.exec(content)
        if (!attribute || attributes.has(attribute[1])) return [null, 'invalid_attributes']
        attributes.set(attribute[1], attribute[2])
        pos = ATTRIBUTE.lastIndex
    }
    if (!attributes.has('id')) return [null, 'missing_id']
    if (!attributes.has('src')) return [null, 'missing_src']
    const artifactId = attributes.get('id')
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(artifactId)) return [null, 'invalid_id']
    const src = attributes.get('src')
    const parts = src.split('/')
    if (parts.length !== 3 || parts[0] !== 'inline-artifacts' || parts[1] !== artifactId
        || /[\\?#\x00]/.test(src) || !/^.+\.(?:html|htm)$/.test(parts[2])
        || parts[2] === '.' || parts[2] === '..') return [null, 'invalid_src']
    const title = attributes.has('title') ? attributes.get('title') : artifactId
    if (Array.from(title).length > 200) return [null, 'invalid_title']
    const rawHeight = attributes.has('height') ? attributes.get('height') : '360'
    if (!/^[+-]?[0-9]+$/.test(rawHeight)) return [null, 'invalid_height']
    const height = Math.max(160, Math.min(900, Number(rawHeight)))
    return [{ artifact_id: artifactId, src, title, height }, null]
}

export function parseInlineArtifactBlocks(text) {
    const tokens = parseMarkdownTokens(text)
    const offsets = lineOffsets(text)
    const comments = commentRanges(text, tokens, offsets)
    const blocks = []
    for (const token of tokens) {
        if (token.type !== 'paragraph_open' || token.level !== 0 || !token.map) continue
        const [startLine, endLine] = token.map
        let [start, end] = token.map.map(line => offsets[line])
        if (startLine && text.slice(offsets[startLine - 1], start).trim()) continue
        if (endLine < offsets.length - 1 && text.slice(end, offsets[endLine + 1]).trim()) continue
        const raw = text.slice(start, end)
        const source = raw.trim()
        if (!/^<twicc:inline-artifact(?=\s|\/>)/.test(source) || !COMPLETE_TAG.test(source)) continue
        start += raw.length - raw.trimStart().length
        end = start + source.length
        if (comments.some(([left, right]) => left <= start && start < right)) continue
        const [parsed, error] = descriptor(source)
        blocks.push({
            start: Array.from(text.slice(0, start)).length,
            end: Array.from(text.slice(0, end)).length,
            descriptor: parsed,
            error,
        })
    }
    return blocks
}

export function publicationKey(sessionId, publication) {
    return JSON.stringify([sessionId, publication.line_num, publication.text_block_index, publication.tag_offset])
}
