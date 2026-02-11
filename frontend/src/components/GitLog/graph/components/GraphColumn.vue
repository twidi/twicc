<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'
import { pxToRem } from '../../utils/units'
import { useSelectCommit } from '../../composables/useSelectCommit'
import { useGraphContext } from '../../composables/useGraphContext'
import { useGitContext } from '../../composables/useGitContext'
import type { Commit } from '../../types'
import type { GraphColumnState } from '../GraphMatrixBuilder/types'
import CommitNode from './CommitNode.vue'
import IndexPseudoCommitNode from './IndexPseudoCommitNode.vue'
import HeadCommitVerticalLine from './HeadCommitVerticalLine.vue'
import VerticalLine from './VerticalLine.vue'
import HorizontalLine from './HorizontalLine.vue'
import ColumnBackground from './ColumnBackground.vue'
import Curve from './Curve.vue'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  /** The zero-based index of this column in its row. */
  index: number

  /** The index of the row in which this column belongs to. */
  rowIndex: number

  /** Details of the commit that is present somewhere on the row. */
  commit: Commit

  /** Details about what is present in this column. */
  state: GraphColumnState

  /** The column index of the commit node in this row. */
  commitNodeIndex: number
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { selectCommitHandler } = useSelectCommit()
const { nodeSize, orientation } = useGraphContext()
const {
  getGraphColumnColour,
  shiftAlphaChannel,
  textColour,
  hoverColour,
  getGraphColumnSelectedBackgroundColour,
} = useTheme()

const {
  showTable,
  headCommit,
  selectedCommit,
  previewedCommit,
  enableSelectedCommitStyling,
  enablePreviewedCommitStyling,
} = useGitContext()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const columnColour = computed(() =>
  props.state.isPlaceholderSkeleton
    ? shiftAlphaChannel(textColour.value, 0.8)
    : getGraphColumnColour(props.index),
)

const isRowCommitIndexNode = computed(() => props.commit.hash === 'index')

const indexCommitNodeBorder = computed(() =>
  shiftAlphaChannel(columnColour.value, 0.5),
)

const rowsCommitMatchesPreviewed = computed(() =>
  previewedCommit.value?.hash === props.commit.hash,
)

const rowsCommitMatchesSelected = computed(() =>
  selectedCommit.value?.hash === props.commit.hash,
)

const rowsCommitIsHead = computed(() =>
  props.commit.hash === headCommit.value?.hash && props.state.isNode,
)

// ---------------------------------------------------------------------------
// Background visibility
// ---------------------------------------------------------------------------

const showPreviewBackground = computed(() => {
  if (!enablePreviewedCommitStyling.value) {
    return false
  }

  const selectedCommitIsNotPreviewed = selectedCommit.value?.hash !== previewedCommit.value?.hash
  const shouldPreview = rowsCommitMatchesPreviewed.value && selectedCommitIsNotPreviewed

  // If we're rendering the table on the right, then we
  // want all columns in this row to render a background
  // so that it lines up with the table row.
  if (showTable.value) {
    return shouldPreview
  }

  // If the table is not rendered on the right, only
  // show the preview background for the node column
  return shouldPreview && props.commitNodeIndex === props.index
})

const showSelectedBackground = computed(() => {
  if (!enableSelectedCommitStyling.value) {
    return false
  }

  // If we're rendering the table on the right, then we
  // want all columns in this row to render a background
  // so that it lines up with the table row.
  if (showTable.value) {
    return rowsCommitMatchesSelected.value
  }

  // If the table is not rendered on the right, only
  // show the selected background for the node column
  return rowsCommitMatchesSelected.value && props.commitNodeIndex === props.index
})

// ---------------------------------------------------------------------------
// Column style
// ---------------------------------------------------------------------------

const columnStyle = computed<CSSProperties>(() => {
  const isCurve = props.state.isLeftDownCurve || props.state.isLeftUpCurve

  if (orientation.value === 'flipped' && isCurve) {
    return {
      minWidth: pxToRem(nodeSize.value),
      transform: 'scale(-1, 1)',
    }
  }

  return {
    minWidth: `${nodeSize.value}px`,
  }
})

// ---------------------------------------------------------------------------
// Selected background colour
// ---------------------------------------------------------------------------

const selectedBackgroundColour = computed(() =>
  props.state.isPlaceholderSkeleton
    ? hoverColour.value
    : getGraphColumnSelectedBackgroundColour(props.commitNodeIndex),
)

// ---------------------------------------------------------------------------
// Event handlers
// ---------------------------------------------------------------------------

function handleClick(): void {
  selectCommitHandler.onClick(props.commit)
}

function handleMouseOver(): void {
  selectCommitHandler.onMouseOver(props.commit)
}

function handleMouseOut(): void {
  selectCommitHandler.onMouseOut()
}
</script>

<template>
  <button
    :id="`graph-column-row-${rowIndex}-col-${index}`"
    :data-testid="`graph-column-row-${rowIndex}-col-${index}`"
    :style="columnStyle"
    :tabindex="rowIndex"
    class="column"
    @click="handleClick"
    @mouseover="handleMouseOver"
    @mouseout="handleMouseOut"
    @focus="handleMouseOver"
    @blur="handleMouseOut"
  >
    <!-- This column contains a node (and it's not the git index pseudo-node) -->
    <CommitNode
      v-if="state.isNode && !isRowCommitIndexNode"
      :commit="commit"
      :colour="columnColour"
      :row-index="rowIndex"
      :column-index="index"
    />

    <!-- This column contains a node (and it's the git index pseudo-node) -->
    <IndexPseudoCommitNode
      v-if="state.isNode && isRowCommitIndexNode"
      :column-colour="columnColour"
      :animate="rowsCommitMatchesPreviewed || rowsCommitMatchesSelected"
    />

    <!-- This column contains the HEAD commit, so draw a vertical line below the node -->
    <HeadCommitVerticalLine
      v-if="state.isVerticalLine && rowsCommitIsHead"
      :column-colour="columnColour"
    />

    <!-- This column contains a vertical branching line (full column height) -->
    <VerticalLine
      v-if="state.isVerticalLine"
      :state="state"
      :commit="commit"
      :column-index="index"
      :column-colour="columnColour"
      :is-index="state.isVerticalIndexLine ?? false"
      :index-commit-node-border="indexCommitNodeBorder"
    />

    <!-- This column contains a horizontal branching line (full column width) -->
    <HorizontalLine
      v-if="state.isHorizontalLine"
      :state="state"
      :column-index="index"
      :column-colour="columnColour"
      :commit-node-index="commitNodeIndex"
    />

    <!-- This column is part of a row that has been selected -->
    <ColumnBackground
      v-if="showSelectedBackground"
      id="selected"
      :index="index"
      :commit-node-index="commitNodeIndex"
      :colour="selectedBackgroundColour"
    />

    <!-- This column is part of a row that has been previewed (via hover) -->
    <ColumnBackground
      v-if="showPreviewBackground"
      id="previewed"
      :index="index"
      :colour="hoverColour"
      :commit-node-index="commitNodeIndex"
    />

    <!-- This column is part of a merge or branching and requires a curve (left edge to bottom edge) -->
    <Curve
      v-if="state.isLeftDownCurve"
      direction="down"
      :colour="columnColour"
      :is-placeholder="state.isPlaceholderSkeleton"
      :show-break-point="state.isBottomBreakPoint"
    />

    <!-- This column is part of a merge or branching and requires a curve (left edge to top edge) -->
    <Curve
      v-if="state.isLeftUpCurve"
      direction="up"
      :colour="columnColour"
      :is-placeholder="state.isPlaceholderSkeleton"
      :show-break-point="state.isTopBreakPoint"
    />
  </button>
</template>

<style scoped lang="scss">
.column {
  all: unset;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;

  &:hover {
    cursor: pointer;
  }
}
</style>
