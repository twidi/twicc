<script setup>
// Activity indicators shown next to the Orchestration tab's label: zero, one or
// two icons, each only when it has something to say.
//
//   - Sessions: the aggregated badge of the visible sessions spawned under this
//     one (any depth), outside user_turn — the project/workspace cascade
//     (ProcessActivityIndicator), fed with no unread count.
//   - Subagents: the working robot of a subagent tab, as soon as one of this
//     session's subagents runs.
//
// Shared by the center tab strip, the dock tab bars, the gutter chips (and
// their measurement mirrors) and the layout overlay.
import { computed, useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import ProcessActivityIndicator from '../ui/ProcessActivityIndicator.vue'
import ProcessIndicator from '../ui/ProcessIndicator.vue'
import { visibleOrchestrationIndicators } from '../../utils/orchestrationActivity'

const props = defineProps({
    /** Show only one of the two indicators: 'sessions' | 'subagents'. Both by default. */
    only: {
        type: String,
        default: null,
        validator: (value) => value === null || ['sessions', 'subagents'].includes(value),
    },
    /** { sessions: summarizeProcessActivity result or null, subagentsRunning: boolean }, or null. */
    activity: {
        type: Object,
        default: null,
    },
})

const subagentsId = useId()

const visible = computed(() => visibleOrchestrationIndicators(props.activity, props.only))
</script>

<template>
    <span v-if="visible.sessions || visible.subagents" class="orchestration-tab-activity">
        <ProcessActivityIndicator v-if="visible.sessions" :summary="visible.sessions" size="small" />
        <template v-if="visible.subagents">
            <ProcessIndicator :id="subagentsId" state="assistant_turn" size="small" />
            <AppTooltip :for="subagentsId">Subagents working</AppTooltip>
        </template>
    </span>
</template>

<style scoped>
.orchestration-tab-activity {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    /* Breathing room after the label, on top of the tab link's own gap. */
    margin-inline-start: var(--wa-space-2xs);
    flex-shrink: 0;
}
</style>
