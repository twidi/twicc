<script setup lang="ts">
import { computed } from 'vue'
import { useGraphContext } from '../../composables/useGraphContext'
import type { Commit } from '../../types'
import type { GraphColumnState } from '../GraphMatrixBuilder/types'
import GraphColumn from './GraphColumn.vue'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  /** The zero-based row id used for tabIndex and indexing. */
  id: number

  /** The commit represented by this row. */
  commit: Commit

  /** The column states for this row. */
  columns: GraphColumnState[]
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { graphColumns, orientation } = useGraphContext()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const commitNodeIndex = computed(() =>
  props.columns.findIndex(col => col.isNode),
)

/**
 * The empty state used for virtual columns that have no entry in
 * the column states array.
 */
const emptyColumnState: GraphColumnState = {}
</script>

<template>
  <GraphColumn
    v-for="i in graphColumns"
    :key="`row_${commit.hash}_column_${orientation === 'normal' ? i - 1 : graphColumns - i}`"
    :row-index="id"
    :commit="commit"
    :index="orientation === 'normal' ? i - 1 : graphColumns - i"
    :commit-node-index="commitNodeIndex"
    :state="columns[orientation === 'normal' ? i - 1 : graphColumns - i] ?? emptyColumnState"
  />
</template>
