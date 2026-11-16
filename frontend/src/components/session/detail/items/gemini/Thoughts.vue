<script setup>
/**
 * Gemini thoughts: the collapsible reasoning of an assistant record.
 *
 * Reuses the Codex ``Reasoning`` card (same look, persisted open state,
 * streaming spinner, empty placeholder) by handing it its synthetic
 * thinking shape. Two inputs feed it:
 *
 *  - ``thoughts``: the record's ``[{ subject, description }]``, joined
 *    into markdown (bold subject, then description) — the shape a streamed
 *    thinking chunk already has (``**Subject**\ndescription``);
 *  - ``streamingData``: the store's live thinking placeholder, passed
 *    through untouched.
 */
import { computed } from 'vue'
import { thoughtsMarkdown } from '../../../../../providers/gemini/records'
import Reasoning from '../codex/Reasoning.vue'

const props = defineProps({
    thoughts: { type: Array, default: () => [] },
    streamingData: { type: Object, default: null },
    sessionId: { type: String, required: true },
    // Feeds the persisted open/closed key (``line:<lineNum>:0``), the same
    // key the store moves a streamed thinking block's open state to.
    lineNum: { type: Number, required: true },
})

const data = computed(() => props.streamingData ?? {
    message: { content: [{ type: 'thinking', thinking: thoughtsMarkdown(props.thoughts) }] },
})
</script>

<template>
    <Reasoning :data="data" :session-id="sessionId" :line-num="lineNum" />
</template>
