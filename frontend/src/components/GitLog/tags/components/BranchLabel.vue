<script setup lang="ts">
import { computed } from 'vue'
import { useGitContext } from '../../composables/useGitContext'
import { formatBranch } from '../../utils/formatBranch'
import type { Commit } from '../../types'
import Link from './Link.vue'
import BranchIcon from './BranchIcon.vue'
import styles from './BranchLabel.module.scss'

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
  return remoteProviderUrlBuilder.value?.({ commit: props.commit })?.branch
})
</script>

<template>
  <template v-if="linkHref">
    <Link
      :href="linkHref"
      :text="displayName"
      :link-class="styles.branchName"
    />
    <BranchIcon />
  </template>

  <template v-else>
    <span :class="styles.branchName">
      {{ displayName }}
    </span>
    <BranchIcon />
  </template>
</template>
