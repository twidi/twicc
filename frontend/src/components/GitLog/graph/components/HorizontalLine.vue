<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useGraphContext } from '../../composables/useGraphContext'
import { useTheme } from '../../composables/useTheme'
import type { GraphColumnState } from '../GraphMatrixBuilder'
import styles from './HorizontalLine.module.scss'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  state: GraphColumnState
  columnIndex: number
  commitNodeIndex: number
  columnColour: string
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { orientation } = useGraphContext()
const { getGraphColumnColour } = useTheme()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const lineConfig = computed<{ variant: string; style: CSSProperties }>(() => {
  const farthestRightMergeNodeColumnIndex = props.state.mergeSourceColumns
    ? Math.max(...props.state.mergeSourceColumns)
    : undefined

  const borderColour = props.state.isPlaceholderSkeleton
    ? props.columnColour
    : getGraphColumnColour(farthestRightMergeNodeColumnIndex ?? props.commitNodeIndex)

  const borderStyle = props.state.isPlaceholderSkeleton ? 'dotted' : 'solid'

  if (props.state.isNode && props.state.mergeSourceColumns) {
    const isNormalOrientation = orientation.value === 'normal'
    const variant = isNormalOrientation ? 'right-half' : 'left-half'
    return {
      variant,
      style: {
        borderTop: `2px ${borderStyle} ${borderColour}`,
        width: '50%',
        right: isNormalOrientation ? '0' : '50%',
        zIndex: props.columnIndex + 1,
      },
    }
  }

  const isInFirstColumn = props.columnIndex === 0

  return {
    variant: isInFirstColumn ? 'right-half' : 'full-width',
    style: {
      borderTop: `2px ${borderStyle} ${borderColour}`,
      width: isInFirstColumn ? '50%' : '100%',
      zIndex: props.columnIndex + 1,
      right: '0',
    },
  }
})
</script>

<template>
  <div
    :id="`horizontal-line-${lineConfig.variant}`"
    :data-testid="`horizontal-line-${lineConfig.variant}`"
    :class="[styles.line, styles.horizontal]"
    :style="lineConfig.style"
  />
</template>
