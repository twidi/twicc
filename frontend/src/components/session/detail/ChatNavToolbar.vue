<script setup>
/**
 * Transcript navigation toolbar: jump to the extremes, or block by block.
 *
 * Pinned to the bottom-right corner of the scroll area, over the content. Same
 * shape as the markdown toolbar that sits on the opposite corner of a message
 * (`.markdown-toolbar` in MarkdownContent.vue): a vertical `wa-button-group` of
 * small filled buttons, scaled down, fading in on hover — so the two read as
 * one family. Reused as-is by the read-only share viewer.
 *
 * Purely presentational — the logic lives in composables/useChatNavigation.js.
 */

const props = defineProps({
    canGoTop: { type: Boolean, default: false },
    canGoPrev: { type: Boolean, default: false },
    canGoNext: { type: Boolean, default: false },
    canGoBottom: { type: Boolean, default: false },
    // The scrolling element the toolbar covers. The toolbar is a sibling of the
    // scroller, not a descendant, so a wheel event over it would otherwise find
    // no scrollable ancestor and do nothing — see onWheel.
    scrollElement: { type: Object, default: null },
})

const emit = defineEmits(['top', 'prev', 'next', 'bottom'])

// Rough line height used to convert a line-based wheel delta into pixels.
const WHEEL_LINE_HEIGHT_PX = 16

/** Forward the wheel to the scroller so the toolbar isn't a dead zone. */
function onWheel(event) {
    const el = props.scrollElement
    if (!el) return
    const factor = event.deltaMode === 1 ? WHEEL_LINE_HEIGHT_PX : 1
    el.scrollTop += event.deltaY * factor
    event.preventDefault()
}
</script>

<template>
    <div class="chat-nav-toolbar" @wheel="onWheel">
        <wa-button-group orientation="vertical" :label="$t('session.chatNavigation.label')">
            <wa-button
                size="small"
                variant="neutral"
                appearance="filled"
                :title="$t('session.chatNavigation.first')"
                :disabled="!canGoTop"
                @click="emit('top')"
            >
                <wa-icon name="angles-up"></wa-icon>
            </wa-button>
            <wa-button
                size="small"
                variant="neutral"
                appearance="filled"
                :title="$t('session.chatNavigation.previous')"
                :disabled="!canGoPrev"
                @click="emit('prev')"
            >
                <wa-icon name="chevron-up"></wa-icon>
            </wa-button>
            <wa-button
                size="small"
                variant="neutral"
                appearance="filled"
                :title="$t('session.chatNavigation.next')"
                :disabled="!canGoNext"
                @click="emit('next')"
            >
                <wa-icon name="chevron-down"></wa-icon>
            </wa-button>
            <wa-button
                size="small"
                variant="neutral"
                appearance="filled"
                :title="$t('session.chatNavigation.last')"
                :disabled="!canGoBottom"
                @click="emit('bottom')"
            >
                <wa-icon name="angles-down"></wa-icon>
            </wa-button>
        </wa-button-group>
    </div>
</template>

<style scoped>
/* Mirrors `.markdown-toolbar`, anchored to the opposite corner. It stays
   readable at rest rather than fading out completely: unlike a message's own
   toolbar, this one has no block to hover to bring it back. */
.chat-nav-toolbar {
    position: absolute;
    /* Clear of the composer by the same gap the composer keeps from the right
       edge (its own `padding`), so the corner reads as one consistent inset.
       The composer also reserves room above itself for its focus ring, which
       widens the gap the eye actually sees — so that is taken back out here.
       Tuned for the ordinary case, a plain composer below the conversation;
       the footer occasionally holds something else (a read-only banner, or
       nothing at all on a subagent) and the gap is then 4px tight. `max` keeps
       the toolbar inside the scroll area whatever a theme does to the ring. */
    bottom: max(0px, calc(
        var(--wa-space-s) - var(--wa-focus-ring-width) - var(--wa-focus-ring-offset)
    ));
    /* Flush right: the squared corners below only make sense against the edge. */
    right: 0;
    padding: 0;
    background: transparent;
    border: none;
    opacity: 0.55;
    transform: scale(0.8);
    transform-origin: bottom right;
    transition: opacity 0.15s ease;
    z-index: 2;
}

.chat-nav-toolbar:hover {
    opacity: 1;
}

/* Flush against the right edge of the scroll area: the two outer corners that
   touch it stay square, so the group reads as anchored rather than floating. */
.chat-nav-toolbar wa-button:first-child::part(base) {
    border-start-end-radius: 0;
}
.chat-nav-toolbar wa-button:last-child::part(base) {
    border-end-end-radius: 0;
}

.chat-nav-toolbar wa-button wa-icon {
    width: 0.6rem;
}

@media print {
    .chat-nav-toolbar {
        display: none;
    }
}
</style>
