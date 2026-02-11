<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'
import type { Commit } from '../../types'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  id: string
  commit: Commit
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { textColour, getTooltipBackground, getCommitColour } = useTheme()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const tooltipStyle = computed<CSSProperties>(() => ({
  color: textColour.value,
  background: getTooltipBackground(props.commit),
  border: `2px solid ${getCommitColour(props.commit)}`,
}))
</script>

<template>
  <div
    :id="`tag-${id}-tooltip`"
    class="tooltip"
    :style="tooltipStyle"
  >
    {{ commit.branch }}
  </div>
</template>

<style scoped>
.tooltip {
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 0.8rem;
}
</style>
