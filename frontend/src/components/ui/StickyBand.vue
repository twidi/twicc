<script setup>
// StickyBand.vue - The band at the top of a scope tab's content: the Stats provider filter, the Timeline header.
// It sits in the content's scroll container, scrolls with the content, then stays at the top of it
// (position: sticky). Its content (a filter, a label) is the default slot.
//
// Glass (styles/glass.css .glass-sticky): the content scrolling under it shows through, blurred. Same
// translucency as the shared glass-sticky-bg (60%), but the neutral surface colour instead of the accent tint.
</script>

<template>
    <div class="sticky-band glass-sticky">
        <slot />
    </div>
</template>

<style scoped>
.sticky-band {
    position: sticky;
    top: 0;
    z-index: 2;
    display: flex;
    justify-content: center;
    padding: var(--wa-space-xs) 0 var(--wa-space-m);
    --glass-sticky-bg: color-mix(in oklab, var(--surface-solid) 60%, transparent);
}

/* Without the blur (reduced transparency, or "Reduce effects" below) the translucent layer would let the
   content show through unreadable: the card's opaque colour, as before the glass. */
@media (prefers-reduced-transparency: reduce) {
    .sticky-band {
        --glass-sticky-bg: transparent;
        background: var(--panel-solid);
    }
}
</style>

<!-- Unscoped on purpose: Vue's scoped compiler drops the descendant part of a
     `:global(:root.x) .y` selector, leaving a bare `:root.x` rule. -->
<style>
/* "Reduce effects" has no blur either: the opaque card colour, as in the other no-blur modes. */
:root.reduce-effects .sticky-band {
    --glass-sticky-bg: transparent;
    background: var(--panel-solid);
}
</style>
