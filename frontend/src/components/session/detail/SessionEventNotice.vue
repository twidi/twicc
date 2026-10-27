<script setup>
// SessionEventNotice.vue - One session event in the chat: the event line of the Timeline, without its session block.
// The scroller renders one item per event, so each row draws its part of the band that holds a run of events: the
// first row its top, every row its sides, the last row its bottom (the pattern of TimelineEventRow).
import { computed } from 'vue'
import { useSettingsStore } from '../../../stores/settings'
import { describeEvent } from '../../../utils/timeline/describeEvent.js'
import TimelineEventLine from '../../timeline/TimelineEventLine.vue'

const props = defineProps({
    event: { type: Object, required: true },
    context: { type: Object, required: true },
    /** First and last row of a run of events: the band opens and closes there. */
    first: { type: Boolean, default: true },
    last: { type: Boolean, default: true },
})
const settingsStore = useSettingsStore()
const vm = computed(() => describeEvent(props.event, props.context))
</script>

<template>
    <div class="session-event-notice" :class="{ 'session-event-notice--first': first, 'session-event-notice--last': last }" :data-kind="vm.kind">
        <div class="session-event-band">
            <TimelineEventLine :vm="vm" :event-id="event.id" :show-time="settingsStore.areMessageTimestampsShown" />
        </div>
    </div>
</template>

<style scoped>
.session-event-notice {
    padding-inline: var(--wa-space-m);
    min-width: 0;
    max-width: 100%;
    font-size: var(--wa-font-size-s);
}

/* The same room above and below a run: the top edge of the chat, the text of the agent before and after. A
   neighbor that keeps its own margin is compensated below (unscoped block). */
.session-event-notice--first { padding-block-start: var(--card-spacing, var(--wa-space-l)); }
.session-event-notice--last { padding-block-end: var(--card-spacing, var(--wa-space-l)); }

/* The band: the block-card look of the Timeline, drawn in parts across the rows of a run. */
.session-event-band {
    padding-inline: var(--wa-space-s);
    background: var(--block-card-bg);
    border-inline: 1px solid var(--block-card-border);
}
.session-event-notice--first .session-event-band {
    border-block-start: 1px solid var(--block-card-border);
    border-start-start-radius: var(--wa-border-radius-m);
    border-start-end-radius: var(--wa-border-radius-m);
    padding-block-start: var(--wa-space-xs);
}
.session-event-notice--last .session-event-band {
    border-block-end: 1px solid var(--block-card-border);
    border-end-start-radius: var(--wa-border-radius-m);
    border-end-end-radius: var(--wa-border-radius-m);
    padding-block-end: var(--wa-space-xs);
}
</style>

<style>
/* A group toggle ("2 tools") keeps its own margin (GroupToggle.vue): it counts in the room of the run, on both sides. */
.session-items .virtual-scroller-item:has(> .group-toggle:last-child) + .virtual-scroller-item .session-event-notice--first {
    padding-block-start: calc(var(--card-spacing) - var(--wa-space-s));
}
.session-items .virtual-scroller-item:has(+ .virtual-scroller-item > .group-toggle:first-child) .session-event-notice--last {
    padding-block-end: calc(var(--card-spacing) - var(--wa-space-s));
}

/* Before a user message, half the room: the message's own top margin adds the rest. */
.session-items .virtual-scroller-item:has(+ .virtual-scroller-item .session-item[data-kind="user_message"]) .session-event-notice--last {
    padding-block-end: calc(var(--card-spacing) / 2);
}

/* A user message after a notice sits at the distance of a first message, not the larger one it keeps after the
   agent's blocks (SessionItem.vue). */
.session-items .virtual-scroller-item:has(.session-event-notice) + .virtual-scroller-item .session-item[data-kind="user_message"] {
    margin-top: calc(var(--card-spacing) - var(--main-shadow-size));
}
</style>
