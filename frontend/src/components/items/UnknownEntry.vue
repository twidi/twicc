<script setup>
defineProps({
    type: {
        type: String,
        default: 'unknown'
    },
    data: {
        type: Object,
        default: null
    }
})
</script>

<template>
    <div class="unknown-entry">
        <span class="unknown-label">Unknown type</span>
        <span class="unknown-type">({{ type }})</span>
    </div>
</template>

<style scoped>
.unknown-entry {
    color: var(--wa-color-text-subtle);
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
}

.unknown-label {
    font-style: italic;
}

.unknown-type {
    margin-left: var(--wa-space-xs);
    opacity: 0.7;
}
</style>
