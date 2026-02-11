<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'
import type { Commit } from '../../types'

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
    class="tooltip"
  >
    <div>
      <p class="label">Hash:</p>
      <p class="text">{{ commit.hash }}</p>
    </div>

    <div>
      <p class="label">Parents:</p>
      <p class="text">{{ parentsText }}</p>
    </div>

    <div>
      <p class="label">Children:</p>
      <p class="text">{{ childrenText }}</p>
    </div>

    <div>
      <p class="label">Branch Tip:</p>
      <p class="text">{{ commit.isBranchTip ? 'Yes' : 'No' }}</p>
    </div>
  </div>
</template>

<style scoped>
.tooltip {
  padding: 15px 20px;
  border-radius: 8px;
}

.label {
  font-weight: 600;
  display: inline-block;
  margin: 0 5px 5px 0;
}

.text {
  display: inline;
  margin: 0 0 5px 0;
}
</style>
