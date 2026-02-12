<script setup lang="ts">
import { computed } from 'vue'
import { useGitContext } from '../composables/useGitContext'
import type { Commit } from '../types'
import BranchTag from './components/BranchTag.vue'

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const {
  previewedCommit,
  selectedCommit,
  indexCommit,
  graphData,
  paging,
  graphColumnWidth,
  isIndexVisible,
  graphOrientation,
} = useGitContext()

// ---------------------------------------------------------------------------
// Prepare commits — track which tags have been rendered
// ---------------------------------------------------------------------------

interface PreparedCommit extends Commit {
  isMostRecentTagInstance: boolean
}

function prepareCommits(commits: Commit[]): PreparedCommit[] {
  const tagsSeen = new Map<string, boolean>()

  return commits.map(commit => {
    const isTag = commit.branch.includes('tags/')
    const hasBeenRendered = tagsSeen.has(commit.branch)

    const shouldRenderTag = isTag && !hasBeenRendered
    if (shouldRenderTag) {
      tagsSeen.set(commit.branch, true)
    }

    return {
      ...commit,
      isMostRecentTagInstance: shouldRenderTag,
    }
  })
}

// ---------------------------------------------------------------------------
// Visible commits (sliced, with index prepended)
// ---------------------------------------------------------------------------

const preparedCommits = computed<PreparedCommit[]>(() => {
  const data = graphData.value.commits.slice(paging.value?.startIndex, paging.value?.endIndex)

  if (isIndexVisible.value && indexCommit.value) {
    data.unshift(indexCommit.value)
  }

  return prepareCommits(data)
})

// ---------------------------------------------------------------------------
// Tag line width — dotted line connecting the tag badge to its graph column
// ---------------------------------------------------------------------------

function tagLineWidth(commit: Commit): number {
  const isNormalOrientation = graphOrientation.value === 'normal'
  const numberOfColumns = graphData.value.graphColumns
  const columnWidth = graphColumnWidth.value

  if (commit.hash === 'index') {
    return isNormalOrientation
      ? columnWidth / 2
      : ((numberOfColumns - 1) * columnWidth) + (columnWidth / 2)
  }

  const position = graphData.value.positions.get(commit.hash)
  if (!position) {
    return 0
  }

  const columnIndex = position[1]
  const normalisedColumnIndex = isNormalOrientation
    ? columnIndex
    : numberOfColumns - 1 - columnIndex

  return (columnWidth * normalisedColumnIndex) + (columnWidth / 2)
}

// ---------------------------------------------------------------------------
// Determine which commits should show a BranchTag
// ---------------------------------------------------------------------------

function shouldRenderBranchTag(commit: PreparedCommit): boolean {
  const shouldPreviewBranch = previewedCommit.value && commit.hash === previewedCommit.value.hash
  const selectedIsNotTip = selectedCommit.value && commit.hash === selectedCommit.value.hash
  const isIndexCommit = commit.hash === indexCommit.value?.hash

  return commit.isBranchTip
    || !!shouldPreviewBranch
    || !!selectedIsNotTip
    || commit.isMostRecentTagInstance
    || !!isIndexCommit
}
</script>

<template>
  <div class="container">
    <template v-for="(commit, i) in preparedCommits" :key="`tag-${commit.hash}`">
      <BranchTag
        v-if="shouldRenderBranchTag(commit)"
        :commit="commit"
        :line-width="tagLineWidth(commit)"
        :line-right="-tagLineWidth(commit)"
      />
      <div
        v-else
        class="tag"
      />
    </template>
  </div>
</template>

<style scoped>
.container {
  position: relative;
  padding: 0 .5rem;
}

.tag {
  /* Empty tag placeholder — takes up the same row height
  as a BranchTag so the layout stays aligned with the graph. */
  height: var(--git-log-row-height);
}
</style>
