<script setup lang="ts">
import { computed, ref, type CSSProperties } from 'vue'
import { pxToRem } from '../../utils/units'
import { useTheme, shiftAlphaChannel } from '../../composables/useTheme'
import { useSelectCommit } from '../../composables/useSelectCommit'
import { useGraphContext } from '../../composables/useGraphContext'
import { useGitContext } from '../../composables/useGitContext'
import { getMergeNodeInnerSize } from '../utils/getMergeNodeInnerSize'
import type { Commit, CustomCommitNodeProps, CustomTooltipProps } from '../../types'
import CommitNodeTooltip from './CommitNodeTooltip.vue'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  commit: Commit
  colour: string
  rowIndex: number
  columnIndex: number
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { selectCommitHandler } = useSelectCommit()
const { remoteProviderUrlBuilder } = useGitContext()
const { textColour, theme, getCommitNodeColours } = useTheme()
const { showCommitNodeTooltips, showCommitNodeHashes, nodeTheme, nodeSize, node, tooltip } = useGraphContext()

// ---------------------------------------------------------------------------
// Tooltip visibility
// ---------------------------------------------------------------------------

const showTooltip = ref(false)

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const isMergeCommit = computed(() =>
  nodeTheme.value === 'default' && props.commit.parents.length > 1,
)

const commitUrl = computed(() =>
  remoteProviderUrlBuilder.value?.({ commit: props.commit })?.commit,
)

const commitNodeColours = computed(() =>
  getCommitNodeColours({ columnColour: props.colour }),
)

const nodeVars = computed<CSSProperties>(() => ({
  '--commit-node-background-color':  commitNodeColours.value.backgroundColour,
  '--commit-node-border-color': commitNodeColours.value.borderColour,
}))

const mergeInnerNodeVars = computed<CSSProperties>(() => {
  const diameter = pxToRem(getMergeNodeInnerSize({ nodeSize: nodeSize.value }));
  return {
    '--merge-commit-diameter': diameter,
    '--merge-commit-background-color': commitNodeColours.value.borderColour,
  }
})

const commitLabelVars = computed<CSSProperties>(() => {
    return {
        '--commit-label--background-color': commitNodeColours.value.backgroundColour
    }
})

const isTooltipVisible = computed(() =>
  showCommitNodeTooltips.value && showTooltip.value,
)

// ---------------------------------------------------------------------------
// Custom node slot props
// ---------------------------------------------------------------------------

const customNodeProps = computed<CustomCommitNodeProps>(() => ({
  commit: props.commit,
  colour: props.colour,
  rowIndex: props.rowIndex,
  columnIndex: props.columnIndex,
  nodeSize: nodeSize.value,
  isIndexPseudoNode: false,
}))

// ---------------------------------------------------------------------------
// Custom tooltip slot props
// ---------------------------------------------------------------------------

const customTooltipProps = computed<CustomTooltipProps>(() => ({
  commit: props.commit,
  borderColour: commitNodeColours.value.borderColour,
  backgroundColour: commitNodeColours.value.backgroundColour,
}))

// ---------------------------------------------------------------------------
// Event handlers
// ---------------------------------------------------------------------------

function handleMouseOver(): void {
  showTooltip.value = true
  selectCommitHandler.onMouseOver(props.commit)
}

function handleMouseOut(): void {
  showTooltip.value = false
  selectCommitHandler.onMouseOut()
}

function handleClick(): void {
  selectCommitHandler.onClick(props.commit)

  if (commitUrl.value) {
    window.open(commitUrl.value, '_blank')
  }
}

function handleKeyDown(event: KeyboardEvent): void {
  if (event.key === 'Enter') {
    event.preventDefault()
    handleClick()
  }
}
</script>

<template>
  <!-- Custom node wrapped in a transparent interactive container -->
  <div
    v-if="node"
    class="customNodeWrapper"
    role="button"
    :tabindex="0"
    :title="commitUrl ? 'View Commit' : undefined"
    @click.stop="handleClick"
    @mouseover.stop="handleMouseOver"
    @mouseout.stop="handleMouseOut"
    @focus.stop="handleMouseOver"
    @blur.stop="handleMouseOut"
    @keydown.stop="handleKeyDown"
  >
    <component :is="() => node!(customNodeProps)" />
  </div>

  <!-- Default node rendering -->
  <div
    v-else
    role="button"
    :tabindex="0"
    :id="`commit-node-${commit.hash}`"
    :style="nodeVars"
    class="commitNode"
    :title="commitUrl ? 'View Commit' : undefined"
    @click.stop="handleClick"
    @mouseover.stop="handleMouseOver"
    @mouseout.stop="handleMouseOut"
    @focus.stop="handleMouseOver"
    @blur.stop="handleMouseOut"
    @keydown.stop="handleKeyDown"
  >
    <!-- Merge commit inner circle -->
    <div
      v-if="isMergeCommit"
      :id="`commit-node-merge-circle-${commit.hash}`"
      :style="mergeInnerNodeVars"
      class="mergeCommitInner"
    />

    <!-- Commit hash label -->
    <span
      v-if="showCommitNodeHashes"
      :id="`commit-node-hash-${commit.hash}`"
      :style="commitLabelVars"
      class="commitLabel"
    >
      {{ commit.hash }}
    </span>

    <!-- Tooltip (shown on hover) -->
    <div
      v-if="isTooltipVisible"
      class="tooltipContainer"
    >
      <!-- Custom tooltip via graph context render function -->
      <component
        v-if="tooltip"
        :is="() => tooltip!(customTooltipProps)"
      />

      <!-- Default tooltip -->
      <CommitNodeTooltip
        v-else
        :commit="commit"
        :colour="commitNodeColours.borderColour"
      />
    </div>
  </div>
</template>

<style scoped>
.customNodeWrapper {
  all: unset;
  z-index: 20;
  position: relative;

  &:hover {
    cursor: pointer;
  }
}

.commitNode {
  all: unset;
  width: var(--git-node-size);
  height: var(--git-node-size);
  border-radius: 50%;
  z-index: 20;
  position: relative;
  background-color: var(--commit-node-background-color);
  border: var(--git-node-border-width) solid var(--commit-node-border-color);

  &:hover {
    cursor: pointer;
  }

  .commitLabel {
    position: absolute;
    padding: 0.0625rem 0.1875rem;
    border-radius: 5px;
    font-size: 0.7rem;
    color: var(--git-text-color);
    left: 50%;
    top: 50%;
    translate: -50% -50%;
    line-height: normal;
    background: var(--commit-label--background-color);
    opacity: 0.7;
  }
}

.mergeCommitInner {
  border-radius: 50%;
  position: absolute;
  background: var(--merge-commit-background-color);
  width: var(--merge-commit-diameter);
  height: var(--merge-commit-diameter);
  top: calc(50% - var(--merge-commit-diameter) / 2);
  left: calc(50% - var(--merge-commit-diameter) / 2);
}

.tooltipContainer {
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  z-index: 30;
  pointer-events: none;
  white-space: nowrap;
  margin-bottom: .5rem;
}
</style>
