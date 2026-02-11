<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { BACKGROUND_HEIGHT_OFFSET } from '../../constants'
import { pxToRem } from '../../utils/units'
import { useGitContext } from '../../composables/useGitContext'
import { useGraphContext } from '../../composables/useGraphContext'
import { getColumnBackgroundSize } from '../utils/getColumnBackgroundSize'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  id: string
  index: number
  colour: string
  commitNodeIndex: number
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { showTable, rowHeight } = useGitContext()
const { nodeSize, orientation, highlightedBackgroundHeight } = useGraphContext()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const height = computed<number>(() => {
  if (highlightedBackgroundHeight.value) {
    return highlightedBackgroundHeight.value
  }

  const dynamicHeight = nodeSize.value + BACKGROUND_HEIGHT_OFFSET
  return dynamicHeight > rowHeight.value ? rowHeight.value : dynamicHeight
})

const backgroundStyle = computed<CSSProperties>(() => {
  const offset = getColumnBackgroundSize({ nodeSize: nodeSize.value })

  if (!showTable.value) {
    const backgroundSize = nodeSize.value + offset

    return {
      borderRadius: '50%',
      height: pxToRem(backgroundSize),
      width: pxToRem(backgroundSize),
      background: props.colour,
      left: `calc(50% - ${pxToRem(backgroundSize / 2)})`,
    }
  }

  if (props.index === props.commitNodeIndex) {
    return {
      width: `calc(50% + ${pxToRem(nodeSize.value / 2 + offset / 2)})`,
      height: pxToRem(height.value),
      background: props.colour,
      right: '0',
      borderTopLeftRadius: '50%',
      borderBottomLeftRadius: '50%',
    }
  }

  return {
    height: pxToRem(height.value),
    background: props.colour,
  }
})

const shouldShowFullBackground = computed(() =>
  orientation.value === 'normal'
    ? props.index > props.commitNodeIndex
    : props.index < props.commitNodeIndex,
)
</script>

<template>
  <div
    :id="`column-background-${index}-${id}`"
    :data-testid="`column-background-${index}-${id}`"
    :style="backgroundStyle"
    :class="[
      'background',
      shouldShowFullBackground && 'backgroundSquare',
    ]"
  />
</template>

<style scoped>
.background {
  position: absolute;
}

.backgroundSquare {
  width: 100%;
  left: 0;
}
</style>
