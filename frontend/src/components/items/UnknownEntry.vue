<script setup>
import { ref } from 'vue'
import JsonViewer from '../JsonViewer.vue'

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
        <span slot="summary" class="items-details-summary">
            <strong class="items-details-summary-name">Unhandled event</strong>
            <span class="items-details-summary-separator"> — </span>
            <span class="items-details-summary-description">{{ type }}</span>
        </span>
        <div v-if="data" class="unknown-data">
            <JsonViewer
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
.unknown-data {
    padding: var(--wa-space-xs) 0;
    overflow-x: auto;
}

.unknown-no-data {
    color: var(--wa-color-text-quiet);
    font-style: italic;
    padding: var(--wa-space-xs) 0;
}
</style>
