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
    class="customTableContainer"
    :style="props.styleOverrides"
  >
    <slot />
  </div>

  <!-- Default mode: CSS Grid layout -->
  <div
    v-else
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
  grid-template-columns: minmax(20rem, 4fr) minmax(6rem, 1fr) 12rem;
  grid-template-rows: repeat(var(--git-commit-rows), var(--git-row-height));
  &.hasHeaders {
    grid-template-rows: var(--git-header-row-height) repeat(var(--git-commit-rows), var(--git-row-height));
  }
}
.customTableContainer {
    margin-top: 0;
}
</style>
