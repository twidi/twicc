<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { ROW_HEIGHT } from '../../constants'
import { useGitContext } from '../../composables/useGitContext'
import { placeholderCommits } from '../../graph/placeholderData'
import { HEADER_ROW_HEIGHT, TABLE_MARGIN_TOP } from '../constants'
import styles from './TableContainer.module.scss'

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

const { rowSpacing, showHeaders } = useGitContext()

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
    return `repeat(${commitsVisible}, ${ROW_HEIGHT}px)`
  }

  // With no row spacing, the header row height lines
  // up with the first data row fine. But when the row
  // spacing is increased, we must subtract half of it
  // from the height of the first header row to counteract
  // the gap between the header and the first data row.
  const headerRowHeight = HEADER_ROW_HEIGHT - (rowSpacing.value / 2)

  // All other rows (with data) get a fixed height.
  const remainingRowsHeight = `repeat(${commitsVisible}, ${ROW_HEIGHT}px)`

  return `${headerRowHeight}px ${remainingRowsHeight}`
})

const marginTop = computed(() => {
  if (showHeaders.value) {
    return TABLE_MARGIN_TOP
  }

  return (rowSpacing.value / 2) + TABLE_MARGIN_TOP
})

// ---------------------------------------------------------------------------
// Container styles
// ---------------------------------------------------------------------------

const containerStyle = computed<CSSProperties>(() => {
  if (props.hasCustomRow) {
    return {
      marginTop: `${TABLE_MARGIN_TOP}px`,
      ...props.styleOverrides,
    }
  }

  return {
    ...props.styleOverrides,
    marginTop: `${marginTop.value}px`,
    gridTemplateRows: gridTemplateRows.value,
    rowGap: `${rowSpacing.value}px`,
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
    :class="[styles.tableContainer, className]"
    :style="containerStyle"
  >
    <slot />
  </div>
</template>
