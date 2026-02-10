<script setup lang="ts">
import { CURVE_SIZE } from '../../constants'
import styles from './CurvedEdge.module.scss'

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
    :class="styles.curve"
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
