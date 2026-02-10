<script setup lang="ts">
import { CURVE_SIZE } from '../../constants'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

defineProps<{
  id: string
  colour: string
  path: string
  dashed?: boolean
}>()
</script>

<template>
  <svg
    :id="id"
    :data-testid="id"
    :width="CURVE_SIZE"
    :height="CURVE_SIZE"
    viewBox="0 0 100 100"
    class="curve"
    preserveAspectRatio="none"
  >
    <path
      :d="path"
      :stroke="colour"
      stroke-width="2"
      fill="transparent"
      vector-effect="non-scaling-stroke"
      :stroke-dasharray="dashed ? '2 2' : undefined"
    />
  </svg>
</template>

<style scoped lang="scss">
.curve {
  position: absolute;
}
</style>
