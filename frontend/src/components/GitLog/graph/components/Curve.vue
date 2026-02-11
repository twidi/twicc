<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { CURVE_SIZE } from '../../constants'
import { pxToRem } from '../../utils/units'
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

const curveBorderStyle = computed<CSSProperties>(() => ({
    border: `2px ${borderStyle.value} ${props.colour}`,
    borderRadius: pxToRem(CURVE_SIZE),
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
    :data-testid="`curve-${direction}`"
    class="container"
  >
    <BreakPoint
      v-if="showBreakPoint"
      :colour="colour"
      :position="breakPointPosition"
      :style-overrides="breakPointStyleOverrides"
    />

    <div :class="['curve', curveClass]"
      :style="curveBorderStyle"
    />

  </div>
</template>

<style scoped lang="scss">
.container {
  display: contents;
}

.curve {
    width: 100%;
    height: 100%;
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
