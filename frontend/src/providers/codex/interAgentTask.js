import { i18n } from '../../i18n/index.js'
/**
 * The task a parent agent hands to a subagent (Codex multi-agent v2).
 *
 * Codex models it as an inter-agent message rather than a user message,
 * but in the receiving thread it plays the role a human's prompt plays in
 * a top-level session — so the backend classifies it as a USER_MESSAGE
 * (see the Codex compute's ``compute_item_kind``) and it renders in the
 * same bubble, through the same markdown pipeline as any other message.
 *
 * The body normally travels encrypted (a Fernet token in a sibling
 * content block), leaving the task name as the only readable trace of
 * what was asked. Rather than an empty bubble or a ciphertext dump, we
 * compose the markdown the renderer would have received if Codex had
 * written it in the clear.
 *
 * The envelope's routing header (sender, agent paths) is deliberately
 * dropped: internal plumbing, with nothing in it for the reader as long
 * as subagents are shown one level at a time.
 */
// Explicit extension: this module is covered by ``node --test``, which does
// not run through Vite's resolver.
import { humanizeToolSegment } from '../../utils/toolNames.js'

const NEW_TASK_HEADER = 'Message Type: NEW_TASK'
const TASK_NAME_PREFIX = 'Task name:'
const PAYLOAD_MARKER = 'Payload:'
const HEADER_LINE_RE = /^(Message Type|Task name|Sender|Payload):/
const ENCRYPTED_NOTICE_KEY = 'session.messages.encryptedPrompt'

/** The envelope's leading plain-text block, or '' for any other shape. */
function envelopeText(parsed) {
    const content = parsed?.payload?.content
    if (parsed?.payload?.type !== 'agent_message' || !Array.isArray(content)) return ''
    const text = content[0]?.text
    return typeof text === 'string' ? text : ''
}

/**
 * Markdown body for a ``NEW_TASK`` envelope, or ``null`` when the line is
 * anything else.
 *
 * Bold task name, then the payload — the real one when it came through in
 * the clear, an italic notice when it did not. The encrypted case is
 * detected on the *block type*, never by recognising a ciphertext, so a
 * plaintext payload would simply render.
 *
 * @param {object} parsed - Parsed JSONL line.
 * @returns {string|null}
 */
export function interAgentTaskMarkdown(parsed) {
    const envelope = envelopeText(parsed)
    if (!envelope.startsWith(NEW_TASK_HEADER)) return null

    const lines = envelope.split('\n')
    const rawPath = lines.find(line => line.startsWith(TASK_NAME_PREFIX))?.slice(TASK_NAME_PREFIX.length).trim() || ''
    const segment = rawPath.split('/').filter(Boolean).pop() || ''
    const taskName = segment ? humanizeToolSegment(segment) : ''

    const payloadStart = lines.findIndex(line => line.trim() === PAYLOAD_MARKER)
    const payload = payloadStart < 0
        ? ''
        : lines.slice(payloadStart + 1).filter(line => !HEADER_LINE_RE.test(line)).join('\n').trim()

    const encrypted = (parsed?.payload?.content || []).some(block => block?.type === 'encrypted_content')

    const parts = []
    if (taskName) parts.push(`**${taskName}**`)
    if (payload) parts.push(payload)
    else if (encrypted) parts.push(`*${i18n.global.t(ENCRYPTED_NOTICE_KEY)}*`)
    return parts.join('\n\n')
}
