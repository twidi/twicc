<script setup lang="ts">
import { computed, useId, type CSSProperties, type PropType } from 'vue'
import type { CommitAuthor } from '../../types'
import AppTooltip from '../../../../ui/AppTooltip.vue'


// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps({
  index: { type: Number, required: true },
  author: { type: Object as PropType<CommitAuthor>, required: false },
  isPlaceholder: { type: Boolean, required: true },
  style: { type: Object as PropType<CSSProperties>, required: false },
})

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

const authorId = useId()

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
    :id="authorId"
    class="author"
  >
    {{ isPlaceholder ? '-' : authorName }}
  </div>
  <AppTooltip v-if="authorTitle" :for="authorId">{{ authorTitle }}</AppTooltip>
</template>

<style scoped>
.author {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-right: 1rem;
  font-weight: 400;
}
</style>
