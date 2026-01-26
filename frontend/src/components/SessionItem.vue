<script setup>
import { computed, ref } from 'vue'
import JsonNode from './JsonNode.vue'

const props = defineProps({
    content: {
        type: String,
        required: true
    },
    kind: {
        type: String,
        default: null
    },
    lineNum: {
        type: Number,
        default: null
    }
})

// Toggle for showing raw JSON
const showJson = ref(false)

// Parse JSON content
const parsedContent = computed(() => {
    try {
        return JSON.parse(props.content)
    } catch {
        return { error: 'Invalid JSON', raw: props.content }
    }
})

// Get the entry type from parsed JSON (for unknown kind display)
const entryType = computed(() => parsedContent.value?.type || 'unknown')

// Get message content array (for user_message and assistant_message)
const messageContent = computed(() => {
    const parsed = parsedContent.value
    const content = parsed?.message?.content

    // If content is a string, treat it as a single text item
    if (typeof content === 'string') {
        return [{ type: 'text', text: content }]
    }

    // If content is an array, return it as-is
    if (Array.isArray(content)) {
        return content
    }

    return []
})

// Get API error info
const apiError = computed(() => {
    const parsed = parsedContent.value
    if (props.kind !== 'api_error') return null

    const error = parsed?.error?.error?.error
    return {
        type: error?.type || 'unknown_error',
        message: error?.message || 'Unknown error',
        status: parsed?.error?.status,
        retryAttempt: parsed?.retryAttempt,
        maxRetries: parsed?.maxRetries
    }
})

// Track collapsed state for JSON view
const collapsedPaths = ref(new Set())

function toggleCollapse(path) {
    if (collapsedPaths.value.has(path)) {
        collapsedPaths.value.delete(path)
    } else {
        collapsedPaths.value.add(path)
    }
}

function toggleJsonView() {
    showJson.value = !showJson.value
}
</script>

<template>
    <div class="session-item">
        <div v-if="lineNum !== null" class="line-number">{{ lineNum }}</div>

        <div class="item-content">
            <!-- JSON toggle button -->
            <button
                class="json-toggle"
                @click="toggleJsonView"
                :title="showJson ? 'Hide JSON' : 'Show JSON'"
            >
                <wa-icon :name="showJson ? 'code-slash' : 'code'"></wa-icon>
            </button>

            <!-- JSON view -->
            <div v-if="showJson" class="json-tree">
                <JsonNode
                    :data="parsedContent"
                    :path="'root'"
                    :collapsed-paths="collapsedPaths"
                    @toggle="toggleCollapse"
                />
            </div>

            <!-- Formatted view based on kind -->
            <template v-else>
                <!-- User message / Assistant message -->
                <div v-if="kind === 'user_message' || kind === 'assistant_message'" class="message-content">
                    <template v-for="(entry, idx) in messageContent" :key="idx">
                        <div v-if="entry.type === 'text'" class="content-text">{{ entry.text }}</div>
                        <div v-else-if="entry.type === 'image'" class="content-placeholder">[image]</div>
                        <div v-else-if="entry.type === 'document'" class="content-placeholder">[document]</div>
                        <div v-else class="content-other">[{{ entry.type }}]</div>
                    </template>
                </div>

                <!-- API error -->
                <div v-else-if="kind === 'api_error'" class="message api-error">
                    <div class="message-header">API Error</div>
                    <div class="message-body">
                        <div class="error-type">{{ apiError.type }}</div>
                        <div class="error-message">{{ apiError.message }}</div>
                        <div v-if="apiError.status" class="error-detail">Status: {{ apiError.status }}</div>
                        <div v-if="apiError.retryAttempt" class="error-detail">
                            Retry {{ apiError.retryAttempt }}/{{ apiError.maxRetries }}
                        </div>
                    </div>
                </div>

                <!-- Unknown type (kind is null) -->
                <div v-else class="unknown-type">
                    Unknown type ({{ entryType }})
                </div>
            </template>
        </div>
    </div>
</template>

<style scoped>
.session-item {
    display: flex;
    gap: var(--wa-space-m);
    padding: var(--wa-space-m);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-radius-m);
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
    line-height: 1.5;
}

.line-number {
    flex-shrink: 0;
    width: 40px;
    text-align: right;
    color: var(--wa-color-text-subtle);
    font-weight: 500;
    user-select: none;
}

.item-content {
    flex: 1;
    min-width: 0;
    position: relative;
}

.json-toggle {
    position: absolute;
    top: 0;
    right: 0;
    background: var(--wa-color-surface);
    border: 1px solid var(--wa-color-border);
    border-radius: var(--wa-radius-s);
    padding: var(--wa-space-xs);
    cursor: pointer;
    opacity: 0.6;
    transition: opacity 0.2s;
    z-index: 1;
}

.json-toggle:hover {
    opacity: 1;
}

.json-tree {
    overflow: auto;
}

/* Message content (user/assistant) */
.message-content {
    padding-right: var(--wa-space-xl); /* Space for the toggle button */
    font-family: var(--wa-font-sans);
}

.message-content > * + * {
    margin-top: var(--wa-space-s);
}

/* Content items */
.content-text {
    white-space: pre-wrap;
    word-break: break-word;
}

.content-placeholder {
    color: var(--wa-color-text-subtle);
    font-style: italic;
}

.content-other {
    color: var(--wa-color-text-subtle);
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-xs);
}

/* API error */
.api-error {
    padding-right: var(--wa-space-xl); /* Space for the toggle button */
}

.api-error .message-header {
    font-weight: 600;
    margin-bottom: var(--wa-space-s);
    color: var(--wa-color-danger);
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.api-error .message-body {
    font-family: var(--wa-font-sans);
}

.api-error .error-type {
    font-weight: 600;
    color: var(--wa-color-danger);
}

.api-error .error-message {
    margin-top: var(--wa-space-xs);
}

.api-error .error-detail {
    margin-top: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-subtle);
}

/* Unknown type */
.unknown-type {
    padding-right: var(--wa-space-xl); /* Space for the toggle button */
    color: var(--wa-color-text-subtle);
}
</style>
