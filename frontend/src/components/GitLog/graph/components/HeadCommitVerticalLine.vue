<script setup lang="ts">
import styles from './HeadCommitVerticalLine.module.scss'

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
    :class="[styles.line, styles.vertical]"
  />
</template>
