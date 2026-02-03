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
        <wa-callout variant="danger" appearance="outlined" size="small">
            <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
            <div class="error-content">
                <div class="error-message">Error from Claude: {{ errorInfo.message }}</div>
                <div v-if="errorInfo.status || errorInfo.retryAttempt" class="error-details">
                    <span v-if="errorInfo.status">Status: {{ errorInfo.status }}</span>
                    <span v-if="errorInfo.status && errorInfo.retryAttempt" class="separator">•</span>
                    <span v-if="errorInfo.retryAttempt">
                        Retry {{ errorInfo.retryAttempt }}/{{ errorInfo.maxRetries }}
                    </span>
                </div>
            </div>
        </wa-callout>
    </div>
</template>

<style scoped>
.api-error {
    padding-top: var(--wa-space-l);
}

.error-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

.error-message {
    color: inherit;
    font-weight: bold;
}

.error-details {
    font-size: var(--wa-font-size-xs);
    opacity: 0.8;
}

.separator {
    margin: 0 var(--wa-space-2xs);
}
</style>
