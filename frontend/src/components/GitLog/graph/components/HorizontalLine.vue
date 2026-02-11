<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useGraphContext } from '../../composables/useGraphContext'
import { useTheme } from '../../composables/useTheme'
import type { GraphColumnState } from '../GraphMatrixBuilder'

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

const lineConfig = computed<{ variant: string; vars: CSSProperties }>(() => {
  const farthestRightMergeNodeColumnIndex = props.state.mergeSourceColumns
    ? Math.max(...props.state.mergeSourceColumns)
    : undefined

  const borderColour = props.state.isPlaceholderSkeleton
    ? props.columnColour
    : getGraphColumnColour(farthestRightMergeNodeColumnIndex ?? props.commitNodeIndex)

  const borderStyle = props.state.isPlaceholderSkeleton ? 'dotted' : 'solid'

  const vars = {
    '--line-style': borderStyle,
    '--line-color': borderColour,
    '--line-zIndex': props.columnIndex + 1,
  }

  if (props.state.isNode && props.state.mergeSourceColumns) {
    const isNormalOrientation = orientation.value === 'normal'
    const variant = isNormalOrientation ? 'right-half' : 'left-half'
    return {
      variant,
      vars: {
        ...vars,
        '--line-right': isNormalOrientation ? '0' : '50%',
        '--line-width': '50%',
      },
    }
  }

  const isInFirstColumn = props.columnIndex === 0

  return {
    variant: isInFirstColumn ? 'right-half' : 'full-width',
    vars: {
        ...vars,
      '--line-right': 0,
      '--line-width': isInFirstColumn ? '50%' : '100%',
    },
  }
})
</script>

<template>
  <div
    :class="['line', 'horizontal']"
    :style="lineConfig.vars"
  />
</template>

<style scoped>
.line {
  position: absolute;
}

.horizontal {
  top: 50%;
  height: 2px;
  border-top: 2px var(--line-style) var(--line-color);
  right: var(--line-right);
  width: var(--line-width);
  z-index: var(--line-zIndex);
}
</style>
