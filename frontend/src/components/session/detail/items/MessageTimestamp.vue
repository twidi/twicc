<script setup>
/**
 * MessageTimestamp — the time of a user or assistant message, rendered at the
 * very bottom of the message (after the markdown).
 *
 * The always-visible label is a fixed 24-hour "HH:MM" (local time). It is
 * deliberately NOT tied to the "Time display" setting and never relative, so it
 * needs no periodic refresh (a relative label would mean a live ticker on every
 * message). Day-boundary disambiguation is left for later; the exact moment is
 * always available in a tooltip, which shows the full classic local date-time
 * down to the second ("YYYY-MM-DD HH:MM:SS"). It opens on hover/focus with a
 * pointer, and on tap on a touch device (no hover there).
 *
 * Renders nothing when no real timestamp is available (e.g. synthetic /
 * optimistic / streaming placeholders).
 */
import { computed, useId } from 'vue'
import { formatClockTime, formatFullDateTime } from '../../../../utils/date'
import { useSettingsStore } from '../../../../stores/settings'
import AppTooltip from '../../../ui/AppTooltip.vue'

const props = defineProps({
    // ISO 8601 timestamp string from the parsed JSONL line (both providers emit
    // a top-level `timestamp`). Null/absent for synthetic placeholders.
    timestamp: {
        type: String,
        default: null,
    },
})

// Epoch ms parsed from the ISO string (NaN when invalid/absent).
const timestampMs = computed(() => (props.timestamp ? Date.parse(props.timestamp) : Number.NaN))
const hasTimestamp = computed(() => Number.isFinite(timestampMs.value))

const clockLabel = computed(() => formatClockTime(timestampMs.value))
const fullLabel = computed(() => formatFullDateTime(timestampMs.value))

const settingsStore = useSettingsStore()
const timestampId = `message-timestamp-${useId()}`
// No hover on a touch device: a tap opens the tooltip (AppTooltip closes it on the next outside tap).
const tooltipTrigger = computed(() => (settingsStore.isTouchDevice ? 'click' : 'hover focus'))
</script>

<template>
    <!-- The tooltip lives inside the div: SessionItem's CSS relies on .message-timestamp being the last child. -->
    <div v-if="hasTimestamp" :id="timestampId" class="message-timestamp">
        {{ clockLabel }}
        <AppTooltip :for="timestampId" :trigger="tooltipTrigger" force>{{ fullLabel }}</AppTooltip>
    </div>
</template>

<style scoped>
.message-timestamp {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-variant-numeric: tabular-nums;
    width: fit-content;
    /* Always pinned to the right edge of the message (user and assistant alike). */
    margin-left: auto;
    cursor: default;
    --offset: calc(-1 * var(--card-spacing) / 2);
    margin-right: var(--offset);
    /* Sink into the card's bottom padding — which only the card's LAST row has
       (--content-card-end-item, SessionItem.vue; unset outside assistant cards,
       hence the 1). A block-end message followed by a row of the same card (the
       USER_TURN background-work status line) has no bottom padding: sinking
       there would draw the time over that row. */
    margin-bottom: calc(var(--offset) * var(--content-card-end-item, 1));
}
</style>
