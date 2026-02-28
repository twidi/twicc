<script setup>
import MarkdownContent from '../../MarkdownContent.vue'

defineProps({
    thinking: {
        type: String,
        required: true
    }
})
</script>

<template>
    <wa-details class="item-details thinking-content" icon-placement="start">
        <span slot="summary" class="items-details-summary">
            <strong class="items-details-summary-name">Thinking</strong>
        </span>
        <div class="thinking-body">
            <MarkdownContent :source="thinking" />
        </div>
    </wa-details>
</template>

<style scoped>
wa-details::part(content) {
    padding-top: 0;
}

.thinking-body {
    padding: var(--wa-space-xs) 0;
    word-break: break-word;
}
</style>
