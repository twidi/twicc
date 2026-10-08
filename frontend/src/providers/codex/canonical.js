export function completedItem(data) {
    if (data?.type !== 'event_msg' || data.payload?.type !== 'item_completed') return null
    return data.payload.item && typeof data.payload.item === 'object' ? data.payload.item : null
}

function itemOfType(data, type) {
    const item = completedItem(data)
    return item?.type === type ? item : null
}

function contentOfType(data, itemType) {
    const content = itemOfType(data, itemType)?.content
    return Array.isArray(content) ? content : []
}

/**
 * The content entries of a canonical ``UserMessage`` item, in order (text,
 * ``image``, ``local_image``). The attachment strip binds its inline entries
 * to the leading media entries (spec 2026-10-03 §10.2).
 */
export function userMessageContent(data) {
    return contentOfType(data, 'UserMessage')
}

export function userMessageText(data) {
    const text = contentOfType(data, 'UserMessage')
        .filter(entry => entry?.type === 'text' && typeof entry.text === 'string')
        .map(entry => entry.text)
        .join('')
    return text || null
}

export function userMessageImages(data) {
    return contentOfType(data, 'UserMessage').flatMap(entry => {
        if (entry?.type === 'image' && typeof entry.image_url === 'string') {
            return [{ type: 'image', value: entry.image_url }]
        }
        if (entry?.type === 'local_image' && typeof entry.path === 'string') {
            return [{ type: 'local_image', value: entry.path }]
        }
        return []
    })
}

export function userMessageAttachmentCount(data) {
    return userMessageImages(data).length
}

/** Original canonical Text entries, with content-array identities preserved. */
export function assistantTextBlocks(data) {
    return contentOfType(data, 'AgentMessage').flatMap((entry, textBlockIndex) =>
        entry?.type === 'Text' && typeof entry.text === 'string' ? [{ textBlockIndex, text: entry.text }] : [])
}

export function agentMessageText(data) {
    const text = contentOfType(data, 'AgentMessage')
        .filter(entry => entry?.type === 'Text' && typeof entry.text === 'string')
        .map(entry => entry.text)
        .join('')
    return text || null
}

export function fileChangeItem(data) {
    return itemOfType(data, 'FileChange')
}

export function mcpToolCallItem(data) {
    return itemOfType(data, 'McpToolCall')
}

export function imageGeneration(data) {
    const item = completedItem(data)
    if (item?.type === 'ImageGeneration') {
        return {
            id: item.id,
            status: item.status,
            revisedPrompt: item.revised_prompt ?? null,
            result: item.result ?? '',
            savedPath: item.saved_path ?? null,
            transparentBackground: null,
            failure: null,
        }
    }
    if (item?.type === 'Extension' && item.kind === 'image_gen.generation') {
        return {
            id: item.id,
            status: item.status,
            revisedPrompt: item.revisedPrompt ?? null,
            result: item.result ?? '',
            savedPath: item.savedPath ?? null,
            transparentBackground: item.transparentBackground ?? null,
            failure: item.failure ?? null,
        }
    }
    return null
}

export function buildOptimisticUserMessage(text, attachments = []) {
    const content = []
    if (text) content.push({ type: 'text', text, text_elements: [] })
    content.push(...attachments)
    return {
        type: 'event_msg',
        payload: {
            type: 'item_completed',
            item: {
                type: 'UserMessage',
                id: 'twicc-optimistic',
                content,
            },
        },
    }
}
