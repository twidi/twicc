<script setup lang="ts">
import type { CSSProperties } from 'vue'
import { computed } from 'vue'
import { BACKGROUND_HEIGHT_OFFSET } from '../../constants'
import { pxToRem } from '../../utils/units'
import { useGitContext } from '../../composables/useGitContext'
import { useTheme } from '../../composables/useTheme'
import { useSelectCommit } from '../../composables/useSelectCommit'
import type { Commit, CustomTableRowProps } from '../../types'
import CommitMessageData from './CommitMessageData.vue'
import AuthorData from './AuthorData.vue'
import TimestampData from './TimestampData.vue'


// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = withDefaults(defineProps<{
  index: number
  commit: Commit
  isPlaceholder?: boolean
  rowStyleOverrides?: CSSProperties
  dataStyleOverrides?: CSSProperties
}>(), {
  isPlaceholder: false,
})

// ---------------------------------------------------------------------------
// Slots
// ---------------------------------------------------------------------------

defineSlots<{
  row?(props: CustomTableRowProps): unknown
}>()

// ---------------------------------------------------------------------------
// Contexts and composables
// ---------------------------------------------------------------------------

const {
  textColour,
  hoverColour,
  reduceOpacity,
  getCommitColour,
  shiftAlphaChannel,
} = useTheme()

const { selectCommitHandler } = useSelectCommit()

const {
  nodeSize,
  rowHeight,
  selectedCommit,
  previewedCommit,
  enableSelectedCommitStyling,
  enablePreviewedCommitStyling,
} = useGitContext()

// ---------------------------------------------------------------------------
// Row state
// ---------------------------------------------------------------------------

const isRowSelected = computed(() => selectedCommit.value?.hash === props.commit.hash)
const isRowPreviewed = computed(() => previewedCommit.value?.hash === props.commit.hash)

// ---------------------------------------------------------------------------
// Background colour
// ---------------------------------------------------------------------------

const backgroundColour = computed(() => {
  if (isRowSelected.value && enableSelectedCommitStyling.value) {
    if (props.isPlaceholder) {
      return hoverColour.value
    }

    const colour = getCommitColour(props.commit)
    return reduceOpacity(colour, 0.15)
  }

  if (isRowPreviewed.value && enablePreviewedCommitStyling.value) {
    return hoverColour.value
  }

  return 'transparent'
})

// --------------------------------------------------------- ------------------
// Background gradient style
// ---------------------------------------------------------------------------

const backgroundStyles = computed(() => {
  const height = nodeSize.value + BACKGROUND_HEIGHT_OFFSET
  const padding = (rowHeight.value - height) / 2
  const end = padding + height

  return {
    background: `linear-gradient(
      to bottom,
      transparent ${pxToRem(padding)},
      ${backgroundColour.value} ${pxToRem(padding)},
      ${backgroundColour.value} ${pxToRem(end)},
      transparent ${pxToRem(end)}
    )`,
  }
})

// ---------------------------------------------------------------------------
// Cell styling
// ---------------------------------------------------------------------------

const isMergeCommit = computed(() => props.commit.parents.length > 1)

const shouldReduceOpacity = computed(
  () => !props.isPlaceholder && (isMergeCommit.value || props.commit.hash === 'index'),
)

const shouldRenderHyphenValue = computed(
  () => props.commit.hash === 'index' || props.isPlaceholder,
)

const tableDataStyle = computed<CSSProperties>(() => ({
  lineHeight: 'var(--git-log-row-height)',
  color: shiftAlphaChannel(textColour.value, shouldReduceOpacity.value ? 0.4 : 1),
  ...backgroundStyles.value,
  ...props.dataStyleOverrides,
}))

// ---------------------------------------------------------------------------
// Event handlers
// ---------------------------------------------------------------------------

function handleClick() {
  selectCommitHandler.onClick(props.commit)
}

function handleMouseOver() {
  selectCommitHandler.onMouseOver(props.commit)
}

function handleMouseOut() {
  selectCommitHandler.onMouseOut()
}
</script>

<template>
  <!-- Custom row via scoped slot -->
  <div
    v-if="$slots.row"
    @click="handleClick"
    @mouseover="handleMouseOver"
    @mouseout="handleMouseOut"
  >
    <slot
      name="row"
      :commit="commit"
      :selected="isRowSelected"
      :previewed="isRowPreviewed"
      :background-colour="backgroundColour"
    />
  </div>

  <!-- Default row layout -->
  <div
    v-else
    class="row"
    :style="rowStyleOverrides"
    @click="handleClick"
    @mouseover="handleMouseOver"
    @mouseout="handleMouseOut"
  >
    <CommitMessageData
      :index="index"
      :style="tableDataStyle"
      :commit-message="commit.message"
      :is-index="commit.hash === 'index'"
    />

    <AuthorData
      :index="index"
      :author="commit.author"
      :style="tableDataStyle"
      :is-placeholder="shouldRenderHyphenValue"
    />

    <TimestampData
      :index="index"
      :style="tableDataStyle"
      :timestamp="commit.committerDate"
      :is-placeholder="shouldRenderHyphenValue"
    />
  </div>
</template>

<style scoped>
.row {
  display: contents;

  &:hover {
    cursor: pointer;
  }
}
</style>
