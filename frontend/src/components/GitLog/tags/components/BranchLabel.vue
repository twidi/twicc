<script setup lang="ts">
import { computed } from 'vue'
import { useGitContext } from '../../composables/useGitContext'
import { parseBranch } from '../../utils/formatBranch'
import type { Commit } from '../../types'
import Link from './Link.vue'
import BranchIcon from './BranchIcon.vue'

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

const parsed = computed(() => parseBranch(props.commit.branch))
const isRemote = computed(() => parsed.value.isRemote)
const remoteName = computed(() => parsed.value.remote)

// For names like "feature/git-graph-visualization", show "…/git-graph-visualization"
// so the meaningful part is visible when truncated by CSS.
const hasPrefix = computed(() => parsed.value.name.includes('/'))
const displayName = computed(() => {
  const name = parsed.value.name
  const idx = name.indexOf('/')
  return idx >= 0 ? name.slice(idx + 1) : name
})

const linkHref = computed(() => {
  return remoteProviderUrlBuilder.value?.({ commit: props.commit })?.branch
})
</script>

<template>
  <template v-if="linkHref">
    <Link
      :href="linkHref"
      :text="displayName"
    />
    <BranchIcon />
  </template>

  <template v-else>
    <span class="branchName" :class="{ isRemote }">
      <span v-if="isRemote" class="remotePrefix">{{ remoteName }}/</span><span v-if="hasPrefix" class="ellipsisPrefix">&hellip;/</span>{{ displayName }}
    </span>
    <BranchIcon />
  </template>
</template>

<style scoped>
.branchName {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.branchName.isRemote {
  font-style: italic;
  opacity: 0.7;
}

.remotePrefix {
  opacity: 0.6;
}
</style>
