<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import type { BreakPointTheme } from '../../types'
import BreakPoint from './BreakPoint.vue'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  colour: string
  direction: 'up' | 'down'
  showBreakPoint?: boolean
  isPlaceholder?: boolean
}>()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const borderStyle = computed(() => (props.isPlaceholder ? 'dotted' : 'solid'))

const curveBorderVars = computed<CSSProperties>(() => ({
    '--curve-border-style': borderStyle.value,
    '--curve-border-color': props.colour,
}))

const breakPointPosition = computed(() => (props.direction === 'up' ? 'top' : 'bottom'))

const curveClass = computed(() => (props.direction === 'up' ? 'curve-up' : 'curve-down'))

/**
 * Per-theme position overrides for the breakpoint on a Curve.
 * The breakpoint sits at the top (up) or bottom (down) of the curve's vertical extension.
 */
const breakPointStyleOverrides: Partial<Record<BreakPointTheme, CSSProperties>> = {
  'slash': { left: '50%' },
  'dot': { left: '50%' },
  'arrow': { left: 'calc(50% - 3px)' },
  'ring': { left: '50%' },
  'line': { left: 'calc(50% - 6px)' },
  'zig-zag': { left: 'calc(50% - 2px)' },
  'double-line': { left: 'calc(50% - 3px)' },
}
</script>

<template>
  <div
    class="container"
  >
    <BreakPoint
      v-if="showBreakPoint"
      :colour="colour"
      :position="breakPointPosition"
      :style-overrides="breakPointStyleOverrides"
    />

    <div :class="['curve', curveClass]"
      :style="curveBorderVars"
    />

  </div>
</template>

<style scoped>
.container {
  display: contents;
}

.curve {
    width: 100%;
    height: 100%;
    border-radius: var(--git-curve-size);
    border: 2px var(--curve-border-style) var(--curve-border-color);

}

.curve-up {
    translate: calc(-50% + 1px) calc(-50% + 2px);
    clip-path: inset(calc(50% - 1px) 0 0 calc(50% - 2px));
}

.curve-down {
    translate: calc(-50% + 1px) 50%;
    clip-path: inset(0 0 50% calc(50% - 2px));
}
</style>
