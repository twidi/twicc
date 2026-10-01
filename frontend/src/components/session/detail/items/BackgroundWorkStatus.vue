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
            <span class="background-work-status__phrase">{{ line.text }}<span v-if="line.kind === 'shells'" class="background-work-status__dots" aria-hidden="true"><i></i><i></i><i></i></span></span>
        </AgentStatusLine>
    </div>
</template>

<style scoped>
.background-work-status {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

/* Reads like the working line (WorkingAssistantMessage) without its movement: the agent is
   not working, so the phrase keeps the resting colour of that line's shimmer. */
.background-work-status__phrase {
    color: var(--wa-color-text-quiet);
}

/* A running shell shows the working line's three bouncing dots (same rules as
   WorkingAssistantMessage; the keyframes live in glow.css). A cron line, which waits, has none. */
.background-work-status__dots {
    display: inline-flex;
    gap: 0.1875rem;
    /* About a space's width from the text. */
    margin-inline-start: 0.3em;
    color: var(--wa-color-text-quiet);
}
.background-work-status__dots i {
    display: block;
    width: 0.25rem;
    height: 0.25rem;
    border-radius: 50%;
    background: currentColor;
    /* 1.4s: the robot's hop cycle (robot-working.css), as on the working line. */
    animation: glow-live-dot 1.4s var(--motion-ease-out) infinite;
    /* The delayed dots show the first keyframe (0.35) during their delay, not 1. */
    animation-fill-mode: backwards;
}
.background-work-status__dots i:nth-child(2) { animation-delay: 0.15s; }
.background-work-status__dots i:nth-child(3) { animation-delay: 0.3s; }
</style>
