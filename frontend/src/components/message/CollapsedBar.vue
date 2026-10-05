<script setup>
// Shared single-line "collapsed" bar used by both the message input and the
// pending request form when they are reduced to a header. Clicking anywhere on
// the bar expands it (the chevron button is the explicit visual cue); the icon
// and label describe what is waiting inside. Keeping this in one component
// guarantees the two reduced panels render and hover identically.
import { useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'

defineProps({
    // Leading icon name (Font Awesome, resolved by wa-icon).
    icon: { type: String, required: true },
    // Single-line label; truncated with an ellipsis when too long.
    label: { type: String, default: '' },
    // Tooltip on the expand (chevron) button.
    expandTooltip: { type: String, default: 'Expand' },
    // Icon + label color family. The historical bars (pending request, message
    // input) are warning-tinted; the goal bar picks per goal state.
    variant: {
        type: String,
        default: 'warning',
        validator: (v) => ['warning', 'brand', 'success', 'neutral'].includes(v),
    },
    // Add left padding so the content clears the floating sidebar-toggle button
    // only while it is visible at the bottom-left of the chat surface.
    // Only the bottom-most bar needs this.
    sidebarToggleClearance: { type: Boolean, default: false },
})
const emit = defineEmits(['expand'])
const restoreButtonId = useId()
</script>

<template>
    <div
        class="collapsed-bar"
        :class="[`collapsed-bar--${variant}`, { 'collapsed-bar--sidebar-clearance': sidebarToggleClearance }]"
        @click="emit('expand')"
    >
        <wa-icon :name="icon" class="collapsed-bar-icon"></wa-icon>
        <span class="collapsed-bar-label">{{ label }}</span>
        <!-- Optional trailing content (e.g. a count badge) before the chevron. -->
        <slot name="trailing" />
        <wa-button
            variant="neutral"
            appearance="outlined"
            size="small"
            class="collapsed-bar-restore-btn"
            :id="restoreButtonId"
            @click.stop="emit('expand')"
        >
            <wa-icon name="chevron-up"></wa-icon>
        </wa-button>
        <AppTooltip :for="restoreButtonId">{{ expandTooltip }}</AppTooltip>
    </div>
</template>

<style scoped>
.collapsed-bar {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-xs);
    border-radius: var(--wa-border-radius-m);
    color: var(--wa-color-text-quiet);
    cursor: pointer;
}
/* Icon + label tint, per variant (see the `variant` prop). */
.collapsed-bar--warning { --collapsed-bar-color: var(--wa-color-warning-60); }
.collapsed-bar--brand { --collapsed-bar-color: var(--wa-color-brand-60); }
.collapsed-bar--success { --collapsed-bar-color: var(--wa-color-success-60); }
.collapsed-bar--neutral { --collapsed-bar-color: var(--wa-color-text-quiet); }
.collapsed-bar:hover {
    background: var(--wa-color-neutral-fill-quiet);
}
/* On mobile, clear the floating reopen button only while it is visible.
   Mirror the toolbar's offset so the label clears it. */
body.sidebar-toggle-floating .collapsed-bar--sidebar-clearance {
    @media (width < 640px) {
        padding-block: var(--wa-space-s);
        padding-left: 4rem;
    }
}
/* While the floating reopen button is visible, clear it on desktop too — same as
   .message-input-toolbar, reading the centralized --sidebar-toggle-clearance-x but sitting 0.5rem
   further in (so full=3rem, partial=1.5rem). */
body.sidebar-toggle-floating .collapsed-bar--sidebar-clearance {
    @media (width >= 640px) {
        padding-block: var(--wa-space-s);
        padding-left: calc(var(--sidebar-toggle-clearance-x) + 0.5rem);
    }
}
/* When a bottom dock *region* lifts the bar above the toggle, or a left column pushes it clear, the
   bar needs no clearance and reverts to its compact base padding. A bottom *gutter* is too thin to
   lift it, so it keeps the clearance (like .message-input-toolbar). */
body.sidebar-toggle-floating :is(.session-layout.has-bottom-region, .session-layout.has-left-col) .collapsed-bar--sidebar-clearance {
    @media (width >= 640px) {
        padding-block: var(--wa-space-xs);
        padding-left: var(--wa-space-xs);
    }
}
.collapsed-bar-icon {
    flex-shrink: 0;
    font-size: var(--wa-font-size-s);
    color: var(--collapsed-bar-color);
}
.collapsed-bar-label {
    flex: 1;
    min-width: 0;
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-bold);
    color: var(--collapsed-bar-color);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.collapsed-bar-restore-btn {
    flex-shrink: 0;
}
</style>
