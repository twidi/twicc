<script setup>
import { computed } from 'vue'
import { SYNTHETIC_ITEM } from '../../../../../constants'
import { useDataStore } from '../../../../../stores/data'
import { emptyAssistantMessageMarkdown, showEmptyAssistantNotice } from '../../../../../utils/emptyMessage'
import { hybridAttachmentImageUrl, messageAttachmentLayout } from '../../../../../utils/attachmentStrip'
import { useAttachmentStripContext } from '../../../../../composables/useAttachmentStripContext'
import AttachmentStrip from '../../../../media/AttachmentStrip.vue'
import ContentList from './ContentList.vue'
import WorkingAssistantMessage from '../WorkingAssistantMessage.vue'

const props = defineProps({
    data: {
        type: Object,
        required: true
    },
    role: {
        type: String,
        required: true,
        validator: (value) => ['user', 'assistant', 'items'].includes(value)
    },
    // Context for store lookups (propagated to ContentList)
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        required: true
    },
    parentSessionId: {
        type: String,
        default: null
    },
    lineNum: {
        type: Number,
        required: true
    },
    externallyGrouped: {
        type: Boolean,
        default: false
    },
    // Group props for prefix/suffix
    groupHead: {
        type: Number,
        default: null
    },
    groupTail: {
        type: Number,
        default: null
    },
    prefixExpanded: {
        type: Boolean,
        default: false
    },
    suffixExpanded: {
        type: Boolean,
        default: false
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

const emit = defineEmits(['toggle-suffix'])

const isStartingAssistantMessage = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE.kind
)

const isWorkingAssistantMessage = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.kind
)

const contentItems = computed(() => {
    const content = props.data?.message?.content

    // If content is a string, treat it as a single text item
    if (typeof content === 'string') {
        return [{ type: 'text', text: content }]
    }

    // If content is an array, return it as-is
    if (Array.isArray(content)) {
        return content
    }

    return []
})

const dataStore = useDataStore()

// Attachments of a user message (spec 2026-10-03 §10.2): one ordered strip
// from the top-level `twicc_attachments` manifest (or the optimistic / failed
// bubble's `attachmentItems`); the native media blocks it binds are then not
// rendered on their own. A hybrid message is a plain string: its inline
// entries are chips. Without either, `strip` is null and the legacy rendering
// stays.
const { share: attachmentShareMode, openArtifact } = useAttachmentStripContext(() => props.sessionId)
// A hybrid inline image has no native block: its thumbnail is served from the
// CLI's own record of the file (not available in a share: icon tile).
const hybridImageUrl = (reference) => hybridAttachmentImageUrl({
    projectId: props.projectId,
    sessionId: props.sessionId,
    lineNum: props.lineNum,
    reference,
})
const attachmentLayout = computed(() => {
    if (props.role !== 'user') return { strip: null, hiddenIndices: [] }
    return messageAttachmentLayout(props.data, contentItems.value, {
        hybrid: typeof props.data?.message?.content === 'string',
        share: attachmentShareMode,
        imageUrl: attachmentShareMode || props.parentSessionId ? null : hybridImageUrl,
    })
})

const isStreamingBlock = computed(() =>
    props.data?.syntheticKind === SYNTHETIC_ITEM.STREAMING_BLOCK.kind
)

// An assistant message the provider wrote without any content is either
// replaced by a notice or rendered as nothing at all, never as the empty
// bubble it would otherwise paint (showEmptyAssistantNotice owns the choice).
// Blank ``text`` blocks count as no content; a ``thinking`` or ``tool_use``
// block does not, so a message is only substituted when it has nothing else to
// show. Streaming placeholders are excluded: their text is legitimately empty
// until the first delta lands.
const displayItems = computed(() => {
    if (props.role !== 'assistant' || isStreamingBlock.value) return contentItems.value
    const isEmpty = contentItems.value.every(
        item => item?.type === 'text' && !(item.text || '').trim()
    )
    if (!isEmpty) return contentItems.value
    if (!showEmptyAssistantNotice(props.isBlockStart, props.isBlockEnd)) return []
    const provider = dataStore.getSession(props.sessionId)?.provider
    return [{ type: 'text', text: emptyAssistantMessageMarkdown(provider) }]
})
</script>

<template>
    <WorkingAssistantMessage v-if="isStartingAssistantMessage" label="starting" process-state="starting" :session-id="sessionId" />
    <WorkingAssistantMessage v-else-if="isWorkingAssistantMessage" :label="data.label || null" :tools="data.tools || []" :last-started-tool-id="data.lastStartedToolId || null" :last-tool-visible="data.lastToolVisible !== false" :session-id="sessionId" />
    <template v-else>
        <AttachmentStrip
            v-if="attachmentLayout.strip?.length"
            :items="attachmentLayout.strip"
            @open-artifact="openArtifact"
        />
        <ContentList
            :items="displayItems"
            :role="role"
            :project-id="projectId"
            :session-id="sessionId"
            :parent-session-id="parentSessionId"
            :line-num="lineNum"
            :timestamp="data?.timestamp || null"
            :externally-grouped="externallyGrouped"
            :group-head="groupHead"
            :group-tail="groupTail"
            :prefix-expanded="prefixExpanded"
            :suffix-expanded="suffixExpanded"
            :hidden-indices="attachmentLayout.hiddenIndices"
            @toggle-suffix="emit('toggle-suffix')"
        />
    </template>
</template>
