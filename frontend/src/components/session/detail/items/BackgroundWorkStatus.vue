<script setup>
/**
 * BackgroundWorkStatus - Status line at the bottom of a USER_TURN session:
 * what still runs behind the finished turn (a background shell the agent left
 * running, active crons). One row per kind of work.
 *
 * Reads like WorkingAssistantMessage (same AgentStatusLine layout, same
 * ProcessIndicator icon) but static: the USER_TURN variants of the indicator
 * (terminal, clock) are never animated — the agent is not working.
 *
 * The lines are built by the store (utils/backgroundWork.js
 * `buildBackgroundWorkStatusLines`), so this component needs no store access.
 */
import { PROCESS_STATE } from '../../../../constants'
import ProcessIndicator from '../../../ui/ProcessIndicator.vue'
import AgentStatusLine from './AgentStatusLine.vue'

defineProps({
    /** `[{kind: 'shells' | 'crons', text}]` — one row per kind of background work. */
    lines: { type: Array, default: () => [] },
})
</script>

<template>
    <div class="background-work-status text-content">
        <AgentStatusLine v-for="line in lines" :key="line.kind">
            <ProcessIndicator
                :state="PROCESS_STATE.USER_TURN"
                size="small"
                :animate-states="[]"
                :background-shells="line.kind === 'shells' ? 1 : 0"
                :has-active-crons="line.kind === 'crons'"
            />
            <span>{{ line.text }}</span>
        </AgentStatusLine>
    </div>
</template>

<style scoped>
.background-work-status {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}
</style>
