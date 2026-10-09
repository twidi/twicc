<script setup>
import { computed, useId } from 'vue'
import { useArtifactBroker, inlineArtifactBrokerConfig } from '../composables/useArtifactBroker.js'
import ArtifactBrokerPrompt from '../components/artifacts/ArtifactBrokerPrompt.vue'
import FloatingPreviewTools from '../components/frames/FloatingPreviewTools.vue'

const props = defineProps({
    entry: { type: Object, required: true },
    runtime: { type: Object, required: true },
    pool: { type: Object, required: true },
})
const reloadButtonId = `inline-artifact-reload-${useId()}`
const actions = computed(() => [{ id: reloadButtonId, icon: 'rotate-right', label: 'Reload',
    action: () => props.runtime.reload(props.entry.artifactKey) }])
function toggleFullscreen() {
    if (fullscreen.value) props.runtime.closeFullscreen()
    else props.runtime.openFullscreen(props.entry.artifactKey)
}
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
        <FloatingPreviewTools
            :visible="runtime.active.value && frameVisible && entry.loadState !== 'error' && entry.descriptor.status !== 'error'"
            :actions="actions"
            :fullscreen="fullscreen"
            :reset-key="entry.descriptor.publicationKey"
            :container="overlay"
            :frame-rect="pool.frames[entry.frameId]?.rect"
            :visible-bounds="pool.frames[entry.frameId]?.clipRect"
            @toggle-fullscreen="toggleFullscreen"
            @drag-start="pool.beginDividerDrag"
            @drag-end="pool.endDividerDrag"
        />
        <ArtifactBrokerPrompt
            :prompt="brokerPrompt"
            :visible="runtime.active.value && frameVisible"
            @decision="onBrokerDecision"
        />
    </Teleport>
</template>
