<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'
import { useGraphContext } from '../../composables/useGraphContext'
import styles from './IndexPseudoCommitNode.module.scss'

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
  width: `${nodeSize.value}px`,
  height: `${nodeSize.value}px`,
  border: `2px dotted ${shiftAlphaChannel(props.columnColour, 0.5)}`,
  backgroundColor: shiftAlphaChannel(props.columnColour, 0.05),
}))
</script>

<template>
  <div
    id="index-pseudo-commit-node"
    data-testid="index-pseudo-commit-node"
    :class="[
      styles.indexNode,
      animate && styles.spin,
    ]"
    :style="nodeStyles"
  />
</template>
