<script setup>
/**
 * PeerInboxBadge — a pending-peer-work counter, in one appearance wherever it
 * lands. Renders nothing at zero.
 *
 * Pinned (default): hangs over the top-inline-end corner of the positioned
 * element it sits in — an inbox button or the floating reopen button
 * while the icon bar is hidden.
 *
 * `inline`: stays in normal flow, for a nav row or a button label. Inside a
 * `wa-button`, wrap the label and this badge in one element: the button pins
 * any wa-badge slotted straight into it to its top corner
 * (`.button ::slotted(wa-badge)`), which the wrapper takes it out of.
 *
 * The caller selects the count. The icon bar and floating reopen button show
 * the sum of messages awaiting review and pairing requests. Each Settings button
 * shows the count for its own action.
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
    /* A surface ring keeps the pinned badge readable over a solid button of the same
       accent fill (the home page's Inbox button). */
    box-shadow: 0 0 0 2px var(--surface-solid);
}
</style>
