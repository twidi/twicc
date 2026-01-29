<script setup>
import MarkdownContent from '../../MarkdownContent.vue'

defineProps({
    text: {
        type: String,
        required: true
    }
})
</script>

<template>
    <div class="text-content">
        <MarkdownContent :source="text.trim()" />
    </div>
</template>

<style scoped>
.text-content {
    word-break: break-word;
    font-family: var(--wa-font-sans);
}
</style>
