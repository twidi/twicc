<script setup lang="ts">
import { computed, provide, useSlots, watchEffect } from 'vue'
import { useGitContext } from '../composables/useGitContext'
import { useColumnData } from '../composables/useColumnData'
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
  showCommitNodeHashes?: boolean
  showCommitNodeTooltips?: boolean
  highlightedBackgroundHeight?: number
}>(), {
  nodeTheme: 'default',
  breakPointTheme: 'dot',
  orientation: 'normal',
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
// Visible commits
// ---------------------------------------------------------------------------

const visibleCommits = computed(() => {
  const commits = graphData.value.commits
  const pagingValue = paging.value
  if (pagingValue) {
    return commits.slice(pagingValue.startIndex, pagingValue.endIndex)
  }
  return commits
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

const graphColumnsWithVirtual = computed(() => graphData.value.graphColumns + virtualColumns.value)

const graphContextValue: GraphContextBag = {
  showCommitNodeHashes: computed(() => props.showCommitNodeHashes),
  showCommitNodeTooltips: computed(() => props.showCommitNodeTooltips),
  node: nodeSlotFn,
  highlightedBackgroundHeight: computed(() => props.highlightedBackgroundHeight),
  nodeTheme: computed(() => props.nodeTheme),
  breakPointTheme: computed(() => props.breakPointTheme),
  nodeSize,
  graphColumns: graphColumnsWithVirtual,
  orientation: computed(() => props.orientation),
  visibleCommits,
  isHeadCommitVisible,
  columnData,
  tooltip: tooltipSlotFn,
}

provide(GRAPH_CONTEXT_KEY, graphContextValue)

const graphCssVars = computed(() => ({
  '--commit-label-width': props.showCommitNodeHashes ? '2.5rem' : '0rem',
}))
</script>

<template>
  <div class="container" :style="graphCssVars">
    <slot />
  </div>
</template>

<style scoped>
.container {
  position: relative;
  width: var(--git-graph-width);
}

.graph {
  height: 100%;
  display: grid;
  gap: 0;
}

</style>
