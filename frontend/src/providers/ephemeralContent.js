import { i18n } from '../i18n/index.js'

/** Local prompt summaries never contain attachment bytes or preview URLs. */
export function ephemeralPromptText(text, attachments = []) {
    const summary = attachments.map(attachment =>
        i18n.global.t('session.messages.attachmentSummary', { name: attachment.name_key === 'session.messages.attachmentFile' ? i18n.global.t('session.messages.attachmentFile') : (attachment.name || attachment.kind || i18n.global.t('session.messages.attachmentFile')), type: attachment.media_type || i18n.global.t('session.messages.attachmentUnknownType') }),
    ).join('\n')
    return [text, summary].filter(Boolean).join('\n\n')
}

/** Native assistant envelopes consumed by the existing message renderers. */
export function buildEphemeralResultContent(provider, text) {
    if (provider === 'codex') {
        return {
            type: 'event_msg',
            payload: {
                type: 'item_completed',
                item: { type: 'AgentMessage', id: 'twicc-ephemeral-result', content: [{ type: 'Text', text }] },
            },
        }
    }
    return { type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text }] } }
}
