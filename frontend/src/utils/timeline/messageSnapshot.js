import MarkdownIt from 'markdown-it'

// Parse only. The collapsed excerpt never renders HTML or loads the Markdown renderer's store dependencies.
const markdown = new MarkdownIt({ html: false })

function plainText(tokens) {
    return tokens.map(token => {
        if (token.children) return plainText(token.children)
        if (['text', 'code_inline', 'code_block', 'fence'].includes(token.type)) return token.content
        if (token.type === 'softbreak' || token.type === 'hardbreak' || (token.block && token.nesting === -1)) return '\n'
        return ''
    }).join('')
}

/** The events whose `data.text` is shown like a message, with the role of that text. */
const TEXT_ROLES = {
    'user_message.sent': 'user',
    'assistant_message.sent': 'assistant',
    // The instructions of a /compact, and the summary a compaction wrote.
    'compaction.requested': 'user',
    'compaction.completed': 'assistant',
}

/** Whether the events of this kind show their `data.text` like a message. */
export function showsMessageText(kind) {
    return Object.hasOwn(TEXT_ROLES, kind)
}

/** Describe only saved message events. The kind identifies the role; the event origin does not. */
export function describeMessageSnapshot(kind, data) {
    const role = Object.hasOwn(TEXT_ROLES, kind) ? TEXT_ROLES[kind] : null
    const text = role && data && typeof data === 'object' && !Array.isArray(data) && typeof data.text === 'string' ? data.text : ''
    const expandable = text.trim().length > 0
    const lines = expandable ? plainText(markdown.parse(text, {})).split(/\n+/).map(line => line.replace(/\s+/g, ' ').trim()).filter(Boolean) : []
    const hasMoreLines = lines.length > 1
    const excerpt = (lines[0] ?? '') + (hasMoreLines ? '…' : '')
    return { role, text, excerpt, expandable, hasMoreLines }
}
