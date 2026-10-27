<script setup>
// SessionEventNotice.vue - One session event in the chat: the event line of the Timeline, without its session block.
import { computed } from 'vue'
import { describeEvent } from '../../../utils/timeline/describeEvent.js'
import TimelineEventLine from '../../timeline/TimelineEventLine.vue'

const props = defineProps({ event: { type: Object, required: true }, context: { type: Object, required: true } })
const vm = computed(() => describeEvent(props.event, props.context))
</script>

<template>
    <div class="session-event-notice" :data-kind="vm.kind">
        <TimelineEventLine :vm="vm" :event-id="event.id" instance />
    </div>
</template>

<style scoped>
.session-event-notice {
    padding-inline: var(--wa-space-m);
    /* Room above the first notice of a run: the top edge of the chat, the end of the block before. */
    padding-block-start: var(--card-spacing, var(--wa-space-l));
    min-width: 0;
    max-width: 100%;
    font-size: var(--wa-font-size-s);
}
</style>

<style>
/* Notices of one run keep only the line's own spacing between them. */
.session-items .virtual-scroller-item:has(.session-event-notice) + .virtual-scroller-item .session-event-notice {
    padding-block-start: 0;
}

/* A user message after a notice sits at the distance of a first message, not the larger one it keeps after the
   agent's blocks (SessionItem.vue). */
.session-items .virtual-scroller-item:has(.session-event-notice) + .virtual-scroller-item .session-item[data-kind="user_message"] {
    margin-top: calc(var(--card-spacing) - var(--main-shadow-size));
}
</style>
