<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { textColour } = useTheme()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const svgStyle = computed<CSSProperties>(() => ({
  stroke: textColour.value,
}))
</script>

<template>
  <svg
    id="tag-icon"
    class="icon"
    :style="svgStyle"
    viewBox="0 0 24 24"
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
    <line x1="7" y1="7" x2="7.01" y2="7" />
  </svg>
</template>

<style scoped>
.icon {
  width: 17px;
  margin-top: 2px;
  flex: 0 0 auto;
  margin-left: 6px;
}
</style>
