<script setup lang="ts">
import { computed } from 'vue'
import { useGitContext } from '../../composables/useGitContext'
import { formatBranch } from '../../utils/formatBranch'
import type { Commit } from '../../types'
import Link from './Link.vue'
import TagIcon from './TagIcon.vue'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  commit: Commit
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { remoteProviderUrlBuilder } = useGitContext()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const displayName = computed(() => formatBranch(props.commit.branch))

const linkHref = computed(() => {
  if (remoteProviderUrlBuilder.value) {
    return remoteProviderUrlBuilder.value({ commit: props.commit }).branch
  }
  return undefined
})
</script>

<template>
  <template v-if="linkHref">
    <Link
      :href="linkHref"
      :text="displayName"
    />
    <TagIcon />
  </template>

  <template v-else>
    <span class="tagName">
      {{ displayName }}
    </span>
    <TagIcon />
  </template>
</template>

<style scoped lang="scss">
.tagName {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
