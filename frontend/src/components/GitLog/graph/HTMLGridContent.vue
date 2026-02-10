<script setup lang="ts">
import { computed } from 'vue'
import { ROW_HEIGHT } from '../constants'
import { useGraphContext } from '../composables/useGraphContext'
import { useGitContext } from '../composables/useGitContext'
import { getEmptyColumnState } from './utils/getEmptyColumnState'
import { placeholderCommits } from './placeholderData'
import { GRAPH_MARGIN_TOP } from './constants'
import SkeletonGraph from './components/SkeletonGraph.vue'
import IndexPseudoRow from './components/IndexPseudoRow.vue'
import GraphRow from './components/GraphRow.vue'
import styles from './GraphCore.module.scss'

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { graphWidth, visibleCommits, columnData } = useGraphContext()
const { isIndexVisible, rowSpacing, paging } = useGitContext()

// ---------------------------------------------------------------------------
// Grid layout computation
// ---------------------------------------------------------------------------

const commitQuantity = computed(() => {
  // When there is no data, render the skeleton graph placeholder
  // which shows fake commits.
  if (visibleCommits.value.length === 0) {
    return placeholderCommits.length
  }

  // When the index node is visible, show one extra commit
  // in the form of the index pseudo-node.
  if (isIndexVisible.value) {
    return visibleCommits.value.length + 1
  }

  return visibleCommits.value.length
})

const wrapperStyle = computed(() => ({
  gridTemplateColumns: `repeat(${graphWidth.value}, 1fr)`,
  gridTemplateRows: `repeat(${commitQuantity.value}, ${ROW_HEIGHT + rowSpacing.value}px)`,
  marginTop: `${GRAPH_MARGIN_TOP}px`,
}))

// ---------------------------------------------------------------------------
// Row data helpers
// ---------------------------------------------------------------------------

/**
 * Returns the column states for a commit at the given visual index.
 * The row index in the column data map accounts for pagination offset.
 */
function getColumnsForCommit(index: number) {
  const pagingValue = paging.value
  const rowIndex = pagingValue ? index + pagingValue.startIndex + 1 : index + 1
  return columnData.value.get(rowIndex)?.columns
    ?? getEmptyColumnState({ columns: graphWidth.value })
}
</script>

<template>
  <div :class="styles.graph" :style="wrapperStyle">
    <!-- Skeleton placeholder (rendered when no visible commits) -->
    <SkeletonGraph v-if="visibleCommits.length === 0" />

    <!-- Index pseudo-row (rendered when the git index is visible) -->
    <IndexPseudoRow v-if="visibleCommits.length > 0 && isIndexVisible" />

    <!-- Commit rows -->
    <GraphRow
      v-for="(commit, index) in visibleCommits"
      :key="commit.hash"
      :id="index + 1"
      :commit="commit"
      :columns="getColumnsForCommit(index)"
    />
  </div>
</template>
