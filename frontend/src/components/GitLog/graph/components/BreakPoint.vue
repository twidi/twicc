<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useGraphContext } from '../../composables/useGraphContext'
import type { BreakPointTheme } from '../../types'

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
  themeClass.value,
  `breakpoint-${props.position}`,
])

const elementStyle = computed<CSSProperties>(() => ({
  ...commonStyles.value,
  ...(props.styleOverrides?.[breakPointTheme.value] ?? {}),
}))
</script>

<template>
  <div
    :class="elementClasses"
    :style="elementStyle"
  />
</template>

<style scoped>
.Slash {
  position: absolute;
  width: 14px;
  height: 3px;
  border-radius: 2px;
  background: var(--breakpoint-colour);
  transform: translate(-50%, -50%) rotate(-45deg);
  transform-origin: center;
  left: calc(50% + 2px);

  &.breakpoint-top {
    top: 0;
  }

  &.breakpoint-bottom {
    top: 100%;
  }
}

.Dot {
  position: absolute;
  --dot-size: 8px;
  width: var(--dot-size);
  height: var(--dot-size);
  border-radius: 50%;
  background: var(--breakpoint-colour);
  transform: translate(-50%, -50%);
  left: calc(50% + 2px);

  &.breakpoint-top {
    top: 0;
  }

  &.breakpoint-bottom {
    top: 100%;
  }
}

.Ring {
  position: absolute;
    --ring-size: 6px;
  width: var(--ring-size);
  height: var(--ring-size);
  border-radius: 50%;
  border: 2px solid var(--breakpoint-colour);
  transform: translate(-50%, -50%);
  transform-origin: center;
  left: calc(50% + 2px);

  &.breakpoint-top {
    top: -4px;
  }

  &.breakpoint-bottom {
    top: calc(100% + 4px);
  }
}

.ZigZag {
  position: absolute;

  &::before {
    position: absolute;
    content: '';
    width: 5px;
    height: 2px;
    background: var(--breakpoint-colour);
    transform: translate(-50%, -50%) rotate(-50deg);
    transform-origin: center;
  }

  &::after {
    position: absolute;
    content: '';
    width: 5px;
    height: 2px;
    background: var(--breakpoint-colour);
    transform: translate(-50%, -50%) rotate(50deg);
    transform-origin: center;
  }

  &.breakpoint-top {
    top: 0;

    &::before {
      top: -1px;
      left: calc(50% + 4px);
    }

    &::after {
      top: -5px;
      left: calc(50% + 5px);
    }
  }

  &.breakpoint-bottom {
    top: 100%;

    &::before {
      top: 2px;
      left: calc(50% + 2px);
    }

    &::after {
      top: 6px;
      left: 50%;
    }
  }
}

.Line {
  position: absolute;
  width: 12px;
  height: 2px;
  border-radius: 10px;
  background: var(--breakpoint-colour);
  left: calc(50% - 4px);

  &.breakpoint-top {
    top: 0;
  }

  &.breakpoint-bottom {
    top: 100%;
  }
}

.DoubleLine {
  position: absolute;

  &::before {
    position: absolute;
    content: '';
    width: 14px;
    height: 2px;
    border-radius: 10px;
    background: var(--breakpoint-colour);
    left: calc(50% - 4px);
  }

  &::after {
    position: absolute;
    content: '';
    width: 8px;
    height: 1px;
    border-radius: 10px;
    background: var(--breakpoint-colour);
    left: calc(50% - 1px);
  }

  &.breakpoint-top {
    top: 0;

    &::after {
      top: -4px;
    }
  }

  &.breakpoint-bottom {
    top: 100%;

    &::after {
      top: 4px;
    }
  }
}

.Arrow {
  position: absolute;

  --arrow-width: 8px;
  --arrow-height: 3px;

  &::before {
    position: absolute;
    content: '';
    width: var(--arrow-width);
    height: var(--arrow-height);
    background: var(--breakpoint-colour);
    border-radius: var(--arrow-height);
  }

  &::after {
    position: absolute;
    content: '';
    width: var(--arrow-width);
    height: var(--arrow-height);
    background: var(--breakpoint-colour);
    border-radius: var(--arrow-height);
  }

  &.breakpoint-top {
    top: -5px;

    &::before {
      top: 5px;
      left: calc(50% - 3px);
      transform: rotate(-45deg);
    }

    &::after {
      top: 5px;
      left: calc(50% + 1px);
      transform: rotate(45deg);
    }
  }

  &.breakpoint-bottom {
    top: 90%;

    &::before {
      top: -3px;
      left: calc(50% - 3px);
      transform: rotate(45deg);
    }

    &::after {
      top: -3px;
      left: calc(50% + 1px);
      transform: rotate(-45deg);
    }
  }
}
</style>
