<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { pxToRem } from '../../utils/units'
import { useGitContext } from '../../composables/useGitContext'
import { placeholderCommits } from '../../graph/placeholderData'


// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = withDefaults(defineProps<{
  rowQuantity: number
  className?: string
  styleOverrides?: CSSProperties
  hasCustomRow?: boolean
}>(), {
  hasCustomRow: false,
})

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { rowHeight, headerRowHeight, showHeaders } = useGitContext()

// ---------------------------------------------------------------------------
// Grid layout computation
// ---------------------------------------------------------------------------

const gridTemplateRows = computed(() => {
  // If no commits are visible as we're showing
  // the placeholder data for the skeleton view,
  // then use that size, else just use the log data length.
  const commitsVisible = props.rowQuantity > 0
    ? props.rowQuantity
    : placeholderCommits.length

  // If the table headers are turned off, then we simply
  // repeat the same row height for all rows.
  if (!showHeaders.value) {
    return `repeat(${commitsVisible}, ${pxToRem(rowHeight.value)})`
  }

  // All other rows (with data) get a fixed height.
  const remainingRowsHeight = `repeat(${commitsVisible}, ${pxToRem(rowHeight.value)})`

  return `${pxToRem(headerRowHeight.value)} ${remainingRowsHeight}`
})

// ---------------------------------------------------------------------------
// Container styles
// ---------------------------------------------------------------------------

const containerStyle = computed<CSSProperties>(() => {
  if (props.hasCustomRow) {
    return {
      marginTop: 0,
      ...props.styleOverrides,
    }
  }

  return {
    ...props.styleOverrides,
    marginTop: 0,
    gridTemplateRows: gridTemplateRows.value,
    rowGap: 0,
  }
})
</script>

<template>
  <!-- Custom row mode: plain div (no CSS Grid) -->
  <div
    v-if="hasCustomRow"
    id="vue-git-log-table"
    data-testid="vue-git-log-table"
    :style="containerStyle"
  >
    <slot />
  </div>

  <!-- Default mode: CSS Grid layout -->
  <div
    v-else
    id="vue-git-log-table"
    data-testid="vue-git-log-table"
    :class="['tableContainer', className]"
    :style="containerStyle"
  >
    <slot />
  </div>
</template>

<style scoped lang="scss">
.tableContainer {
  display: grid;
  grid-template-columns: minmax(350px, 4fr) minmax(100px, 1fr) 195px;
}
</style>
