<script setup lang="ts">
import { computed, ref, type CSSProperties } from 'vue'
import { NODE_BORDER_WIDTH } from '../../constants'
import { pxToRem } from '../../utils/units'
import { useTheme } from '../../composables/useTheme'
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

const commitHashLabelHeight = 20

const isMergeCommit = computed(() =>
  nodeTheme.value === 'default' && props.commit.parents.length > 1,
)

const commitUrl = computed(() =>
  remoteProviderUrlBuilder.value?.({ commit: props.commit })?.commit,
)

const commitNodeColours = computed(() =>
  getCommitNodeColours({ columnColour: props.colour }),
)

const nodeStyles = computed<CSSProperties>(() => ({
  width: pxToRem(nodeSize.value),
  height: pxToRem(nodeSize.value),
  backgroundColor: commitNodeColours.value.backgroundColour,
  border: `${NODE_BORDER_WIDTH}px solid ${commitNodeColours.value.borderColour}`,
}))

const mergeInnerNodeStyles = computed<CSSProperties>(() => {
  const diameter = getMergeNodeInnerSize({ nodeSize: nodeSize.value })
  return {
    background: commitNodeColours.value.borderColour,
    width: pxToRem(diameter),
    height: pxToRem(diameter),
    top: `calc(50% - ${pxToRem(diameter / 2)})`,
    left: `calc(50% - ${pxToRem(diameter / 2)})`,
  }
})

const commitLabelStyles = computed<CSSProperties>(() => ({
  color: textColour.value,
  height: pxToRem(commitHashLabelHeight),
  left: `calc(50% + ${pxToRem(nodeSize.value / 2 + 5)})`,
  top: `calc(50% - ${pxToRem(commitHashLabelHeight / 2)})`,
  background: theme.value === 'dark' ? 'rgb(26,26,26)' : 'white',
}))

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
    :data-testid="`commit-node-${commit.hash}`"
    :style="nodeStyles"
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
      :data-testid="`commit-node-merge-circle-${commit.hash}`"
      :style="mergeInnerNodeStyles"
      class="mergeCommitInner"
    />

    <!-- Commit hash label -->
    <span
      v-if="showCommitNodeHashes"
      :id="`commit-node-hash-${commit.hash}`"
      :data-testid="`commit-node-hash-${commit.hash}`"
      class="commitLabel"
      :style="commitLabelStyles"
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
  border-radius: 50%;
  z-index: 20;
  position: relative;

  &:hover {
    cursor: pointer;
  }

  .commitLabel {
    position: absolute;
    padding: 2px 5px 2px 2px;
    border-radius: 5px;
    font-size: 0.7rem;
  }
}

.mergeCommitInner {
  border-radius: 50%;
  position: absolute;
}

.tooltipContainer {
  position: absolute;
  bottom: 100%;
  left: 50%;
  transform: translateX(-50%);
  z-index: 30;
  pointer-events: none;
  white-space: nowrap;
  margin-bottom: 8px;
}
</style>
