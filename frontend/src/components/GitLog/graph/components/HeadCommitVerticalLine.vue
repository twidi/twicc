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
    :style="{'--vertical-line-color': columnColour}"
    :class="['line', 'vertical']"
  />
</template>

<style scoped>
.line {
  position: absolute;
}

.vertical {
  height: 50%;
  top: 50%;
  border-right: 2px solid var(--vertical-line-color);
  left: calc(50% - 3px);
  width: 2px;
  z-index: 10;
}
</style>
