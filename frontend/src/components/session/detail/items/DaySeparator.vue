<script setup>
/**
 * DaySeparator — a horizontal rule with the calendar date centered, inserted
 * into the session item list at inter-block boundaries whenever the day changes
 * (only when the "Message timestamps" setting is on).
 *
 * Three parts: a line on the left, the date label in the middle, a line on the
 * right (the lines are borders on the side elements).
 */
defineProps({
    label: {
        type: String,
        required: true,
    },
    // Numeric date "YYYY-MM-DD", shown as a native tooltip on hover.
    dayKey: {
        type: String,
        default: null,
    },
})
</script>

<template>
    <div class="day-separator" role="separator" :aria-label="label">
        <span class="day-separator-line"></span>
        <span class="day-separator-label" :title="dayKey">{{ label }}</span>
        <span class="day-separator-line"></span>
    </div>
</template>

<style scoped>
.day-separator {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    margin: var(--wa-space-s) var(--wa-space-l);
    user-select: none;
}

.day-separator-line {
    flex: 1;
    height: 0;
    border-top: var(--wa-border-width-s) solid var(--wa-color-surface-border);
}

.day-separator-label {
    flex: 0 0 auto;
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
}
</style>
