<script setup>
defineProps({
    source: {
        type: Object,
        default: null
    },
    title: {
        type: String,
        default: null
    },
    role: {
        type: String,
        default: null
    }
})
</script>

<template>
    <div class="document-content" :class="role">
        <span class="placeholder">[document]</span>
    </div>
</template>

<style scoped>
.document-content {
    font-family: var(--wa-font-sans);
}

.placeholder {
    color: var(--wa-color-text-quiet);
    font-style: italic;
}
</style>
