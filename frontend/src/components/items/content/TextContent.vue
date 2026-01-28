<script setup>
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
    <div class="text-content" :class="role">{{ text }}</div>
</template>

<style scoped>
.text-content {
    white-space: pre-wrap;
    word-break: break-word;
    font-family: var(--wa-font-sans);
}
</style>
