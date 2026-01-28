<script setup>
import MarkdownContent from '../../MarkdownContent.vue'

defineProps({
    text: {
        type: String,
        required: true
    },
    role: {
        type: String,
        default: null
    }
})
</script>

<template>
    <div class="text-content" :class="role">
        <wa-card v-if="role === 'user'" appearance="filled-outlined">
            <MarkdownContent :source="text.trim()" />
        </wa-card>
        <MarkdownContent v-else :source="text.trim()" />
    </div>
</template>

<style scoped>
.text-content {
    word-break: break-word;
    font-family: var(--wa-font-sans);
}
.text-content.user wa-card {
    width: max-content;
    max-width: 90%;
    margin-left: auto;
    margin-top: var(--wa-space-l);
    margin-bottom: var(--wa-space-l);
}
</style>
