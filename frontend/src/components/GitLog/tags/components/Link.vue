<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import { useTheme } from '../../composables/useTheme'

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

defineProps<{
  href?: string
  text: string
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
    class="link"
  >
    {{ text }}
  </a>
</template>

<style scoped>
.link {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-decoration: none;
}
</style>
