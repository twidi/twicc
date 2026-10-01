<script setup>
// Compact upload status shown next to the Files / Artifacts tab label (spec §6.11): an upload icon
// and the aggregate percentage, with the count first when several uploads run (`2 · 43%`). Renders
// nothing without a status. Shared by the center tab strip, the dock tab bars, the gutter chips
// (and their measurement mirrors), the layout overlay and the project detail panel.

defineProps({
    /** Aggregate of one origin (`statusByOrigin`): { count, percent, allStalled }, or null. */
    status: {
        type: Object,
        default: null,
    },
})
</script>

<template>
    <span
        v-if="status && status.count > 0"
        class="upload-tab-status"
        :class="{ stalled: status.allStalled }"
    >
        <wa-icon name="upload" class="upload-tab-icon" label="Uploads"></wa-icon>
        <span v-if="status.count > 1" class="upload-tab-count">{{ status.count }} ·</span>
        <span class="upload-tab-percent">{{ status.percent }}%</span>
    </span>
</template>

<style scoped>
.upload-tab-status {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    flex-shrink: 0;
    font-size: var(--wa-font-size-xs);
    font-variant-numeric: tabular-nums;
}

.upload-tab-status.stalled {
    color: var(--wa-color-text-quiet);
}

.upload-tab-icon {
    font-size: 0.7rem;
    flex-shrink: 0;
}

.upload-tab-count {
    white-space: nowrap;
}

/* Fixed width while it counts (up to "100%"), so a growing number never resizes the tab, and the
   gutter measurement mirrors never flip the label mode. */
.upload-tab-percent {
    display: inline-block;
    min-width: 4ch;
    text-align: end;
    white-space: nowrap;
}
</style>
