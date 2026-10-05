<script setup>
/**
 * SidebarListSeparator — horizontal rule with a centered label, inserted before
 * a group of rows in a sidebar list (sessions: Selected / Pinned / Active /
 * "Last 24 hours" / "Older than <X>"; artifacts: only the date buckets). Same
 * visual treatment as the message list's DaySeparator (line · label · line).
 *
 * Two label modes:
 *  - Plain: just `label` (e.g. "Pinned", "Active", "Last 24 hours").
 *  - Threshold (when `prefix` is set, e.g. "Older than "): responsive, CSS-only
 *    via the nearest size container (the sidebar list — `.session-list-container`
 *    or `.bookmark-list-container`). On a sidebar ≥ 12rem it reads
 *    "<prefix><label>" (e.g. "Older than 7 days"); on a narrower one it collapses
 *    to "<label> +" (e.g. "7 days +").
 *
 * `title` is shown as a native tooltip (used for the exact cutoff datetime of
 * the "older than" thresholds).
 */
defineProps({
    // Main label text, e.g. "Pinned", "Last 24 hours", or the duration "7 days".
    label: {
        type: String,
        required: true,
    },
    // Optional leading text for threshold separators, e.g. "Older than ". When
    // set, the label becomes responsive (collapses to "<label> +" when narrow).
    prefix: {
        type: String,
        default: null,
    },
    // Optional native tooltip (e.g. the exact cutoff datetime).
    title: {
        type: String,
        default: null,
    },
})
</script>

<template>
    <div
        class="sidebar-list-separator"
        role="separator"
        :aria-label="prefix ? `${prefix}${label}` : label"
        :title="title"
    >
        <span class="sls-line"></span>
        <span class="sls-label"><span v-if="prefix" class="sls-prefix">{{ prefix }}</span>{{ label }}<span v-if="prefix" class="sls-suffix"> +</span></span>
        <span class="sls-line"></span>
    </div>
</template>

<style scoped>
.sidebar-list-separator {
    /* Positioned: a list's gliding ink (the open row's fill) passes under the label, like
       under the rows, instead of painting over it. */
    position: relative;
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
    margin-block: var(--wa-space-xs);
    margin-inline: var(--sidebar-separator-inset, var(--wa-space-m));
    user-select: none;
}

.sls-line {
    flex: 1;
    height: 0;
    /* The sidebar provides an accent-based color; elsewhere (dialogs) the surface border. */
    border-top: var(--wa-border-width-s) solid var(--sidebar-divider-color, var(--wa-color-surface-border));
}

.sls-label {
    flex: 0 0 auto;
    font-size: 0.6875rem;
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.07em;
    color: var(--wa-color-text-quiet);
    white-space: nowrap;
}

/* Threshold labels (with a prefix): default to the compact "<label> +" form,
   expand to "<prefix><label>" once the sidebar (nearest size container) is wide
   enough. Anonymous query so it works under both the session and artifact lists. */
.sls-prefix { display: none; }
.sls-suffix { display: inline; }

@container (min-width: 12rem) {
    .sls-prefix { display: inline; }
    .sls-suffix { display: none; }
}
</style>
