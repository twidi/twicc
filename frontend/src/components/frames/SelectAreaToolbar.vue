<script setup>
// Sub-toolbar of the element-picking mode (select area), shared by the
// Browser pane (companion-driven over postMessage) and the artifact HTML
// preview (direct picker). Purely presentational: the owner supplies the
// picker's state report and executes the emitted actions.
import { useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'

defineProps({
    // The picker's select-state report ({ hasSelection, locked, canParent,
    // canFirstChild, canPrevSibling, canNextSibling }); null until an element
    // has been highlighted.
    state: { type: Object, default: null },
})
defineEmits(['nav', 'clear', 'comment', 'close'])

const instanceId = useId()
</script>

<template>
    <div class="select-toolbar">
        <span class="subbar-label">Select area</span>
        <wa-button :id="`select-clear-${instanceId}`" appearance="plain" size="small" class="subbar-btn reduced-height" :disabled="!state?.locked" @click="$emit('clear')">
            <wa-icon name="ban"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-clear-${instanceId}`">Clear the selection</AppTooltip>
        <wa-button :id="`select-parent-${instanceId}`" appearance="plain" size="small" class="subbar-btn reduced-height" :disabled="!state?.canParent" @click="$emit('nav', 'parent')">
            <wa-icon name="arrow-up"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-parent-${instanceId}`">Select the parent</AppTooltip>
        <wa-button :id="`select-child-${instanceId}`" appearance="plain" size="small" class="subbar-btn reduced-height" :disabled="!state?.canFirstChild" @click="$emit('nav', 'first-child')">
            <wa-icon name="arrow-down"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-child-${instanceId}`">Select the first child</AppTooltip>
        <wa-button :id="`select-prev-${instanceId}`" appearance="plain" size="small" class="subbar-btn reduced-height" :disabled="!state?.canPrevSibling" @click="$emit('nav', 'prev-sibling')">
            <wa-icon name="arrow-left"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-prev-${instanceId}`">Select the previous sibling</AppTooltip>
        <wa-button :id="`select-next-${instanceId}`" appearance="plain" size="small" class="subbar-btn reduced-height" :disabled="!state?.canNextSibling" @click="$emit('nav', 'next-sibling')">
            <wa-icon name="arrow-right"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-next-${instanceId}`">Select the next sibling</AppTooltip>
        <wa-button
            :id="`select-comment-${instanceId}`"
            appearance="plain"
            size="small"
            class="subbar-btn reduced-height"
            :class="{ 'comment-armed': state?.locked }"
            :disabled="!state?.hasSelection"
            @click="$emit('comment')"
        >
            <wa-icon name="comment" variant="regular"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-comment-${instanceId}`">Comment on the selection</AppTooltip>
        <wa-button :id="`select-close-${instanceId}`" appearance="plain" size="small" class="subbar-btn reduced-height subbar-close" @click="$emit('close')">
            <wa-icon name="xmark"></wa-icon>
        </wa-button>
        <AppTooltip :for="`select-close-${instanceId}`">Exit select mode</AppTooltip>
    </div>
</template>

<style scoped>
/* Same look as the responsive-viewport sub-toolbar (ViewportToolbar.vue) —
   scoped twins, kept in sync by hand. */
.select-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
    padding: var(--wa-space-2xs);
    border-bottom: 1px solid var(--wa-color-border-quiet);
    flex-shrink: 0;
}

.subbar-label {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    padding-inline: var(--wa-space-2xs);
}

.subbar-btn {
    flex-shrink: 0;
}

/* Exit button pinned to the far right, away from the mode's controls. */
.subbar-close {
    margin-left: auto;
}

/* Once an element is locked (green frame), the comment button invites the
   click: brand colour + a 1s opacity pulse. */
.comment-armed wa-icon {
    color: var(--wa-color-brand-fill-loud);
    animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}
</style>
