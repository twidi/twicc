<script setup>
/**
 * Gemini user / assistant message.
 *
 * Two shapes feed it (same dispatch as ``codex/Message.vue``):
 *  - a real Gemini record: ``{ type: 'user', content }`` for a user
 *    message, ``{ type: 'gemini', content, thoughts?, toolCalls? }`` for an
 *    assistant message;
 *  - a synthetic placeholder injected by the store (optimistic user
 *    message, STARTING / WORKING assistant message, live streaming text or
 *    thinking block), flagged by its top-level ``syntheticKind``.
 *
 * A user record renders its attachment strip (the ``twicc_attachments``
 * manifest, the optimistic bubble's ``attachmentItems``, else its native
 * media parts) above its text, like ``codex/Message.vue``.
 *
 * An assistant record renders as a fragment — thoughts card, final text,
 * one card per finished tool call — so every part is a direct child of the
 * session item, like the other providers' parts.
 */
import { computed } from 'vue'
import { SYNTHETIC_ITEM } from '../../../../../constants'
import { useDataStore } from '../../../../../stores/data'
import { emptyAssistantMessageMarkdown, showEmptyAssistantNotice } from '../../../../../utils/emptyMessage'
import {
    assistantText,
    finishedToolCalls,
    recordThoughts,
    userMessageMediaBlocks,
    userMessageText,
} from '../../../../../providers/gemini/records'
import { messageAttachmentLayout, nativeMediaStripItems } from '../../../../../utils/attachmentStrip'
import { useAttachmentStripContext } from '../../../../../composables/useAttachmentStripContext'
import AttachmentStrip from '../../../../media/AttachmentStrip.vue'
import TextContent from '../TextContent.vue'
import WorkingAssistantMessage from '../WorkingAssistantMessage.vue'
import Thoughts from './Thoughts.vue'
import ToolCall from './ToolCall.vue'

const props = defineProps({
    data: { type: Object, required: true },
    kind: {
        type: String,
        required: true,
        validator: (value) => ['user_message', 'assistant_message'].includes(value),
    },
    sessionId: { type: String, required: true },
    lineNum: { type: Number, required: true },
    // Forwarded to the tool cards (View Agent button route).
    projectId: { type: String, default: null },
    parentSessionId: { type: String, default: null },
    // Position of this item in its conversation block — only used to decide
    // what an empty assistant message renders (see showEmptyAssistantNotice).
    isBlockStart: { type: Boolean, default: false },
    isBlockEnd: { type: Boolean, default: false },
})

const dataStore = useDataStore()

const syntheticKind = computed(() => props.data?.syntheticKind ?? null)
const isStarting = computed(() => syntheticKind.value === SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE.kind)
const isWorking = computed(() => syntheticKind.value === SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.kind)
const isStreamingBlock = computed(() => syntheticKind.value === SYNTHETIC_ITEM.STREAMING_BLOCK.kind)

// A streaming placeholder paints a single content block at index 0, in the
// store's provider-neutral shape (``message.content[0]``): ``thinking``
// blocks go to the thoughts card, ``text`` blocks to the markdown body.
const streamingBlock = computed(() => (isStreamingBlock.value ? props.data?.message?.content?.[0] ?? null : null))

const userText = computed(() => (props.kind === 'user_message' ? userMessageText(props.data) : ''))

// The user bubble's attachment strip: the manifest strip (or the optimistic /
// failed bubble's), else one tile per native media part. Gemini attachments
// are delivered as files, so a manifest has no inline entry to bind.
const { share: attachmentShareMode, openArtifact } = useAttachmentStripContext(() => props.sessionId)
const userAttachments = computed(() => {
    if (props.kind !== 'user_message') return []
    const blocks = userMessageMediaBlocks(props.data)
    return messageAttachmentLayout(props.data, blocks, { share: attachmentShareMode }).strip
        ?? nativeMediaStripItems(blocks)
})
const thoughts = computed(() => (props.kind === 'assistant_message' && !syntheticKind.value ? recordThoughts(props.data) : []))
const toolCalls = computed(() => (props.kind === 'assistant_message' && !syntheticKind.value ? finishedToolCalls(props.data) : []))

// The record's final text. An assistant record with nothing to show at all
// (no text, no thoughts, no tool call) gets the shared empty-message notice,
// or nothing (``null``) when the block already shows something else.
const text = computed(() => {
    if (streamingBlock.value) return streamingBlock.value.text || ''
    const value = assistantText(props.data)
    if (value.trim() || thoughts.value.length || toolCalls.value.length) return value
    if (!showEmptyAssistantNotice(props.isBlockStart, props.isBlockEnd)) return null
    return emptyAssistantMessageMarkdown(dataStore.getSession(props.sessionId)?.provider)
})
</script>

<template>
    <!-- eslint-disable @intlify/vue-i18n/no-raw-text -- "starting" is an internal status token; WorkingAssistantMessage translates the complete provider message. -->
    <WorkingAssistantMessage
        v-if="isStarting"
        label="starting"
        process-state="starting"
        :session-id="sessionId"
    />
    <!-- eslint-enable @intlify/vue-i18n/no-raw-text -->
    <WorkingAssistantMessage
        v-else-if="isWorking"
        :label="data.label || null"
        :label-ui-message="data.labelUiMessage || null"
        :session-id="sessionId"
    />
    <Thoughts
        v-else-if="streamingBlock?.type === 'thinking'"
        :streaming-data="data"
        :session-id="sessionId"
        :line-num="lineNum"
    />
    <template v-else-if="kind === 'user_message'">
        <AttachmentStrip v-if="userAttachments.length" :items="userAttachments" @open-artifact="openArtifact" />
        <TextContent v-if="userText.trim() || !userAttachments.length" :text="userText" role="user" />
    </template>
    <template v-else-if="kind === 'assistant_message'">
        <Thoughts v-if="thoughts.length" :thoughts="thoughts" :session-id="sessionId" :line-num="lineNum" />
        <TextContent v-if="text !== null && (text.trim() || streamingBlock)" :text="text" role="assistant" />
        <ToolCall
            v-for="(call, index) in toolCalls"
            :key="call.id || index"
            :call="call"
            :index="index"
            :session-id="sessionId"
            :line-num="lineNum"
            :project-id="projectId"
            :parent-session-id="parentSessionId"
        />
    </template>
</template>
