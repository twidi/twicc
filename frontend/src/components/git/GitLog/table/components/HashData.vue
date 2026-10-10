<script setup lang="ts">
import { computed, type CSSProperties, type PropType } from 'vue'


// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps({
  commitHash: { type: String, required: true },
  isPlaceholder: { type: Boolean, required: true },
  style: { type: Object as PropType<CSSProperties>, required: false },
})

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const shortHash = computed(() => {
  if (props.isPlaceholder) return '-'
  return props.commitHash.slice(0, 7)
})
</script>

<template>
  <div
    :style="style"
    class="hash"
  >
    <code>{{ shortHash }}</code>
  </div>
</template>

<style scoped>
.hash {
  font-weight: 400;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  border-top-right-radius: 10px;
  border-bottom-right-radius: 10px;
}

.hash code {
  font-size: 0.85em;
}
</style>
