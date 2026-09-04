<script setup>
import { computed } from 'vue'
import { SYNTHETIC_ITEM } from '../../../../../constants'
import { useDataStore } from '../../../../../stores/data'
import { emptyAssistantMessageMarkdown, showEmptyAssistantNotice } from '../../../../../utils/emptyMessage'
import { interAgentTaskMarkdown } from '../../../../../providers/codex/interAgentTask'
import {
    agentMessageText,
    userMessageImages,
    userMessageText,
} from '../../../../../providers/codex/canonical'
import UserMessage from './UserMessage.vue'
import AssistantMessage from './AssistantMessage.vue'
import Reasoning from './Reasoning.vue'
import WorkingAssistantMessage from '../WorkingAssistantMessage.vue'

const props = defineProps({
    // Parsed JSONL line. Two shapes are supported:
    //  - Real Codex line: ``{ timestamp, type: 'event_msg', payload: { type:
    //    'user_message' | 'agent_message', message: string, ... } }``
    //  - Synthetic placeholder injected by the store (optimistic user
    //    message, or STARTING / WORKING assistant message, or live
    //    streaming text/thinking block). These carry ``syntheticKind``
    //    at the top level and rely on the dispatch below.
    data: {
        type: Object,
        required: true
    },
    // ItemKind value driving the user/assistant dispatch.
    kind: {
        type: String,
        required: true,
        validator: (value) => ['user_message', 'assistant_message'].includes(value)
    },
    // Forwarded to ``WorkingAssistantMessage`` so it can derive the provider
    // label and the session's base directory for tool summaries.
    sessionId: {
        type: String,
        required: true
    },
    // Used by the streaming ``Reasoning`` placeholder to build a stable
    // persisted open/closed key on the synthetic negative line number.
    lineNum: {
        type: Number,
        required: true
    },
    // Position of this item in its conversation block (mirrors the
    // `.is-block-start` / `.is-block-end` CSS classes). Only used to decide
    // what an empty assistant message renders — see showEmptyAssistantNotice.
    isBlockStart: {
        type: Boolean,
        default: false
    },
    isBlockEnd: {
        type: Boolean,
        default: false
    }
})

const isStartingAssistantMessage = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE.kind
)

const isWorkingAssistantMessage = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.kind
)

const isStreamingBlock = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.STREAMING_BLOCK.kind
)

// A streaming placeholder paints a single content block at index 0;
// inspect its ``type`` to route ``thinking`` blocks to ``Reasoning`` and
// regular ``text`` blocks to ``AssistantMessage`` (same dispatch Claude
// does inside its ``ContentList``).
const streamingBlockType = computed(() => {
    if (!isStreamingBlock.value) return null
    return props.data?.message?.content?.[0]?.type ?? null
})

// Plain text source for the ``AssistantMessage`` / ``UserMessage`` path.
// Two shapes feed in:
//
//  - Real Codex line: a canonical ``UserMessage`` / ``AgentMessage``
//    completed item whose text entries are joined by ``canonical.js``.
//  - Streaming text placeholder: the store paints the live SDK delta into
//    a Claude-style content array under ``message.content`` so the same
//    rendering plumbing can carry both providers.
const text = computed(() => {
    if (isStreamingBlock.value) {
        const content = props.data?.message?.content
        if (Array.isArray(content) && content.length > 0) {
            return content[0].text || ''
        }
        return ''
    }
    return props.kind === 'user_message'
        ? (userMessageText(props.data) || '')
        : (agentMessageText(props.data) || '')
})

const dataStore = useDataStore()

// Codex sometimes ends a turn with an ``agent_message`` carrying an empty
// ``message``. The line is a real ASSISTANT_MESSAGE, so it gets a bubble —
// empty, which reads as a display bug. Show a replacement text instead, or
// nothing at all (``null``, dropping the bubble) when the block already
// displays something else — showEmptyAssistantNotice owns that choice.
// Streaming placeholders are excluded: their text is legitimately empty
// until the first delta lands.
const assistantText = computed(() => {
    if (isStreamingBlock.value || text.value.trim()) return text.value
    if (!showEmptyAssistantNotice(props.isBlockStart, props.isBlockEnd)) return null
    return emptyAssistantMessageMarkdown(dataStore.getSession(props.sessionId)?.provider)
})

// A subagent's opening prompt (Codex multi-agent v2) is a ``NEW_TASK``
// inter-agent message, not an canonical ``UserMessage`` item: the text sits in
// a content-block array and the payload itself is usually encrypted. The
// backend classifies it as a USER_MESSAGE (it IS what this thread was asked
// to do), so it lands in the user bubble; the helper composes the markdown
// body the renderer would have received had Codex written it in the clear.
const interAgentTask = computed(() =>
    props.kind === 'user_message' ? interAgentTaskMarkdown(props.data) : null
)

// Attached images on a user_message line. TwiCC sends them as ``image``
// content entries carrying full ``data:image/...;base64,...`` URLs, in
// source order. ``local_image`` entries (a path on the agent's machine,
// written by the native CLI) have no URL the browser could load, so they
// only count as attachments (see ``userMessageAttachmentCount``) and are
// not rendered here.
const images = computed(() => {
    if (props.kind !== 'user_message') return []
    return userMessageImages(props.data)
        .filter(image => image.type === 'image')
        .map(image => image.value)
})
</script>

<template>
    <WorkingAssistantMessage
        v-if="isStartingAssistantMessage"
        label="starting"
        process-state="starting"
        :session-id="sessionId"
    />
    <WorkingAssistantMessage
        v-else-if="isWorkingAssistantMessage"
        :label="data.label || null"
        :session-id="sessionId"
    />
    <Reasoning
        v-else-if="streamingBlockType === 'thinking'"
        :data="data"
        :session-id="sessionId"
        :line-num="lineNum"
    />
    <UserMessage v-else-if="kind === 'user_message'" :text="interAgentTask ?? text" :images="images" />
    <AssistantMessage
        v-else-if="kind === 'assistant_message' && assistantText !== null"
        :text="assistantText"
        :session-id="sessionId"
        :line-num="lineNum"
    />
</template>
