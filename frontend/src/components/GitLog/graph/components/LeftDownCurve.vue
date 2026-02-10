<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { CURVE_SIZE, ROW_HEIGHT } from '../../constants'
import { useGitContext } from '../../composables/useGitContext'
import type { BreakPointTheme } from '../../types'
import CurvedEdge from './CurvedEdge.vue'
import BreakPoint from './BreakPoint.vue'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  colour: string
  showBottomBreakPoint?: boolean
  isPlaceholder?: boolean
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { rowSpacing } = useGitContext()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const borderStyle = computed(() => (props.isPlaceholder ? 'dotted' : 'solid'))

const bottomLineStyle = computed<CSSProperties>(() => ({
  bottom: '0',
  left: 'calc(50% - 1px)',
  borderRight: `2px ${borderStyle.value} ${props.colour}`,
  height: `${(ROW_HEIGHT + rowSpacing.value - CURVE_SIZE) / 2}px`,
}))

const leftLineStyle = computed<CSSProperties>(() => ({
  left: '0',
  top: '50%',
  height: '0',
  borderBottom: `2px ${borderStyle.value} ${props.colour}`,
  width: `calc(50% - ${CURVE_SIZE / 2}px)`,
}))

/**
 * Per-theme position overrides for the bottom breakpoint on a LeftDownCurve.
 * The breakpoint sits at the bottom of the curve's vertical extension.
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
    id="left-down-curve"
    data-testid="left-down-curve"
    class="container"
  >
    <BreakPoint
      v-if="showBottomBreakPoint"
      :colour="colour"
      position="bottom"
      :style-overrides="breakPointStyleOverrides"
    />

    <div
      id="left-down-curve-bottom-line"
      data-testid="left-down-curve-bottom-line"
      class="line"
      :style="bottomLineStyle"
    />

    <CurvedEdge
      id="left-down-curve-curved-line"
      :colour="colour"
      :dashed="isPlaceholder"
      path="M 0,53 A 50,50 0 0,1 50,100"
    />

    <div
      id="left-down-curve-left-line"
      data-testid="left-down-curve-left-line"
      class="line"
      :style="leftLineStyle"
    />
  </div>
</template>

<style scoped lang="scss">
.container {
  display: contents;
}

.curve, .line {
  position: absolute;
}
</style>
