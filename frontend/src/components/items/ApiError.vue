<script setup>
import { computed } from 'vue'

const props = defineProps({
    data: {
        type: Object,
        required: true
    }
})

/**
 * Extract error info from the "bastard" API error format.
 *
 * This format has isApiErrorMessage=true but type="assistant".
 * The error is serialized in content[0].text as: "API Error: 500 {json...}"
 *
 * @param {Object} data - The parsed JSON data
 * @returns {Object} Extracted error info
 */
function extractBastardErrorInfo(data) {
    const content = data?.message?.content
    const textContent = content?.[0]?.text || ''

    // Format: "API Error: 500 {json...}" or "API Error: {json...}"
    const match = textContent.match(/^API Error:\s*(\d+)?\s*(.*)$/s)
    if (match) {
        const [, statusCode, jsonPart] = match
        try {
            const parsed = JSON.parse(jsonPart)
            // Structure: {"type":"error","error":{"type":"api_error","message":"..."}}
            return {
                type: parsed?.error?.type || 'unknown_error',
                message: parsed?.error?.message || textContent,
                status: statusCode ? parseInt(statusCode) : null,
                retryAttempt: null,
                maxRetries: null
            }
        } catch {
            // Invalid JSON, fall through to use raw text
        }
    }

    // Fallback: display raw text
    return {
        type: 'unknown_error',
        message: textContent || 'Unknown error',
        status: null,
        retryAttempt: null,
        maxRetries: null
    }
}

const errorInfo = computed(() => {
    // "Bastard" format: error is in content[0].text as a string
    if (props.data?.isApiErrorMessage) {
        return extractBastardErrorInfo(props.data)
    }

    // Classic format: nested error object
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
