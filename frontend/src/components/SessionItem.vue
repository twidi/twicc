<script setup>
import { computed, ref } from 'vue'
import JsonNode from './JsonNode.vue'

const props = defineProps({
    content: {
        type: String,
        required: true
    },
    lineNum: {
        type: Number,
        default: null
    }
})

// Parse JSON content
const parsedContent = computed(() => {
    try {
        return JSON.parse(props.content)
    } catch {
        return { error: 'Invalid JSON', raw: props.content }
    }
})

// Track collapsed state for objects/arrays
const collapsedPaths = ref(new Set())

function toggleCollapse(path) {
    if (collapsedPaths.value.has(path)) {
        collapsedPaths.value.delete(path)
    } else {
        collapsedPaths.value.add(path)
    }
}
</script>

<template>
    <div class="session-item">
        <div v-if="lineNum !== null" class="line-number">{{ lineNum }}</div>
        <div class="json-tree">
            <JsonNode
                :data="parsedContent"
                :path="'root'"
                :collapsed-paths="collapsedPaths"
                @toggle="toggleCollapse"
            />
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

.json-tree {
    flex: 1;
    min-width: 0;
    overflow: auto;
}
</style>
