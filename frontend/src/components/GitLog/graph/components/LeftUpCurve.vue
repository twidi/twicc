<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { CURVE_SIZE, ROW_HEIGHT } from '../../constants'
import { useGitContext } from '../../composables/useGitContext'
import type { BreakPointTheme } from '../../types'
import CurvedEdge from './CurvedEdge.vue'
import BreakPoint from './BreakPoint.vue'
import styles from './LeftUpCurve.module.scss'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  colour: string
  showTopBreakPoint?: boolean
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

const topLineStyle = computed<CSSProperties>(() => ({
  top: '0',
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
 * Per-theme position overrides for the top breakpoint on a LeftUpCurve.
 * The breakpoint sits at the top of the curve's vertical extension.
 */
const breakPointStyleOverrides: Partial<Record<BreakPointTheme, CSSProperties>> = {
  'slash': { left: '50%' },
  'arrow': { left: 'calc(50% - 3px)' },
  'dot': { left: '50%' },
  'ring': { left: '50%' },
  'line': { left: 'calc(50% - 6px)' },
  'zig-zag': { left: 'calc(50% - 2px)' },
  'double-line': { left: 'calc(50% - 3px)' },
}
</script>

<template>
  <div
    id="left-up-curve"
    data-testid="left-up-curve"
    :class="styles.container"
  >
    <BreakPoint
      v-if="showTopBreakPoint"
      position="top"
      :colour="colour"
      :style-overrides="breakPointStyleOverrides"
    />

    <div
      id="left-up-curve-top-line"
      data-testid="left-up-curve-top-line"
      :class="styles.line"
      :style="topLineStyle"
    />

    <CurvedEdge
      id="left-up-curve-curved-line"
      :colour="colour"
      :dashed="isPlaceholder"
      path="M 0,53 A 50,50 0 0,0 50,0"
    />

    <div
      id="left-up-curve-left-line"
      data-testid="left-up-curve-left-line"
      :class="styles.line"
      :style="leftLineStyle"
    />
  </div>
</template>
