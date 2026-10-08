// Detection/split of Codex Plan-mode ``<proposed_plan>`` blocks.
//
// Plan collaboration mode wraps its final plan in literal
// ``<proposed_plan>`` / ``</proposed_plan>`` tags so clients can render it
// specially. The mode's built-in instructions make the shape a stable
// contract: exact tags (never translated), each on its own line, markdown
// inside, at most one block per turn, possibly surrounded by ordinary
// assistant text.
//
// Shared by the transcript rendering (``codex/AssistantMessage.vue``) and
// the post-plan "implement in a new session" action
// (``codex/PlanImplementationBody.vue``), which re-reads the latest plan off
// the session items.

import { parseMarkdownTokens } from '../../utils/markdown.js'

const OPEN_TAG_RE = /^[ \t]*<proposed_plan>[ \t]*$/
const CLOSE_TAG_RE = /^[ \t]*<\/proposed_plan>[ \t]*$/

/**
 * Split an assistant message around its ``<proposed_plan>`` block.
 *
 * The closing tag is optional so a streaming placeholder already renders the
 * plan while its text is still growing (the block always ends the message in
 * that case). A tag mentioned inline (not on its own line) does not trigger.
 *
 * @param {string} text - The assistant message text.
 * @returns {{before: string, plan: string, after: string, beforeOffset: number, planOffset: number, afterOffset: number} | null} The trimmed
 *   segments, or ``null`` when the message carries no plan block.
 */
export function splitProposedPlan(text) {
    if (typeof text !== 'string' || !text || !text.includes('<proposed_plan>')) return null
    const lines = [...text.matchAll(/[^\r\n]*(?:\r\n|\r|\n|$)/g)].filter(match => match[0])
    const tokens = parseMarkdownTokens(text)
    const eligibleLine = index => tokens.some(token => token.type === 'paragraph_open' && token.level === 0
        && token.map?.[0] <= index && index < token.map[1])
    const codeRanges = tokens.flatMap(token => {
        if (token.type === 'fence' || token.type === 'code_block') {
            return [[lines[token.map[0]].index, lines[token.map[1]]?.index ?? text.length]]
        }
        return token.meta?.sourceCodeRanges || []
    })
    const comments = []
    let cursor = 0
    while (cursor < text.length) {
        const code = codeRanges.find(([start, end]) => start <= cursor && cursor < end)
        if (code) { cursor = code[1]; continue }
        if (text.startsWith('<!--', cursor)) {
            let preceding = cursor - 1
            while (preceding >= 0 && text[preceding] === '\\') preceding--
            if ((cursor - preceding - 1) % 2 === 0) {
                const close = text.indexOf('-->', cursor + 4)
                const end = close < 0 ? text.length : close + 3
                comments.push([cursor, end])
                cursor = end
                continue
            }
        }
        cursor++
    }
    const excludedRanges = [...codeRanges, ...comments]
    const outsideExample = position => !excludedRanges.some(([start, end]) => start <= position && position < end)
    const openLine = lines.findIndex((line, index) => OPEN_TAG_RE.test(line[0].replace(/[\r\n]+$/, ''))
        && eligibleLine(index) && outsideExample(line.index))
    if (openLine < 0) return null
    const planStart = lines[openLine].index + lines[openLine][0].length
    const closeLine = lines.findIndex((line, index) => index > openLine
        && CLOSE_TAG_RE.test(line[0].replace(/[\r\n]+$/, '')) && eligibleLine(index) && outsideExample(line.index))
    const planEnd = closeLine < 0 ? text.length : lines[closeLine].index
    const afterStart = closeLine < 0 ? text.length : lines[closeLine].index + lines[closeLine][0].length
    const segment = (start, end) => {
        const raw = text.slice(start, end)
        const trimmedStart = start + raw.length - raw.trimStart().length
        return { text: raw.trim(), offset: Array.from(text.slice(0, trimmedStart)).length }
    }
    const before = segment(0, lines[openLine].index)
    const plan = segment(planStart, planEnd)
    const after = segment(afterStart, text.length)
    return { before: before.text, plan: plan.text, after: after.text,
        beforeOffset: before.offset, planOffset: plan.offset, afterOffset: after.offset }
}
