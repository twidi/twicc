<script setup>
import { computed, ref } from 'vue'
import JsonNode from './JsonNode.vue'
import Message from './items/Message.vue'
import ApiError from './items/ApiError.vue'
import UnknownEntry from './items/UnknownEntry.vue'

const props = defineProps({
    content: {
        type: String,
        required: true
    },
    kind: {
        type: String,
        default: null
    },
    // Context for store lookups (propagated to Message/ContentList)
    sessionId: {
        type: String,
        required: true
    },
    lineNum: {
        type: Number,
        required: true
    },
    // Group props for ALWAYS items with prefix/suffix
    groupHead: {
        type: Number,
        default: null
    },
    groupTail: {
        type: Number,
        default: null
    },
    prefixExpanded: {
        type: Boolean,
        default: false
    },
    suffixExpanded: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['toggle-suffix'])

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
                <div class="item-body">
                    <Message
                        v-if="kind === 'user_message' || kind === 'assistant_message'"
                        :data="parsedContent"
                        :role="kind === 'user_message' ? 'user' : 'assistant'"
                        :session-id="sessionId"
                        :line-num="lineNum"
                        :group-head="groupHead"
                        :group-tail="groupTail"
                        :prefix-expanded="prefixExpanded"
                        :suffix-expanded="suffixExpanded"
                        @toggle-suffix="emit('toggle-suffix')"
                    />
                    <ApiError
                        v-else-if="kind === 'api_error'"
                        :data="parsedContent"
                    />
                    <UnknownEntry
                        v-else
                        :type="entryType"
                        :data="parsedContent"
                    />
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

.item-body {
    padding-right: var(--wa-space-xl); /* Space for the toggle button */
}
</style>
