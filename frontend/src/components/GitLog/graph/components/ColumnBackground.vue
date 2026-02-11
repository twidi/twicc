<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { BACKGROUND_HEIGHT_OFFSET } from '../../constants'
import { pxToRem } from '../../utils/units'
import { useGitContext } from '../../composables/useGitContext'
import { useGraphContext } from '../../composables/useGraphContext'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
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

const backgroundVars = computed<CSSProperties>(() => {
  const offset = (nodeSize.value  <= 16 ? 6 : 8) * 2;

  return {
    '--column-background-offset': pxToRem(offset),
    '--column-background-color': props.colour,
    '--column-background-height': pxToRem(height.value),
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
    :style="backgroundVars"
    :class="['background', {
      backgroundSquare: shouldShowFullBackground,
      withTable: showTable,
      isIndex: props.index === props.commitNodeIndex,
    }]"
  />
</template>

<style scoped>
.background {
  position: absolute;
  height: var(--column-background-height);

  &.withTable {
    background: var(--column-background-color);
    &.isIndex {
      right: 0;
      border-top-left-radius: 50%;
      border-bottom-left-radius: 50%;
      width: calc(50% + var(--git-node-size) / 2 + var(--column-background-offset) / 2);
    }
  }

  &:not(.withTable) {
    border-radius: 50%;
    --column-background-size: calc(var(--git-node-size) + var(--column-background-offset));
    height: var(--column-background-size);
    width: var(--column-background-size);
    left: calc(50% - var(--column-background-size) / 2);
  }
}

.backgroundSquare {
  width: 100%;
  left: 0;
}
</style>
