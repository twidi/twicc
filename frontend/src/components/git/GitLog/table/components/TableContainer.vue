<script setup lang="ts">
import { type CSSProperties, type PropType } from 'vue'
import { useGitContext } from '../../composables/useGitContext'


// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps({
  rowQuantity: { type: Number, required: true },
  className: { type: String, required: false },
  styleOverrides: { type: Object as PropType<CSSProperties>, required: false },
  hasCustomRow: { type: Boolean, required: false, default: false },
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
  grid-template-columns: minmax(20rem, 4fr) minmax(6rem, 1fr) 12rem 6rem;
  grid-template-rows: repeat(var(--git-log-commit-rows), var(--git-log-row-height));
  &.hasHeaders {
    grid-template-rows: var(--git-log-header-row-height) repeat(var(--git-log-commit-rows), var(--git-log-row-height));
  }
}
.customTableContainer {
    margin-top: 0;
}
</style>
