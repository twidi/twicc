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
        <!-- JSON toggle button (visible on hover) -->
        <wa-button
            class="json-toggle"
            :variant="showJson ? 'warning' : 'neutral'"
            size="small"
            @click="toggleJsonView"
            :title="showJson ? 'Hide JSON' : 'Show JSON'"
        >
            <wa-icon name="code"></wa-icon>
        </wa-button>

        <!-- JSON view -->
        <div v-if="showJson" class="json-view">
            <div class="line-number">{{ lineNum }}</div>
            <div class="json-tree">
                <JsonNode
                    :data="parsedContent"
                    :path="'root'"
                    :collapsed-paths="collapsedPaths"
                    @toggle="toggleCollapse"
                />
            </div>
        </div>

        <!-- Formatted view based on kind -->
        <template v-else>
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
        </template>
    </div>
</template>

<style scoped>
.session-item {
    position: relative;
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
    line-height: 1.5;
}

.json-toggle {
    position: absolute;
    top: 0;
    right: 0;
    opacity: 0;
    transition: opacity 0.2s;
    z-index: 1;
}

.session-item:hover .json-toggle {
    opacity: 1;
}

.session-item:hover .json-toggle:hover {
    opacity: 1;
}

.json-view {
    display: flex;
    gap: var(--wa-space-m);
}

.line-number {
    flex-shrink: 0;
    width: 40px;
    text-align: right;
    color: var(--wa-color-text-subtle);
    font-weight: 500;
    user-select: none;
}

.json-tree {
    flex: 1;
    min-width: 0;
    overflow: auto;
}
</style>
