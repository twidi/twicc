<script setup lang="ts">
import { useId, type CSSProperties } from 'vue'
import IndexStatus from './IndexStatus.vue'
import AppTooltip from '../../../AppTooltip.vue'


// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

defineProps<{
  index: number
  isIndex: boolean
  commitMessage: string
  style?: CSSProperties
}>()

const messageId = useId()
</script>

<template>
  <div
    :id="messageId"
    :class="['message', {isIndex}]"
    :style="style"
  >
    {{ commitMessage }}

    <IndexStatus v-if="isIndex" />
  </div>
  <AppTooltip :for="messageId">{{ commitMessage }}</AppTooltip>
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
