<script setup>
import { ref } from 'vue'
import JsonNode from '../JsonNode.vue'

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

// Collapsed paths for JsonNode
const collapsedPaths = ref(new Set())

function togglePath(path) {
    if (collapsedPaths.value.has(path)) {
        collapsedPaths.value.delete(path)
    } else {
        collapsedPaths.value.add(path)
    }
    // Trigger reactivity
    collapsedPaths.value = new Set(collapsedPaths.value)
}
</script>

<template>
    <wa-details class="item-details unknown-entry" icon-placement="start">
        <span slot="summary" class="unknown-summary">
            <strong class="unknown-label">Unhandled event</strong>
            <span class="unknown-separator"> — </span>
            <span class="unknown-type">{{ type }}</span>
        </span>
        <div v-if="data" class="unknown-data">
            <JsonNode
                :data="data"
                path="root"
                :collapsed-paths="collapsedPaths"
                @toggle="togglePath"
            />
        </div>
        <div v-else class="unknown-no-data">
            No data available
        </div>
    </wa-details>
</template>

<style scoped>
.unknown-entry {
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
    padding-block: var(--wa-space-l);
}

.unknown-summary {
    display: inline;
}

.unknown-label {
    color: var(--wa-color-text);
}

.unknown-separator {
    color: var(--wa-color-text-subtle);
}

.unknown-type {
    color: var(--wa-color-text);
    font-weight: normal;
}

.unknown-data {
    padding: var(--wa-space-xs) 0;
    overflow-x: auto;
}

.unknown-no-data {
    color: var(--wa-color-text-subtle);
    font-style: italic;
    padding: var(--wa-space-xs) 0;
}
</style>
