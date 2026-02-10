<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'
import type { Commit } from '../../types'
import styles from './CommitNodeTooltip.module.scss'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  commit: Commit
  colour?: string
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { textColour, getTooltipBackground } = useTheme()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const tooltipStyle = computed<CSSProperties>(() => ({
  color: textColour.value,
  border: `2px solid ${props.colour}`,
  background: getTooltipBackground(props.commit),
}))

const parentsText = computed(() =>
  props.commit.parents.length > 0 ? props.commit.parents.join(', ') : 'None',
)

const childrenText = computed(() =>
  props.commit.children.length > 0 ? props.commit.children.join(', ') : 'None',
)
</script>

<template>
  <div
    :id="`commit-node-tooltip-${commit.hash}`"
    :data-testid="`commit-node-tooltip-${commit.hash}`"
    :style="tooltipStyle"
    :class="styles.tooltip"
  >
    <div>
      <p :class="styles.label">Hash:</p>
      <p :class="styles.text">{{ commit.hash }}</p>
    </div>

    <div>
      <p :class="styles.label">Parents:</p>
      <p :class="styles.text">{{ parentsText }}</p>
    </div>

    <div>
      <p :class="styles.label">Children:</p>
      <p :class="styles.text">{{ childrenText }}</p>
    </div>

    <div>
      <p :class="styles.label">Branch Tip:</p>
      <p :class="styles.text">{{ commit.isBranchTip ? 'Yes' : 'No' }}</p>
    </div>
  </div>
</template>
