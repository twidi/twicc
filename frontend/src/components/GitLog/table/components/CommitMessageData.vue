<script setup lang="ts">
import type { CSSProperties } from 'vue'
import IndexStatus from './IndexStatus.vue'
import styles from './CommitMessageData.module.scss'

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
    :class="styles.message"
    :id="`vue-git-log-table-data-commit-message-${index}`"
    :style="{ ...style, display: isIndex ? 'flex' : undefined }"
    :data-testid="`vue-git-log-table-data-commit-message-${index}`"
  >
    {{ commitMessage }}

    <IndexStatus v-if="isIndex" />
  </div>
</template>
