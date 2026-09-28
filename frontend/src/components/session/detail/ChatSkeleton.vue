<script setup>
// Chat skeleton (visual refresh step 5a, docs/plans/2026-09-28-chat-entrances-skeleton-design.md §7.4):
// four card shapes with a moving shimmer, shown over the chat while it loads and positions
// itself. The owner mounts one instance per reveal phase and sets `visible` when the
// controller says so (utils/chatReveal.js); the instance fades in and out in opacity.
defineProps({
    visible: { type: Boolean, default: false },
    /** 'end': parent sessions open at the bottom; 'start': sub-agent tabs open at the top. */
    align: { type: String, default: 'end' },
})

// The mock's shapes, in order: user, assistant, user, assistant.
const BARS = [
    { user: true, width: '55%', height: '4.5rem' },
    { user: false, width: '85%', height: '12rem' },
    { user: true, width: '40%', height: '3.5rem' },
    { user: false, width: '75%', height: '8rem' },
]
</script>

<template>
    <div
        class="chat-skeleton"
        :class="{ 'is-visible': visible, 'is-start': align === 'start' }"
        aria-hidden="true"
    >
        <div
            v-for="(bar, index) in BARS"
            :key="index"
            class="chat-skeleton-bar"
            :class="{ 'is-user': bar.user }"
            :style="{ width: bar.width, height: bar.height }"
        ></div>
    </div>
</template>

<style scoped>
.chat-skeleton {
    /* A hair darker than the chat card in light, a hair lighter in dark (where the real
       cards are lighter than the panel). A starting point for the review. */
    --chat-skeleton-base: color-mix(in oklab, var(--wa-color-surface-default), var(--wa-color-text-normal) 7%);
    --chat-skeleton-shine: color-mix(in oklab, var(--wa-color-surface-default), var(--wa-color-text-normal) 13%);
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    gap: 1.5rem;
    padding: var(--wa-space-l);
    overflow: hidden;
    pointer-events: none;
    opacity: 0;
    transition: opacity var(--motion-dur-2) var(--motion-ease);
}
.chat-skeleton.is-start {
    justify-content: flex-start;
}
.chat-skeleton.is-visible {
    opacity: 1;
}
/* Same specificity as .is-visible, declared after: the leave wins. Vue's Transition puts
   the class on this root, which carries the scope attribute. */
.chat-skeleton.chat-skeleton-leave-to {
    opacity: 0;
}

.chat-skeleton-bar {
    flex-shrink: 0;
    border-radius: var(--wa-panel-border-radius);
    background: linear-gradient(90deg, var(--chat-skeleton-base) 0%, var(--chat-skeleton-shine) 50%, var(--chat-skeleton-base) 100%);
    background-size: 200% 100%;
    animation: chat-skeleton-shimmer 1.4s linear infinite;
}
.chat-skeleton-bar.is-user {
    align-self: flex-end;
}

/* One full period, seamless, left to right. */
@keyframes chat-skeleton-shimmer {
    from { background-position: 100% 0; }
    to { background-position: -100% 0; }
}

/* Reduced motion: no moving shimmer, the bars pulse in opacity (motion-status-pulse is
   global, motion.css); the opacity fade of the root stays. */
@media (prefers-reduced-motion: reduce) {
    .chat-skeleton-bar {
        background: var(--chat-skeleton-base);
        animation: motion-status-pulse 1.4s ease-in-out infinite;
    }
}
</style>
