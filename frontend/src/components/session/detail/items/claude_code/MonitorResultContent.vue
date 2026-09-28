<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

const props = defineProps({
    aggregatedOutput: {
        type: String,
        required: true,
    },
    isTerminated: {
        type: Boolean,
        required: true,
    },
})

const markdownSource = computed(() => {
    if (props.aggregatedOutput) {
        return '```\n' + props.aggregatedOutput + '\n```'
    }
    if (!props.isTerminated) {
        return '```\nWaiting for monitor events…\n```'
    }
    return null
})

// No markdown raw-toggle / copy toolbar: it belongs to messages, thinking and
// reasoning; a tool result has the code block's own wrap / copy buttons.
</script>

<template>
    <MarkdownContent v-if="markdownSource" :source="markdownSource" :show-toolbar="false" />
    <div v-else class="monitor-output__no-events">(no events)</div>
</template>

<style scoped>
.monitor-output__no-events {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}
</style>
