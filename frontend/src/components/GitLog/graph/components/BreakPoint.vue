<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useGraphContext } from '../../composables/useGraphContext'
import type { BreakPointTheme } from '../../types'
import styles from './BreakPoint.module.scss'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  position: 'top' | 'bottom'
  colour: string
  styleOverrides?: Partial<Record<BreakPointTheme, CSSProperties>>
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { breakPointTheme } = useGraphContext()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const commonStyles = computed<CSSProperties>(() => ({
  '--breakpoint-colour': props.colour,
} as CSSProperties))

/**
 * Maps the breakpoint theme to the corresponding SCSS module class name
 * and position modifier.
 */
const themeClassMap: Record<BreakPointTheme, string> = {
  'slash': 'Slash',
  'dot': 'Dot',
  'ring': 'Ring',
  'zig-zag': 'ZigZag',
  'line': 'Line',
  'double-line': 'DoubleLine',
  'arrow': 'Arrow',
}

const themeClass = computed(() => {
  const className = themeClassMap[breakPointTheme.value]
  return className
})

const elementClasses = computed(() => [
  styles[themeClass.value],
  styles[`${themeClass.value}--${props.position}`],
])

const elementStyle = computed<CSSProperties>(() => ({
  ...commonStyles.value,
  ...(props.styleOverrides?.[breakPointTheme.value] ?? {}),
}))

const testId = computed(() =>
  `graph-break-point-${breakPointTheme.value}-${props.position}`,
)
</script>

<template>
  <div
    :data-testid="testId"
    :class="elementClasses"
    :style="elementStyle"
  />
</template>
