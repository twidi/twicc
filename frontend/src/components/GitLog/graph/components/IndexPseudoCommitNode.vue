<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'
import { useGraphContext } from '../../composables/useGraphContext'
import { pxToRem } from '../../utils/units'

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

const { nodeSize } = useGraphContext()
const { shiftAlphaChannel } = useTheme()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const nodeStyles = computed<CSSProperties>(() => ({
  width: pxToRem(nodeSize.value),
  height: pxToRem(nodeSize.value),
  border: `2px dotted ${shiftAlphaChannel(props.columnColour, 0.5)}`,
  backgroundColor: shiftAlphaChannel(props.columnColour, 0.05),
}))
</script>

<template>
  <div
    id="index-pseudo-commit-node"
    data-testid="index-pseudo-commit-node"
    :class="[
      'indexNode',
      animate && 'spin',
    ]"
    :style="nodeStyles"
  />
</template>

<style scoped lang="scss">
.indexNode {
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
