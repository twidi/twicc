<script setup>
/**
 * PeerInboxBadge — a pending-peer-work counter, in one appearance wherever it
 * lands. Renders nothing at zero.
 *
 * Pinned (default): hangs over the top-inline-end corner of the positioned
 * element it sits in — the sidebar's inbox button, and the sidebar toggle
 * while the sidebar is collapsed and that button is folded away.
 *
 * `inline`: stays in normal flow, for a nav row or a button label. Inside a
 * `wa-button`, wrap the label and this badge in one element: the button pins
 * any wa-badge slotted straight into it to its top corner
 * (`.button ::slotted(wa-badge)`), which the wrapper takes it out of.
 *
 * The count is the caller's to pick: the sidebar shows the sum (messages
 * awaiting review + pairing requests), while the settings buttons each own
 * the half they act on.
 *
 * Always indicative, never interactive: `pointer-events: none` leaves the
 * click to whatever it sits on.
 */
defineProps({
    count: { type: Number, default: 0 },
    inline: Boolean,
})
</script>

<template>
    <wa-badge
        v-if="count > 0" variant="brand"
        class="peer-inbox-badge" :class="{ 'peer-inbox-badge--inline': inline }"
    >{{ count }}</wa-badge>
</template>

<style scoped>
.peer-inbox-badge {
    box-sizing: border-box;
    inline-size: 1.4rem;
    block-size: 1.4rem;
    padding: 0;
    border-radius: 50%;
    font-variant-numeric: tabular-nums;
    pointer-events: none;
}
.peer-inbox-badge:not(.peer-inbox-badge--inline) {
    position: absolute;
    inset-block-start: 0;
    inset-inline-end: 0;
    translate: 30% -30%;
}
</style>
