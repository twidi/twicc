<script setup lang="ts">
import { computed, provide, useSlots, watchEffect } from 'vue'
import { useGitContext } from '../composables/useGitContext'
import { useColumnData } from '../composables/useColumnData'
import { useResize } from '../composables/useResize'
import {
  GRAPH_CONTEXT_KEY,
  type GraphContextBag,
} from '../composables/keys'
import type {
  BreakPointTheme,
  CustomCommitNodeProps,
  CustomTooltipProps,
  GraphOrientation,
  NodeTheme,
} from '../types'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = withDefaults(defineProps<{
  nodeTheme?: NodeTheme
  breakPointTheme?: BreakPointTheme
  orientation?: GraphOrientation
  enableResize?: boolean
  showCommitNodeHashes?: boolean
  showCommitNodeTooltips?: boolean
  highlightedBackgroundHeight?: number
}>(), {
  nodeTheme: 'default',
  breakPointTheme: 'dot',
  orientation: 'normal',
  enableResize: false,
  showCommitNodeHashes: false,
  showCommitNodeTooltips: false,
})

// ---------------------------------------------------------------------------
// Slots — bridge scoped slots to render function refs for GraphContextBag
// ---------------------------------------------------------------------------

defineSlots<{
  default(): unknown
  node?(props: CustomCommitNodeProps): unknown
  tooltip?(props: CustomTooltipProps): unknown
}>()

const slots = useSlots()

const nodeSlotFn = computed(() =>
  slots.node ? (slotProps: CustomCommitNodeProps) => slots.node!(slotProps) : undefined,
)

const tooltipSlotFn = computed(() =>
  slots.tooltip ? (slotProps: CustomTooltipProps) => slots.tooltip!(slotProps) : undefined,
)

// ---------------------------------------------------------------------------
// Git context
// ---------------------------------------------------------------------------

const {
  paging,
  filter,
  headCommit,
  graphData,
  nodeSize,
  setGraphOrientation,
} = useGitContext()

// Sync orientation prop to the shared GitContext so sibling components
// (e.g. Tags) can read it without accessing the GraphContext.
watchEffect(() => {
  setGraphOrientation(props.orientation)
})

// ---------------------------------------------------------------------------
// Resize
// ---------------------------------------------------------------------------

const { width, ref: graphContainerRef, startResizing } = useResize()

// ---------------------------------------------------------------------------
// Visible commits
// ---------------------------------------------------------------------------

const visibleCommits = computed(() => {
  const commits = graphData.value.commits
  const filteredCommits = filter.value?.(commits) ?? commits

  const pagingValue = paging.value
  if (pagingValue) {
    return filteredCommits.slice(pagingValue.startIndex, pagingValue.endIndex)
  }

  return filteredCommits
})

const isHeadCommitVisible = computed<boolean>(() => {
  const head = headCommit.value
  if (!head) return false
  return visibleCommits.value.some(commit => commit.hash === head.hash)
})

// ---------------------------------------------------------------------------
// Column data
// ---------------------------------------------------------------------------

const visibleCommitsCount = computed(() => visibleCommits.value.length)

const { columnData, virtualColumns } = useColumnData(visibleCommitsCount)

// ---------------------------------------------------------------------------
// Graph context — provide to all descendants
// ---------------------------------------------------------------------------

const graphWidthWithVirtual = computed(() => graphData.value.graphWidth + virtualColumns.value)

const graphContextValue: GraphContextBag = {
  showCommitNodeHashes: computed(() => props.showCommitNodeHashes),
  showCommitNodeTooltips: computed(() => props.showCommitNodeTooltips),
  node: nodeSlotFn,
  highlightedBackgroundHeight: computed(() => props.highlightedBackgroundHeight),
  nodeTheme: computed(() => props.nodeTheme),
  breakPointTheme: computed(() => props.breakPointTheme),
  nodeSize,
  graphWidth: graphWidthWithVirtual,
  orientation: computed(() => props.orientation),
  visibleCommits,
  isHeadCommitVisible,
  columnData,
  tooltip: tooltipSlotFn,
}

provide(GRAPH_CONTEXT_KEY, graphContextValue)
</script>

<template>
  <div
    ref="graphContainerRef"
    class="container"
    :style="{ width: `${width}px` }"
  >
    <slot />

    <button
      v-if="enableResize"
      class="dragHandle"
      @mousedown="startResizing"
    />
  </div>
</template>

<style scoped lang="scss">
.container {
  position: relative;
}

.graph {
  height: 100%;
  display: grid;
  gap: 0;
}

.dragHandle {
  position: absolute;
  right: -5px;
  top: 10px;
  width: 10px;
  height: 100%;
  z-index: 100;
  opacity: 0;
  transition: all ease-in-out 0.2s;
  border-radius: 0;
  box-shadow: none;

  &:hover {
    opacity: 1;
    cursor: ew-resize;
    border-left: none;
    border-right: 1px solid #cccccc;
    border-bottom: none;
    border-top: none;
    background: none;
  }
}
</style>
