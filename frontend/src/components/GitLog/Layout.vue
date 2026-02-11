<script setup lang="ts">
import { computed } from 'vue'
import { CURVE_SIZE, NODE_BORDER_WIDTH } from './constants'
import { useGitContext } from './composables/useGitContext'
import { useTheme } from './composables/useTheme'
import { pxToRem } from './utils/units'
import { placeholderCommits } from './graph/placeholderData'

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const {
  classes,
  showHeaders,
  rowHeight,
  headerRowHeight,
  nodeSize,
  graphColumnWidth,
  graphData,
  paging,
  isIndexVisible,
} = useGitContext()

const { textColour, hoverColour, hoverTransitionDuration } = useTheme()

// ---------------------------------------------------------------------------
// Visible commit count & total row count
// ---------------------------------------------------------------------------

const visibleCommitCount = computed(() => {
  const commits = graphData.value.commits
  const pagingValue = paging.value
  if (pagingValue) {
    return pagingValue.endIndex - pagingValue.startIndex
  }
  return commits.length
})

const commitRowCount = computed(() => {
  if (visibleCommitCount.value === 0) {
    return placeholderCommits.length
  }
  return visibleCommitCount.value + (isIndexVisible.value ? 1 : 0)
})

// ---------------------------------------------------------------------------
// CSS custom properties — cascade sizing values to all descendants
// ---------------------------------------------------------------------------

const cssVars = computed(() => ({
  '--git-row-height': pxToRem(rowHeight.value),
  '--git-header-row-height': pxToRem(headerRowHeight.value),
  '--git-node-size': pxToRem(nodeSize.value),
  '--git-graph-columns': graphData.value.graphColumns,
  '--git-graph-column-width': pxToRem(graphColumnWidth.value),
  '--git-graph-width': `calc(var(--git-graph-columns) * var(--git-graph-column-width))`,
  '--git-curve-size': pxToRem(CURVE_SIZE),
  '--git-node-border-width': pxToRem(NODE_BORDER_WIDTH),
  '--git-visible-commits': visibleCommitCount.value,
  '--git-commit-rows': commitRowCount.value,
  '--git-text-color': textColour.value,
  '--git-hover-color': hoverColour.value,
  '--git-hover-transition-duration': `${hoverTransitionDuration}s`,
}))
</script>

<template>
  <div
    :style="[classes?.containerStyles, cssVars]"
    :class="['container', classes?.containerClass]"
  >
    <div v-if="$slots.tags" class="tags">
      <div
        v-if="showHeaders"
        class="title"
      >
        Branch / Tag
      </div>

      <slot name="tags" />
    </div>

    <div v-if="$slots.graph" class="graph">
      <div
        v-if="showHeaders"
        class="title"
      >
        Graph
      </div>

      <slot name="graph" />
    </div>

    <div v-if="$slots.table" class="table">
      <slot name="table" />
    </div>
  </div>
</template>

<style scoped>
.container {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;

  .tags, .graph {
      display: flex;
      flex-direction: column;
  }

  .tags {
    flex-grow: 1;
    max-width: 10rem;
      .title {
          margin-left: .5rem;
      }
  }

  .table {
    flex-grow: 1;
  }
}

.title {
  margin: 0;
  font-weight: 600;
  display: flex;
  flex-shrink: 0;
  align-items: center;
  height: var(--git-header-row-height);
  color: var(--git-text-color);
}
</style>
