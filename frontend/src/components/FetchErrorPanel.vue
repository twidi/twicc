<script setup>
defineProps({
    loading: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['retry'])

function handleRetry() {
    emit('retry')
}
</script>

<template>
    <div class="fetch-error-panel">
        <wa-callout variant="danger" appearance="filled-outlined">
            <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
            <div class="error-content">
                <div class="error-message">
                    <slot>An error occurred while loading data</slot>
                </div>
                <wa-button
                    variant="danger"
                    appearance="outlined"
                    size="small"
                    :loading="loading"
                    :disabled="loading"
                    @click="handleRetry"
                >
                    <wa-icon slot="start" name="arrow-rotate-right"></wa-icon>
                    Retry
                </wa-button>
            </div>
        </wa-callout>
    </div>
</template>

<style scoped>
.fetch-error-panel {
    display: flex;
    align-items: center;
    justify-content: center;
    flex: 1;
    min-height: 200px;
    padding: var(--wa-space-l);
}

.error-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    text-align: center;
}

.error-message {
    color: inherit;
}
</style>
