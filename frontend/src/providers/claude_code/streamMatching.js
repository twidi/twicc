export function matchesStreamingBlock(parsed, itemKind, messageId, block) {
    if (itemKind !== 'assistant_message' && itemKind !== 'content_items' && itemKind !== 'reasoning') return false
    return parsed?.message?.id === messageId && !!parsed?.uuid && block?.uuid === parsed.uuid
}
