import { completedItem } from './canonical.js'

function validId(id) {
    return typeof id === 'string' && id.length > 0
}

export function matchesStreamingBlock(parsed, itemKind, messageId, block) {
    if (!validId(messageId) || !block) return false

    let itemId
    if (itemKind === 'assistant_message' && block.blockType === 'text') {
        const item = completedItem(parsed)
        if (item?.type !== 'AgentMessage') return false
        itemId = item.id
    } else if (itemKind === 'reasoning' && block.blockType === 'thinking') {
        if (parsed?.type !== 'response_item' || parsed.payload?.type !== 'reasoning') return false
        itemId = parsed.payload.id
    } else {
        return false
    }

    return validId(itemId) && itemId === messageId &&
        (block.uuid == null || (validId(block.uuid) && block.uuid === itemId))
}
