<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import type { CommitAuthor } from '../../types'
import styles from './AuthorData.module.scss'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  index: number
  author?: CommitAuthor
  isPlaceholder: boolean
  style?: CSSProperties
}>()

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const authorTitle = computed(() => {
  if (props.author) {
    if (props.author.name && props.author.email) {
      return `${props.author.name} (${props.author.email})`
    }

    if (props.author.name && !props.author.email) {
      return props.author.name
    }

    if (props.author.email && !props.author.name) {
      return props.author.email
    }
  }

  return undefined
})

const authorName = computed(() => {
  if (props.author?.name) {
    return props.author.name
  }

  return ''
})
</script>

<template>
  <div
    :style="style"
    :title="authorTitle"
    :class="styles.author"
    :id="`vue-git-log-table-data-author-${index}`"
    :data-testid="`vue-git-log-table-data-author-${index}`"
  >
    {{ isPlaceholder ? '-' : authorName }}
  </div>
</template>
