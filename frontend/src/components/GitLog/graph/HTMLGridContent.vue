<script setup lang="ts">
import { useGraphContext } from '../composables/useGraphContext'
import { useGitContext } from '../composables/useGitContext'
import { getEmptyColumnState } from './utils/getEmptyColumnState'
import SkeletonGraph from './components/SkeletonGraph.vue'
import IndexPseudoRow from './components/IndexPseudoRow.vue'
import GraphRow from './components/GraphRow.vue'

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { graphColumns, visibleCommits, columnData } = useGraphContext()
const { isIndexVisible, paging } = useGitContext()

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
    ?? getEmptyColumnState({ columns: graphColumns.value })
}
</script>

<template>
  <div class="graph">
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

<style scoped>
.graph {
  height: 100%;
  display: grid;
  gap: 0;
  grid-template-columns: repeat(var(--git-log-graph-columns), 1fr);
  grid-template-rows: repeat(var(--git-log-commit-rows), var(--git-log-row-height));
}
</style>
