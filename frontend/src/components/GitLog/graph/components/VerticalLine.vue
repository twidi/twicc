<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useGraphContext } from '../../composables/useGraphContext'
import { useGitContext } from '../../composables/useGitContext'
import type { Commit, BreakPointTheme } from '../../types'
import type { GraphColumnState } from '../GraphMatrixBuilder'
import BreakPoint from './BreakPoint.vue'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  state: GraphColumnState
  columnIndex: number
  columnColour: string
  commit: Commit
  indexCommitNodeBorder: string
  isIndex: boolean
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { breakPointTheme } = useGraphContext()
const { headCommit, isServerSidePaginated, headCommitHash, isIndexVisible } = useGitContext()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const lineColour = computed(() =>
  props.isIndex ? props.indexCommitNodeBorder : props.columnColour,
)

const isRowCommitIndexNode = computed(() => props.commit.hash === 'index')

const rowsCommitIsHead = computed(() =>
  props.commit.hash === headCommit.value?.hash && props.state.isNode,
)

const border = computed<CSSProperties>(() => {
  const borderStyle = props.isIndex || props.state.isPlaceholderSkeleton ? 'dotted' : 'solid'

  if (
    (props.state.isLastRow || props.state.isFirstRow) &&
    !props.state.isVerticalIndexLine &&
    !props.commit.isBranchTip
  ) {
    const direction = props.state.isLastRow ? 'bottom' : 'top'
    return {
      borderRight: '2px solid transparent',
      borderImage: `linear-gradient(to ${direction}, ${lineColour.value}, transparent) 1`,
    }
  }

  return {
    borderRight: `2px ${borderStyle} ${lineColour.value}`,
  }
})

const indexPseudoNodeLine = computed<{ variant: string; style: CSSProperties }>(() => {
  if (props.state.isBottomBreakPoint) {
    return {
      variant: 'bottom-half-dotted-with-break-point',
      style: {
        top: '50%',
        height: '70%',
        zIndex: props.columnIndex + 1,
        borderRight: `2px dotted ${props.indexCommitNodeBorder}`,
      },
    }
  }

  return {
    variant: 'bottom-half-dotted',
    style: {
      top: '50%',
      height: '50%',
      zIndex: props.columnIndex + 1,
      borderRight: `2px dotted ${props.indexCommitNodeBorder}`,
    },
  }
})

const lineConfig = computed<{ variant: string; style: CSSProperties }>(() => {
  if (isRowCommitIndexNode.value) {
    return indexPseudoNodeLine.value
  }

  if (rowsCommitIsHead.value && isIndexVisible.value) {
    return {
      variant: 'top-half-dotted',
      style: {
        borderRight: `2px dotted ${props.indexCommitNodeBorder}`,
      },
    }
  }

  const vars = {
      '--line-zIndex': props.columnIndex + 1,
      ...border.value,
  }

  const isFirstCommit = props.state.isNode && props.commit.parents.length === 0
  if (isFirstCommit || props.state.isColumnBelowEmpty) {
    if (props.state.isTopBreakPoint) {
      return {
        variant: 'top-half-with-break-point',
        style: vars,
      }
    }

    return {
      variant: 'top-half',
      style: vars,
    }
  }

  const isBranchTip = isServerSidePaginated.value
    ? props.commit.hash === headCommitHash.value
    : props.commit.isBranchTip && props.state.isNode

  if (isBranchTip || props.state.isColumnAboveEmpty) {
    return {
      variant: 'bottom-half',
      style: {
          ...vars,
          '--line-top': '50%',
      },
    }
  }

  if (props.state.isBottomBreakPoint) {
    return {
      variant: 'bottom-break-point',
      style: {
        ...vars,
        '--line-height': breakPointTheme.value === 'ring' ? '30%' : '50%',
      },
    }
  }

  if (props.state.isTopBreakPoint) {
    return {
      variant: 'top-break-point',
      style: {
          ...vars,
          '--line-height': '100%',
      },
    }
  }

  return {
    variant: 'full-height',
    style: {
      ...vars,
      '--line-height': '100%',
    },
  }
})

/**
 * Style overrides for the breakpoint when it sits on the index pseudo-node.
 * The dotted style is applied to the slash theme to match the index node's
 * visual treatment.
 */
const indexBreakPointStyleOverrides = computed<Partial<Record<BreakPointTheme, CSSProperties>>>(() => ({
  slash: {
    left: 'calc(50% + 1px)',
    width: '20px',
    background: 'none',
    borderRadius: 'unset',
    borderBottom: `3px dotted ${props.indexCommitNodeBorder}`,
  },
  ring: {
    borderStyle: 'dotted',
  },
}))
</script>

<template>
  <div
    :class="['line', 'vertical']"
    :style="lineConfig.style"
  >
    <!-- Bottom breakpoint for normal commits -->
    <BreakPoint
      v-if="state.isBottomBreakPoint && !isRowCommitIndexNode"
      position="bottom"
      :colour="lineColour"
    />

    <!-- Bottom breakpoint for index pseudo-node (dotted style overrides) -->
    <BreakPoint
      v-if="state.isBottomBreakPoint && isRowCommitIndexNode"
      position="bottom"
      :colour="indexCommitNodeBorder"
      :style-overrides="indexBreakPointStyleOverrides"
    />

    <!-- Top breakpoint -->
    <BreakPoint
      v-if="state.isTopBreakPoint"
      position="top"
      :colour="lineColour"
    />
  </div>
</template>

<style scoped>
.line {
  position: absolute;
}

.vertical {
  top: var(--line-top, 0);
  height: var(--line-height, 50%);
  left: calc(50% - 3px);
  width: 2px;
  z-index: var(--line-zIndex, 10);
}
</style>
