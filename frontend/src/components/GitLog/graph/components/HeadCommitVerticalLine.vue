<script setup lang="ts">

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

defineProps<{
  columnColour: string
}>()
</script>

<template>
  <div
    id="head-commit-vertical-line"
    data-testid="head-commit-vertical-line"
    :style="{
      height: '50%',
      top: '50%',
      borderRight: `2px solid ${columnColour}`,
    }"
    :class="['line', 'vertical']"
  />
</template>

<style scoped>
.line {
  position: absolute;
}

.vertical {
  left: calc(50% - 3px);
  width: 2px;
  z-index: 10;
}
</style>
