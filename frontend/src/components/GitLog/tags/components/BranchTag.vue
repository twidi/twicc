<script setup lang="ts">
import { computed, ref, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'
import { pxToRem } from '../../utils/units'
import { useGitContext } from '../../composables/useGitContext'
import type { Commit } from '../../types'
import BranchLabel from './BranchLabel.vue'
import TagLabel from './TagLabel.vue'
import IndexLabel from './IndexLabel.vue'
import BranchTagTooltip from './BranchTagTooltip.vue'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  commit: Commit
  lineRight: number
  lineWidth: number
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { textColour, shiftAlphaChannel, getCommitColour } = useTheme()
const {
  selectedCommit,
  previewedCommit,
  enablePreviewedCommitStyling,
  enableSelectedCommitStyling,
} = useGitContext()

// ---------------------------------------------------------------------------
// Tooltip visibility
// ---------------------------------------------------------------------------

const showTooltip = ref(false)

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const colour = computed(() => getCommitColour(props.commit))

const isPreviewCommit = computed(() =>
  props.commit.hash === previewedCommit.value?.hash && enablePreviewedCommitStyling.value,
)
const isSelectedCommit = computed(() =>
  props.commit.hash === selectedCommit.value?.hash && enableSelectedCommitStyling.value,
)

const tagLineVars = computed<CSSProperties>(() => ({
  '--tag-line-right': pxToRem(props.lineRight),
  '--tag-line-width': pxToRem(props.lineWidth),
  '--tag-line-border-color': colour.value,
} as CSSProperties))

const tagLabelVars = computed<CSSProperties>(() => {
  const borderColor = isIndex.value
    ? shiftAlphaChannel(colour.value, 0.50)
    : colour.value
  const background = shiftAlphaChannel(colour.value, isIndex.value ? 0.05 : 0.30)

  return {
    '--tag-label-color': textColour.value,
    '--tag-label-border-color': borderColor,
    '--tag-label-background': background,
  } as CSSProperties
})

const isTag = computed(() => props.commit.branch.includes('tags/'))
const isIndex = computed(() => props.commit.hash === 'index')

// ---------------------------------------------------------------------------
// Event handlers
// ---------------------------------------------------------------------------

function handleMouseOver(): void {
  showTooltip.value = true
}

function handleMouseOut(): void {
  showTooltip.value = false
}
</script>

<template>
  <button
    class="tagContainer"
    @blur="handleMouseOut"
    @focus="handleMouseOver"
    @mouseout="handleMouseOut"
    @mouseover="handleMouseOver"
  >
    <span
      :class="['tag', { isIndex }]"
      :style="tagLabelVars"
    >
      <IndexLabel v-if="isIndex" />
      <TagLabel v-else-if="isTag" :commit="commit" />
      <BranchLabel v-else :commit="commit" />
    </span>

    <span
      :class="['tagLine', { isSelected: isSelectedCommit, isPreviewed: isPreviewCommit }]"
      :style="tagLineVars"
    />

    <!-- Tooltip (shown on hover), positioned to the right -->
    <div
      v-if="showTooltip"
      class="tooltipContainer"
    >
      <BranchTagTooltip
        :commit="commit"
      />
    </div>
  </button>
</template>

<style scoped>
.tagContainer {
  all: unset;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  width: 100%;
  height: var(--git-log-row-height);

  .tag {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: .125rem .375rem;
    border-radius: 6px;
    max-width: 8rem;
    font-size: 0.8rem;
    height: min(1.25rem, max(.75rem, calc(var(--git-log-row-height) - .75rem)));
    opacity: 0.8;
    transition: all ease-in-out 0.2s;
    color: var(--tag-label-color);
    border: 2px solid var(--tag-label-border-color);
    background: var(--tag-label-background);

    &.isIndex {
      border-style: dashed;
    }

    &:hover {
      opacity: 1;
    }
  }

  .tagLine {
    top: 50%;
    height: 1px;
    position: absolute;
    right: calc(var(--tag-line-right) - 1px);
    width: var(--tag-line-width);
    border-top: 2px dotted var(--tag-line-border-color);
    opacity: 0.4;
    transition: opacity ease-in-out 0.3s;

    &.isPreviewed {
      opacity: 0.8;
      transition-duration: 0s;
    }

    &.isSelected {
      opacity: 1;
    }
  }
}

.tooltipContainer {
  position: absolute;
  left: 100%;
  top: 50%;
  transform: translateY(-50%);
  z-index: 30;
  pointer-events: none;
  white-space: nowrap;
  margin-left: .5rem;
}
</style>
