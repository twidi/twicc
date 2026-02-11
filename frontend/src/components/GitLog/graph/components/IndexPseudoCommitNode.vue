<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  columnColour: string
  animate: boolean
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { shiftAlphaChannel } = useTheme()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const nodeVars = computed<CSSProperties>(() => ({
  '--index-border-color': shiftAlphaChannel(props.columnColour, 0.5),
  '--index-background-color': shiftAlphaChannel(props.columnColour, 0.05),
}))
</script>

<template>
  <div
    id="index-pseudo-commit-node"
    :class="[
      'indexNode',
      animate && 'spin',
    ]"
    :style="nodeVars"
  />
</template>

<style scoped>
.indexNode {
  width: var(--git-node-size);
  height: var(--git-node-size);
  border: 2px dotted var(--index-border-color);
  background-color: var(--index-background-color);

  border-radius: 50%;
  z-index: 20;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.spin {
  animation: spin 10s linear infinite;
}
</style>
