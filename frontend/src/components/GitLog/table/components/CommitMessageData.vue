<script setup lang="ts">
import type { CSSProperties } from 'vue'
import IndexStatus from './IndexStatus.vue'


// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

defineProps<{
  index: number
  isIndex: boolean
  commitMessage: string
  style?: CSSProperties
}>()
</script>

<template>
  <div
    :title="commitMessage"
    :class="['message', {isIndex}]"
    :style="style"
  >
    {{ commitMessage }}

    <IndexStatus v-if="isIndex" />
  </div>
</template>

<style scoped>
.message {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-inline: 1rem;
  font-weight: 400;
  &.isIndex {
    display: flex;
  }
}
</style>
