<script setup>
import { computed } from 'vue'
import { describeEvent } from '../../../utils/timeline/describeEvent.js'
import TimelineWordingPart from '../../timeline/TimelineWordingPart.vue'

const props = defineProps({ event: { type: Object, required: true }, context: { type: Object, required: true } })
const vm = computed(() => describeEvent(props.event, props.context))
const details = computed(() => {
    const lines = [vm.value.tooltip]
    if (vm.value.detail) lines.push([vm.value.detail.first, vm.value.detail.rest].filter(Boolean).join('\n'))
    const tool = props.event.data?.tool_name
    if (typeof tool === 'string') lines.push(`Tool: ${tool}`)
    return lines.filter(Boolean).join('\n')
})
</script>

<template>
    <div class="session-event-notice" :class="`session-event-notice--${vm.severity}`" :data-kind="vm.kind">
        <div class="session-event-main">
            <wa-icon :name="vm.icon" auto-width aria-hidden="true"></wa-icon>{{ ' ' }}
            <span><TimelineWordingPart v-for="(part, index) in vm.parts" :key="index" :part="part" /></span>
            <span v-for="tag in vm.tags" :key="tag">{{ ' · ' + tag }}</span>
            <span v-if="vm.badge">{{ ' · ' + vm.badge.text }}</span>
            <span v-if="vm.author.parts.length">{{ ' ' }}<TimelineWordingPart v-for="(part, index) in vm.author.parts" :key="index" :part="part" /></span>
            {{ ' ' }}<time :datetime="event.occurred_at" :aria-label="vm.time.full" :title="vm.time.full">{{ vm.time.text }}</time>
        </div>
        <details v-if="details" class="session-event-details">
            <summary>Details</summary>
            <pre>{{ details }}</pre>
        </details>
    </div>
</template>

<style scoped>
.session-event-notice {
    padding: var(--wa-space-s) var(--wa-space-m);
    min-width: 0;
    max-width: 100%;
    text-align: center;
    overflow-wrap: anywhere;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}
.session-event-main { min-width: 0; }
.session-event-main > wa-icon { margin-inline-end: var(--wa-space-2xs); }
.session-event-notice--warning .session-event-main > wa-icon { color: var(--wa-color-warning-on-quiet); }
.session-event-notice--danger .session-event-main > wa-icon { color: var(--wa-color-danger-on-quiet); }
time { margin-inline-start: var(--wa-space-xs); font-variant-numeric: tabular-nums; white-space: nowrap; }
.session-event-details { margin-block-start: var(--wa-space-2xs); }
summary { display: inline list-item; cursor: pointer; font-size: var(--wa-font-size-xs); }
pre { margin-block: var(--wa-space-xs) 0; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; user-select: text; }
</style>
