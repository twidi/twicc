<script setup>
import { computed, ref } from 'vue'
import JsonNode from '../../JsonNode.vue'

const props = defineProps({
    name: {
        type: String,
        required: true
    },
    input: {
        type: Object,
        default: () => ({})
    },
    toolId: {
        type: String,
        required: true
    },
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        required: true
    },
    lineNum: {
        type: Number,
        required: true
    }
})

// Tool result state
const resultState = ref('idle') // 'idle' | 'loading' | 'loaded' | 'error'
const resultData = ref(null)
const resultError = ref(null)

async function onResultOpen() {
    // Only fetch once
    if (resultState.value !== 'idle') return

    resultState.value = 'loading'
    resultError.value = null

    try {
        const url = `/api/projects/${props.projectId}/sessions/${props.sessionId}/items/${props.lineNum}/tool-results/${props.toolId}/`
        const response = await fetch(url)

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`)
        }

        const data = await response.json()
        resultData.value = data.results
        resultState.value = 'loaded'
    } catch (err) {
        resultError.value = err.message
        resultState.value = 'error'
    }
}

// Computed for display: single result or array of multiple
const displayResult = computed(() => {
    if (!resultData.value || resultData.value.length === 0) return null
    if (resultData.value.length === 1) return resultData.value[0]
    return resultData.value
})

// Extract description from input if present
const description = computed(() => props.input?.description || null)

// Input without description for display
const displayInput = computed(() => {
    if (!props.input || Object.keys(props.input).length === 0) {
        return null
    }
    const { description, ...rest } = props.input
    return Object.keys(rest).length > 0 ? rest : null
})

// Collapsed paths for JsonNode (input)
const collapsedPaths = ref(new Set())

// Collapsed paths for result JsonNode
const resultCollapsedPaths = ref(new Set())

function togglePath(path) {
    if (collapsedPaths.value.has(path)) {
        collapsedPaths.value.delete(path)
    } else {
        collapsedPaths.value.add(path)
    }
    // Trigger reactivity
    collapsedPaths.value = new Set(collapsedPaths.value)
}

function toggleResultPath(path) {
    if (resultCollapsedPaths.value.has(path)) {
        resultCollapsedPaths.value.delete(path)
    } else {
        resultCollapsedPaths.value.add(path)
    }
    // Trigger reactivity
    resultCollapsedPaths.value = new Set(resultCollapsedPaths.value)
}
</script>

<template>
    <wa-details class="tool-use" icon-placement="start">
        <span slot="summary" class="tool-use-summary">
            <strong class="tool-name">{{ name }}</strong>
            <template v-if="description">
                <span class="tool-separator"> — </span>
                <span class="tool-description">{{ description }}</span>
            </template>
        </span>
        <div v-if="displayInput" class="tool-input">
            <JsonNode
                :data="displayInput"
                path="root"
                :collapsed-paths="collapsedPaths"
                @toggle="togglePath"
            />
        </div>
        <div v-else class="tool-no-input">
            No input parameters
        </div>
        <wa-details class="tool-result" @wa-show="onResultOpen">
            <span slot="summary">Result</span>
            <div class="tool-result-content">
                <div v-if="resultState === 'loading'" class="tool-result-loading">
                    <wa-spinner></wa-spinner>
                    <span>Loading result...</span>
                </div>
                <div v-else-if="resultState === 'error'" class="tool-result-error">
                    Error loading result: {{ resultError }}
                </div>
                <div v-else-if="resultState === 'loaded' && !displayResult" class="tool-result-empty">
                    No result available
                </div>
                <div v-else-if="resultState === 'loaded' && displayResult" class="tool-result-data">
                    <JsonNode
                        :data="displayResult"
                        path="result"
                        :collapsed-paths="resultCollapsedPaths"
                        @toggle="toggleResultPath"
                    />
                </div>
            </div>
        </wa-details>
    </wa-details>
</template>

<style scoped>
.tool-use {
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
    padding-block: var(--wa-space-l);
}

.tool-use-summary {
    display: inline;
}

.tool-name {
    color: var(--wa-color-text);
}

.tool-separator {
    color: var(--wa-color-text-subtle);
}

.tool-description {
    color: var(--wa-color-text);
    font-weight: normal;
}

.tool-input {
    padding: var(--wa-space-xs) 0;
    overflow-x: auto;
}

.tool-no-input {
    color: var(--wa-color-text-subtle);
    font-style: italic;
    padding: var(--wa-space-xs) 0;
}

.tool-result {
    margin-top: var(--wa-space-l);
}

.tool-result-content {
    padding: var(--wa-space-xs) 0;
}

.tool-result-loading {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-subtle);
}

.tool-result-error {
    color: var(--wa-color-danger-text);
}

.tool-result-empty {
    color: var(--wa-color-text-subtle);
    font-style: italic;
}

.tool-result-data {
    overflow-x: auto;
}
</style>
