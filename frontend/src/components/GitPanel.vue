<script setup>
defineProps({
    active: {
        type: Boolean,
        default: false,
    },
})
</script>

<template>
    <div class="git-panel">
        <div class="panel-placeholder">Git (coming soon)</div>
    </div>
</template>

<style scoped>
.git-panel {
    height: 100%;
}

.panel-placeholder {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
