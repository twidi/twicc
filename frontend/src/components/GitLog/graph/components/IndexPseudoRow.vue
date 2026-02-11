<script setup lang="ts">
import { computed } from 'vue'
import { useGitContext } from '../../composables/useGitContext'
import { useGraphContext } from '../../composables/useGraphContext'
import type { GraphColumnState } from '../GraphMatrixBuilder/types'
import GraphRow from './GraphRow.vue'

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { indexCommit } = useGitContext()
const { graphColumns, isHeadCommitVisible } = useGraphContext()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

/**
 * Builds the column states for the index pseudo-row.
 *
 * The first column (index 0) contains the node and vertical line.
 * A bottom breakpoint is shown when the HEAD commit is not visible
 * in the current pagination, to indicate a gap.
 */
const indexColumns = computed<GraphColumnState[]>(() => {
  const columns = new Array<GraphColumnState>(graphColumns.value).fill({})

  columns[0] = {
    isNode: true,
    isVerticalLine: true,
    isBottomBreakPoint: !isHeadCommitVisible.value,
  }

  return columns
})
</script>

<template>
  <GraphRow
    v-if="indexCommit"
    :id="0"
    :commit="indexCommit"
    :columns="indexColumns"
  />
</template>
