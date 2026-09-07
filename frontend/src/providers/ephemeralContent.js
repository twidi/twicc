/** Local prompt summaries never contain attachment bytes or preview URLs. */
export function ephemeralPromptText(text, attachments = []) {
    const summary = attachments.map(attachment =>
        `Attachment: ${attachment.name || attachment.kind || 'file'} (${attachment.media_type || 'unknown type'})`,
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
