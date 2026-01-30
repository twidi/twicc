<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../MarkdownContent.vue'
import { commandToText } from '../../../utils/command'

const props = defineProps({
    text: {
        type: String,
        required: true
    }
})

const displayText = computed(() => {
    const trimmed = props.text.trim()
    return commandToText(trimmed) ?? trimmed
})
</script>

<template>
    <div class="text-content">
        <MarkdownContent :source="displayText" />
    </div>
</template>

<style scoped>
.text-content {
    word-break: break-word;
    font-family: var(--wa-font-sans);
}
</style>
