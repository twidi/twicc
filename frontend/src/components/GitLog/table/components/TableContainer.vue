<script setup lang="ts">
import { type CSSProperties } from 'vue'
import { useGitContext } from '../../composables/useGitContext'


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

const { showHeaders } = useGitContext()

</script>

<template>
  <!-- Custom row mode: plain div (no CSS Grid) -->
  <div
    v-if="hasCustomRow"
    id="vue-git-log-table"
    class="customTableContainer"
    data-testid="vue-git-log-table"
    :style="props.styleOverrides"
  >
    <slot />
  </div>

  <!-- Default mode: CSS Grid layout -->
  <div
    v-else
    id="vue-git-log-table"
    data-testid="vue-git-log-table"
    :class="['tableContainer', className, {hasHeaders: showHeaders}]"
    :style="props.styleOverrides"
  >
    <slot />
  </div>
</template>

<style scoped>
.tableContainer {
  display: grid;
  margin-top: 0;
  row-gap: 0;
  grid-template-columns: minmax(350px, 4fr) minmax(100px, 1fr) 195px;
  grid-template-rows: repeat(var(--git-commit-rows), var(--git-row-height));
  &.hasHeaders {
    grid-template-rows: var(--git-header-row-height) repeat(var(--git-commit-rows), var(--git-row-height));
  }
}
.customTableContainer {
    margin-top: 0;
}
</style>
