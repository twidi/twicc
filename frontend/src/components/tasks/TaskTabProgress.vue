<script setup>
// Compact task progress shown next to the Tasks tab label: `done/total` while work remains, a green
// check once every task is done. Renders nothing without tasks. Shared by the center tab strip, the
// dock tab bars, the gutter chips (and their measurement mirrors) and the layout overlay.
import { computed, useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    /** Counts from countTasks(): { done, total }, or null. */
    progress: {
        type: Object,
        default: null,
    },
})

const id = useId()

const total = computed(() => props.progress?.total ?? 0)
const allDone = computed(() => total.value > 0 && props.progress.done === total.value)
const label = computed(() => {
    if (!total.value) return ''
    if (allDone.value) return total.value === 1 ? 'Task done' : `All ${total.value} tasks done`
    return `${props.progress.done} of ${total.value} tasks done`
})
</script>

<template>
    <span v-if="total > 0" :id="id" class="task-tab-progress" :class="{ done: allDone }" :aria-label="label">
        <wa-icon v-if="allDone" name="check" class="task-tab-check"></wa-icon>
        <span v-else class="task-tab-count">{{ progress.done }}/{{ total }}</span>
        <AppTooltip :for="id">{{ label }}</AppTooltip>
    </span>
</template>

<style scoped>
.task-tab-progress {
    display: inline-flex;
    align-items: center;
    flex-shrink: 0;
    margin-inline-start: var(--wa-space-2xs);
    font-size: var(--wa-font-size-xs);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}

.task-tab-progress.done {
    color: var(--wa-color-success-60);
}

.task-tab-check {
    font-size: 0.8rem;
}
</style>
