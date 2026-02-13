<script setup>
import { computed } from 'vue'
import { SYNTHETIC_ITEM } from '../../constants'
import ContentList from './content/ContentList.vue'
import WorkingAssistantMessage from './WorkingAssistantMessage.vue'

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
    }
})

const emit = defineEmits(['toggle-suffix'])

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
</script>

<template>
    <WorkingAssistantMessage v-if="isWorkingAssistantMessage" :tool-use="data.toolUse || null" />
    <ContentList
        v-else
        :items="contentItems"
        :role="role"
        :project-id="projectId"
        :session-id="sessionId"
        :parent-session-id="parentSessionId"
        :line-num="lineNum"
        :group-head="groupHead"
        :group-tail="groupTail"
        :prefix-expanded="prefixExpanded"
        :suffix-expanded="suffixExpanded"
        @toggle-suffix="emit('toggle-suffix')"
    />
</template>
