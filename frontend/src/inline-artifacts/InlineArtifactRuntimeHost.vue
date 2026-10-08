<script setup>
import InlineArtifactFrameOwner from './InlineArtifactFrameOwner.vue'

// These owners survive transcript row detachment and cached view deactivation.
// The application FrameHost owns all iframe DOM nodes.
defineProps({
    runtime: { type: Object, required: true },
    pool: { type: Object, required: true },
})
</script>

<template>
    <InlineArtifactFrameOwner
        v-for="entry in runtime.loadedEntries"
        :key="entry.artifactKey"
        :entry="entry"
        :runtime="runtime"
        :pool="pool"
    />
</template>
