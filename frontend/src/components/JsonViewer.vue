<script setup>
import { ref } from 'vue'
import JsonNode from './JsonNode.vue'

defineProps({
    data: { required: true },
    path: { type: String, default: 'root' },
    collapsedPaths: { type: Set, required: true }
})

const emit = defineEmits(['toggle'])

// Wrap state (default: no wrap, scrollable)
const wrap = ref(false)

function forwardToggle(path) {
    emit('toggle', path)
}
</script>

<template>
    <div class="json-viewer" :class="{ 'json-viewer--wrap': wrap }">
        <wa-button
            class="json-viewer-wrap-toggle"
            variant="text"
            size="small"
            @click="wrap = !wrap"
            :title="wrap ? 'Disable wrap' : 'Enable wrap'"
        >
            <wa-icon :name="wrap ? 'align-justify' : 'align-left'"></wa-icon>
        </wa-button>
        <JsonNode
            :data="data"
            :path="path"
            :collapsed-paths="collapsedPaths"
            @toggle="forwardToggle"
        />
    </div>
</template>

<style scoped>
.json-viewer {
    position: relative;
    white-space: pre;
}

.json-viewer--wrap {
    white-space: pre-wrap;
    word-break: break-all;
}

.json-viewer-wrap-toggle {
    position: absolute;
    top: -.75em;
    right: 0;
    opacity: 0.3;
    transition: opacity 0.2s;
    z-index: 1;
    transform-origin: top center;
    scale: 0.5;
    &::part(label) {
        scale: 1.5;
    }
}

.json-viewer-wrap-toggle:hover {
    opacity: 1;
}
</style>
