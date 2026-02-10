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
    class="message"
    :id="`vue-git-log-table-data-commit-message-${index}`"
    :style="{ ...style, display: isIndex ? 'flex' : undefined }"
    :data-testid="`vue-git-log-table-data-commit-message-${index}`"
  >
    {{ commitMessage }}

    <IndexStatus v-if="isIndex" />
  </div>
</template>

<style scoped lang="scss">
.message {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-right: 30px;
  padding-left: 20px;
  font-weight: 400;
}
</style>
