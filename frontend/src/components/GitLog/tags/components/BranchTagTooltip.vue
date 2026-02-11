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

const tooltipVars = computed<CSSProperties>(() => ({
  '--tooltip--background-color': getTooltipBackground(props.commit),
  '--tooltip-border-color': getCommitColour(props.commit),
}))
</script>

<template>
  <div
    :id="`tag-${id}-tooltip`"
    class="tooltip"
    :style="tooltipVars"
  >
    {{ commit.branch }}
  </div>
</template>

<style scoped>
.tooltip {
  padding: .125rem .5rem;
  border-radius: 4px;
  font-size: 0.8rem;
  color: var(--git-text-color);
  background: var(--tooltip--background-color);
  border: 2px solid var(--tooltip-border-color);
}
</style>
