<script setup lang="ts">
import { computed, useId, type CSSProperties, type PropType } from 'vue'
import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import relativeTime from 'dayjs/plugin/relativeTime'
import { useTableContext } from '../../composables/useTableContext'
import AppTooltip from '../../../../ui/AppTooltip.vue'


// ---------------------------------------------------------------------------
// dayjs plugins
// ---------------------------------------------------------------------------

dayjs.extend(utc)
dayjs.extend(relativeTime)

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps({
  index: { type: Number, required: true },
  timestamp: { type: String, required: true },
  isPlaceholder: { type: Boolean, required: true },
  style: { type: Object as PropType<CSSProperties>, required: false },
})

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { timestampFormat } = useTableContext()

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const timestampId = useId()

const formattedTimestamp = computed(() => {
  const commitDate = dayjs.utc(props.timestamp)

  if (dayjs.utc().diff(commitDate, 'week') >= 1) {
    return commitDate.format(timestampFormat.value)
  }

  return commitDate.fromNow()
})

const fullTimestamp = computed(() => {
  if (props.isPlaceholder) return undefined
  return dayjs.utc(props.timestamp).format('YYYY-MM-DD HH:mm:ss')
})
</script>

<template>
  <div
    :id="timestampId"
    :style="style"
    class="timestamp"
  >
    {{ isPlaceholder ? '-' : formattedTimestamp }}
  </div>
  <AppTooltip v-if="fullTimestamp" :for="timestampId">{{ fullTimestamp }}</AppTooltip>
</template>

<style scoped>
.timestamp {
  font-weight: 400;
}
</style>
