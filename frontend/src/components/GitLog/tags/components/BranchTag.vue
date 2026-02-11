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
  id: string
  commit: Commit
  height: number
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

const tagLineStyles = computed<CSSProperties>(() => {
  const isPreviewCommit = props.commit.hash === previewedCommit.value?.hash && enablePreviewedCommitStyling.value
  const isSelectedCommit = props.commit.hash === selectedCommit.value?.hash && enableSelectedCommitStyling.value

  const previewOrDefaultOpacity = isPreviewCommit ? 0.8 : 0.4
  const opacity: number = isSelectedCommit ? 1 : previewOrDefaultOpacity

  return {
    opacity,
    right: pxToRem(props.lineRight - 1),
    width: pxToRem(props.lineWidth),
    borderTop: `2px dotted ${colour.value}`,
    animationDuration: isPreviewCommit ? '0s' : '0.3s',
  }
})

const tagLabelContainerStyles = computed<CSSProperties>(() => {
  if (props.commit.hash === 'index') {
    return {
      color: textColour.value,
      border: `2px dashed ${shiftAlphaChannel(colour.value, 0.50)}`,
      background: shiftAlphaChannel(colour.value, 0.05),
    }
  }

  return {
    color: textColour.value,
    border: `2px solid ${colour.value}`,
    background: shiftAlphaChannel(colour.value, 0.30),
  }
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
    :id="`tag-${id}`"
    :style="{ height: pxToRem(height) }"
    class="tagContainer"
    @blur="handleMouseOut"
    @focus="handleMouseOver"
    @mouseout="handleMouseOut"
    @mouseover="handleMouseOver"
  >
    <span
      :id="`tag-label-${id}`"
      class="tag"
      :style="tagLabelContainerStyles"
    >
      <IndexLabel v-if="isIndex" />
      <TagLabel v-else-if="isTag" :commit="commit" />
      <BranchLabel v-else :commit="commit" />
    </span>

    <span
      :id="`tag-line-${id}`"
      class="tagLine"
      :style="tagLineStyles"
    />

    <!-- Tooltip (shown on hover), positioned to the right -->
    <div
      v-if="showTooltip"
      class="tooltipContainer"
    >
      <BranchTagTooltip
        :id="id"
        :commit="commit"
      />
    </div>
  </button>
</template>

<style scoped lang="scss">
.tagContainer {
  all: unset;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  width: 100%;

  .tag {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 2px 6px;
    border-radius: 6px;
    max-width: 140px;
    font-size: 0.8rem;
    height: 22px;
    opacity: 0.8;
    transition: all ease-in-out 0.2s;

    &:hover {
      opacity: 1;
    }
  }

  .tagLine {
    $size: 1px;
    top: 50%;
    height: $size;
    position: absolute;
    transition: opacity ease-in-out 0.3s;
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
  margin-left: 8px;
}
</style>
