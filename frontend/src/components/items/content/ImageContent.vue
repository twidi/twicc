<script setup>
defineProps({
    source: {
        type: String,
        default: null
    },
    mediaType: {
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
    <div class="image-content" :class="role">
        <span class="placeholder">[image]</span>
    </div>
</template>

<style scoped>
.image-content {
    font-family: var(--wa-font-sans);
}

.placeholder {
    color: var(--wa-color-text-quiet);
    font-style: italic;
}
</style>
