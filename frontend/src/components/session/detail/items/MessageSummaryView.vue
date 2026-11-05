<script setup>
// Chat rendering of a message summary: the summary text replaces the original
// text, under its controls. The text goes through TextContent, as a direct child
// of the session item: the chat styles the assistant text (toolbar position
// included) through this exact structure.
import MessageSummaryControls from './MessageSummaryControls.vue'
import TextContent from './TextContent.vue'

defineProps({ summary: { type: Object, required: true } })
</script>

<template>
    <MessageSummaryControls :summary="summary" />
    <TextContent v-if="summary.view === 'summary' && summary.entry?.text" :text="summary.entry.text" role="assistant" summary-active />
</template>
