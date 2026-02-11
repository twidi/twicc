<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'
import dayjs from 'dayjs'
import utc from 'dayjs/plugin/utc'
import relativeTime from 'dayjs/plugin/relativeTime'
import { useTableContext } from '../../composables/useTableContext'


// ---------------------------------------------------------------------------
// dayjs plugins
// ---------------------------------------------------------------------------

dayjs.extend(utc)
dayjs.extend(relativeTime)

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = defineProps<{
  index: number
  timestamp: string
  isPlaceholder: boolean
  style?: CSSProperties
}>()

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { timestampFormat } = useTableContext()

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const formattedTimestamp = computed(() => {
  const commitDate = dayjs.utc(props.timestamp)

  if (dayjs.utc().diff(commitDate, 'week') >= 1) {
    return commitDate.format(timestampFormat.value)
  }

  return commitDate.fromNow()
})
</script>

<template>
  <div
    :style="style"
    class="timestamp"
    :id="`vue-git-log-table-data-timestamp-${index}`"
  >
    {{ isPlaceholder ? '-' : formattedTimestamp }}
  </div>
</template>

<style scoped>
.timestamp {
  font-weight: 400;
  border-top-right-radius: 10px;
  border-bottom-right-radius: 10px;
}
</style>
