<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import type { CommitAuthor } from '../../types'


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
    class="author"
    :id="`vue-git-log-table-data-author-${index}`"
    :data-testid="`vue-git-log-table-data-author-${index}`"
  >
    {{ isPlaceholder ? '-' : authorName }}
  </div>
</template>

<style scoped>
.author {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-right: 30px;
  font-weight: 400;
}
</style>
