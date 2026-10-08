<script setup>
import { computed } from 'vue'
import { useArtifactBroker, inlineArtifactBrokerConfig } from '../composables/useArtifactBroker.js'
import ArtifactBrokerPrompt from '../components/artifacts/ArtifactBrokerPrompt.vue'

const props = defineProps({
    entry: { type: Object, required: true },
    runtime: { type: Object, required: true },
    pool: { type: Object, required: true },
})
const iframe = computed(() => props.pool.frameEl(props.entry.frameId))
const frameVisible = computed(() => !!props.pool.frames[props.entry.frameId]?.visible)
const fullscreen = computed(() => props.runtime.fullscreenArtifactKey.value === props.entry.artifactKey)
const overlay = computed(() => props.pool.frameOverlayEl(props.entry.frameId))
const { brokerPrompt, onBrokerDecision } = useArtifactBroker(iframe,
    () => inlineArtifactBrokerConfig(props.entry, props.runtime, location.href),
    [iframe, () => props.entry.bindingKey])
</script>

<template>
    <Teleport :to="overlay || 'body'" :disabled="!overlay">
        <div v-if="runtime.active.value && frameVisible && !fullscreen" class="inline-artifact-controls">
            <button type="button" @click="runtime.openFullscreen(entry.artifactKey)">Full screen</button>
            <button type="button" @click="runtime.reload(entry.artifactKey)">Reload</button>
        </div>
        <ArtifactBrokerPrompt
            :prompt="brokerPrompt"
            :visible="runtime.active.value && frameVisible"
            @decision="onBrokerDecision"
        />
    </Teleport>
</template>

<style scoped>
.inline-artifact-controls { position: absolute; top: .5rem; right: .5rem; display: flex; gap: .25rem; pointer-events: auto; }
</style>
