<script setup lang="ts">
import { computed, provide, useSlots } from 'vue'
import { DEFAULT_NODE_SIZE } from '../constants'
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
import styles from './GraphCore.module.scss'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = withDefaults(defineProps<{
  nodeSize?: number
  nodeTheme?: NodeTheme
  breakPointTheme?: BreakPointTheme
  orientation?: GraphOrientation
  enableResize?: boolean
  showCommitNodeHashes?: boolean
  showCommitNodeTooltips?: boolean
  highlightedBackgroundHeight?: number
}>(), {
  nodeSize: DEFAULT_NODE_SIZE,
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
} = useGitContext()

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
  nodeSize: computed(() => props.nodeSize),
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
    :class="styles.container"
    :style="{ width: `${width}px` }"
  >
    <slot />

    <button
      v-if="enableResize"
      :class="styles.dragHandle"
      @mousedown="startResizing"
    />
  </div>
</template>
