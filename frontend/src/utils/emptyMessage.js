import { i18n } from '../i18n/index.js'
import { getProviderLabel } from '../providers'

/**
 * Markdown rendered in place of an assistant message the provider persisted
 * with no content at all (Codex writes an ``event_msg.agent_message`` whose
 * ``message`` is an empty string; Claude can leave the content array empty).
 * Without it the transcript shows an empty bubble, which reads as a TwiCC bug.
 *
 * Italic, so it never passes for something the agent wrote. Falls back to the
 * generic ``Agent`` label when the provider is unknown (see getProviderLabel).
 *
 * @param {string|null} provider - Session provider (PROVIDER.* value).
 * @returns {string} Markdown source for the replacement text.
 */
export function emptyAssistantMessageMarkdown(provider) {
    return i18n.global.t('session.messages.emptyAssistant', { provider: getProviderLabel(provider) })
}

/**
 * Whether an assistant message with no content must be replaced by the notice
 * above, or dropped entirely (rendered as nothing).
 *
 * The notice only earns its place when the empty message is the whole block:
 * the assistant turn would otherwise render nothing at all between two user
 * messages. As soon as the block displays anything else — an earlier text, a
 * tool use, a group toggle — that context already shows the turn happened, so
 * narrating the emptiness is noise (a common shape in orchestration: the agent
 * messages another session, then closes its turn with an empty message).
 *
 * Both flags come from the visual-item block flags (see the store's
 * ``recomputeVisualItems``), so they reflect what is actually displayed in the
 * current display mode, not the raw JSONL.
 *
 * @param {boolean} isBlockStart - Nothing is displayed before it in its block.
 * @param {boolean} isBlockEnd - Nothing is displayed after it in its block.
 * @returns {boolean} true to render the notice, false to render nothing.
 */
export function showEmptyAssistantNotice(isBlockStart, isBlockEnd) {
    return isBlockStart && isBlockEnd
}
