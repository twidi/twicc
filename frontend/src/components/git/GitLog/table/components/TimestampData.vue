<script setup lang="ts">
import { computed, useId, type CSSProperties, type PropType } from 'vue'
import { useI18n } from 'vue-i18n'
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
const { locale } = useI18n()

function formatTimestamp() {
  const date = new Date(props.timestamp)
  if (!Number.isFinite(date.getTime())) return '-'
  return new Intl.DateTimeFormat(locale.value, {
    dateStyle: 'short', timeStyle: 'medium', timeZone: 'UTC',
  }).format(date)
}

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

const timestampId = useId()

const formattedTimestamp = computed(() => {
  const commitDate = dayjs.utc(props.timestamp).locale(({ 'pt-BR': 'pt-br', de: 'de', es: 'es', fr: 'fr' })[locale.value] ?? 'en')
  if (!commitDate.isValid()) return '-'

  if (dayjs.utc().diff(commitDate, 'week') >= 1) {
    return timestampFormat.value && timestampFormat.value !== 'YYYY-MM-DD HH:mm:ss'
      ? commitDate.format(timestampFormat.value)
      : formatTimestamp()
  }

  return commitDate.fromNow()
})

const fullTimestamp = computed(() => {
  if (props.isPlaceholder) return undefined
  return formatTimestamp()
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
