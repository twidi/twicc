<script setup>
/**
 * The ``tool_use`` item of a Gemini session: an assistant record without
 * final text, with finished tool calls (and possibly thoughts). Renders the
 * thoughts card, then one tool card per finished call — a fragment, so each
 * card is a direct child of the session item like the other providers'
 * cards (the joined-run styling of SessionItem.vue relies on it).
 */
import { computed } from 'vue'
import { finishedToolCalls, recordThoughts } from '../../../../../providers/gemini/records'
import Thoughts from './Thoughts.vue'
import ToolCall from './ToolCall.vue'

const props = defineProps({
    data: { type: Object, required: true },
    sessionId: { type: String, required: true },
    lineNum: { type: Number, required: true },
    projectId: { type: String, default: null },
    parentSessionId: { type: String, default: null },
})

const thoughts = computed(() => recordThoughts(props.data))
const calls = computed(() => finishedToolCalls(props.data))
</script>

<template>
    <Thoughts v-if="thoughts.length" :thoughts="thoughts" :session-id="sessionId" :line-num="lineNum" />
    <ToolCall
        v-for="(call, index) in calls"
        :key="call.id || index"
        :call="call"
        :index="index"
        :session-id="sessionId"
        :line-num="lineNum"
        :project-id="projectId"
        :parent-session-id="parentSessionId"
    />
</template>
