<script setup>
// Activity indicator shown next to the Workflows tab's label: the working
// process indicator while at least one of the session's Workflow runs is live
// (`background_work_in_progress.workflows` of its process state — cleared when
// the process dies, unlike the persisted run rows). Renders nothing otherwise.
//
// Shared by the center tab strip, the dock tab bars, the gutter chips (and
// their measurement mirrors) and the layout overlay.
import { useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import ProcessIndicator from '../ui/ProcessIndicator.vue'
import { pluralize } from '../../utils/backgroundWork'

defineProps({
    /** Number of live Workflow runs, or null/0 when none runs. */
    running: {
        type: Number,
        default: null,
    },
})

const id = useId()
</script>

<template>
    <span v-if="running > 0" class="workflow-tab-activity">
        <ProcessIndicator :id="id" state="assistant_turn" size="small" />
        <AppTooltip :for="id">{{ running === 1 ? 'Workflow running' : `${pluralize(running, 'workflow')} running` }}</AppTooltip>
    </span>
</template>

<style scoped>
.workflow-tab-activity {
    display: inline-flex;
    align-items: center;
    flex-shrink: 0;
    /* Breathing room after the label, on top of the tab link's own gap. */
    margin-inline-start: var(--wa-space-2xs);
}
</style>
