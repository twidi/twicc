<script setup>
/**
 * GroupToggle - Clickable toggle for expanding/collapsing item groups.
 *
 * A hairline with a count pill in the middle (the look of the day separators): the rule says that
 * something is folded here, the pill gives the count and lights up on hover, on keyboard focus and
 * while the group is open. In simplified mode, this replaces collapsed group content.
 */
import CodeCommentsIndicator from '../../ui/CodeCommentsIndicator.vue'

defineProps({
    /**
     * Whether the group is currently expanded.
     */
    expanded: {
        type: Boolean,
        default: false
    },
    /**
     * Number of items in the group (optional, for future display).
     */
    itemCount: {
        type: Number,
        default: 0
    },
    /**
     * Number of code comments in this group's tools.
     */
    commentsCount: {
        type: Number,
        default: 0,
    },
})

const emit = defineEmits(['toggle'])

function handleClick() {
    emit('toggle')
}

</script>

<template>
    <button type="button" class="group-toggle" :aria-expanded="expanded" @click="handleClick">
        <span class="group-toggle-line"></span>
        <span class="group-toggle-pill">
            <span class="toggle-label-text">{{ itemCount }} tool{{ itemCount !== 1 ? 's' : '' }}</span>
            <CodeCommentsIndicator :count="commentsCount" :show-tooltip="false" class="toggle-comments-indicator" />
            <wa-icon name="chevron-down" class="group-toggle-chevron"></wa-icon>
        </span>
        <span class="group-toggle-line"></span>
    </button>
</template>

<style scoped>
/* A native button reset: the toggle is the whole row. The spacing is the old switch's (the card
   variables are set on this element by the item list). */
.group-toggle {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    width: 100%;
    padding: 0;
    border: 0;
    background: none;
    font: inherit;
    color: inherit;
    cursor: pointer;
    user-select: none;
    --spacing-top: calc(var(--content-card-not-start-item, 1) * var(--wa-space-s));
    --spacing-bottom: calc(var(--content-card-not-end-item, 1) * var(--wa-space-s));
    margin-top: var(--spacing-top);
    margin-bottom: var(--spacing-bottom);
}

/* The day separator's rule. */
.group-toggle-line {
    flex: 1;
    height: 0;
    border-top: var(--wa-border-width-s) solid var(--wa-color-surface-border);
}

.group-toggle-pill {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    padding: 0.05em 0.7em;
    border-radius: 999px;
    border: 1px solid var(--wa-color-surface-border);
    background: var(--surface-solid, var(--wa-color-surface-default));
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
    transition: color 0.2s, border-color 0.2s, background-color 0.2s;
}

.group-toggle:hover .group-toggle-pill,
.group-toggle:focus-visible .group-toggle-pill,
.group-toggle[aria-expanded="true"] .group-toggle-pill {
    color: var(--wa-color-brand-on-quiet);
    border-color: color-mix(in oklab, var(--wa-color-brand-60) 55%, transparent);
    background: color-mix(in oklab, var(--wa-color-brand-60) 12%, var(--surface-solid, var(--wa-color-surface-default)));
}

.group-toggle:focus-visible {
    outline: none;
}
.group-toggle:focus-visible .group-toggle-pill {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.group-toggle-chevron {
    font-size: 0.85em;
    transition: rotate 0.2s;
}
/* Opens counter-clockwise. */
.group-toggle[aria-expanded="true"] .group-toggle-chevron {
    rotate: -180deg;
}

.toggle-comments-indicator {
    font-size: 0.8em;
}
</style>
