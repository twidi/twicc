<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'
import styles from './Link.module.scss'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

defineProps<{
  href?: string
  text: string
  linkClass?: string
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { textColour } = useTheme()

// ---------------------------------------------------------------------------
// Computed values
// ---------------------------------------------------------------------------

const linkStyle = computed<CSSProperties>(() => ({
  color: textColour.value,
}))
</script>

<template>
  <a
    :href="href"
    target="_blank"
    rel="noopener noreferrer"
    :style="linkStyle"
    :class="[styles.link, linkClass]"
  >
    {{ text }}
  </a>
</template>
