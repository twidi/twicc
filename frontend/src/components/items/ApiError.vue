<script setup>
import { computed } from 'vue'

const props = defineProps({
    data: {
        type: Object,
        required: true
    }
})

const errorInfo = computed(() => {
    const error = props.data?.error?.error?.error
    return {
        type: error?.type || 'unknown_error',
        message: error?.message || 'Unknown error',
        status: props.data?.error?.status,
        retryAttempt: props.data?.retryAttempt,
        maxRetries: props.data?.maxRetries
    }
})
</script>

<template>
    <div class="api-error">
        <div class="error-header">API Error</div>
        <div class="error-body">
            <div class="error-type">{{ errorInfo.type }}</div>
            <div class="error-message">{{ errorInfo.message }}</div>
            <div v-if="errorInfo.status" class="error-detail">Status: {{ errorInfo.status }}</div>
            <div v-if="errorInfo.retryAttempt" class="error-detail">
                Retry {{ errorInfo.retryAttempt }}/{{ errorInfo.maxRetries }}
            </div>
        </div>
    </div>
</template>

<style scoped>
.api-error {
    font-family: var(--wa-font-sans);
}

.error-header {
    font-weight: 600;
    color: var(--wa-color-danger);
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.error-type {
    font-weight: 600;
    color: var(--wa-color-danger);
}

.error-detail {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-subtle);
}
</style>
