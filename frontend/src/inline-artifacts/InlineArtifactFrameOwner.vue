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
const overlay = computed(() => props.pool.frameOverlayEl(props.entry.frameId))
const { brokerPrompt, onBrokerDecision } = useArtifactBroker(iframe,
    () => inlineArtifactBrokerConfig(props.entry, props.runtime, location.href),
    [iframe, () => props.entry.bindingKey])
</script>

<template>
    <Teleport :to="overlay || 'body'" :disabled="!overlay">
        <ArtifactBrokerPrompt
            :prompt="brokerPrompt"
            :visible="runtime.active.value && entry.visible"
            @decision="onBrokerDecision"
        />
    </Teleport>
</template>
