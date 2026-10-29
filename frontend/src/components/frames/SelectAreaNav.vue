<script setup>
// Header of the element-picking card (select area), shared by the Browser pane
// (companion-driven over postMessage) and the artifact HTML preview (direct
// picker). Purely presentational: the owner supplies the picker's state report
// and executes the emitted actions. Lives in the `header` slot of the
// persistent TextSelectionComment.
import { computed, useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { useSettingsStore } from '../../stores/settings'

const props = defineProps({
    // The picker's select-state report ({ hasSelection, locked, canParent,
    // canFirstChild, canPrevSibling, canNextSibling, rect }); null until an
    // element has been highlighted.
    state: { type: Object, default: null },
})
defineEmits(['nav', 'clear'])

const instanceId = useId()
const settingsStore = useSettingsStore()

const hint = computed(() => (settingsStore.isTouchDevice ? 'Tap an element to comment' : 'Click an element to comment'))
</script>

<template>
    <div class="select-nav">
        <!-- One line either way: the hint until an element is locked, then the
             navigation buttons (all of them need a locked element). -->
        <div v-if="!state?.locked" class="select-nav-hint">{{ hint }}</div>
        <div v-else class="select-nav-buttons">
            <wa-button :id="`select-clear-${instanceId}`" appearance="plain" size="small" class="nav-btn reduced-height" :disabled="!state?.locked" @click="$emit('clear')">
                <wa-icon name="ban"></wa-icon>
            </wa-button>
            <AppTooltip :for="`select-clear-${instanceId}`">Clear the selection</AppTooltip>
            <wa-button :id="`select-parent-${instanceId}`" appearance="plain" size="small" class="nav-btn reduced-height" :disabled="!state?.canParent" @click="$emit('nav', 'parent')">
                <wa-icon name="arrow-up"></wa-icon>
            </wa-button>
            <AppTooltip :for="`select-parent-${instanceId}`">Select the parent</AppTooltip>
            <wa-button :id="`select-child-${instanceId}`" appearance="plain" size="small" class="nav-btn reduced-height" :disabled="!state?.canFirstChild" @click="$emit('nav', 'first-child')">
                <wa-icon name="arrow-down"></wa-icon>
            </wa-button>
            <AppTooltip :for="`select-child-${instanceId}`">Select the first child</AppTooltip>
            <wa-button :id="`select-prev-${instanceId}`" appearance="plain" size="small" class="nav-btn reduced-height" :disabled="!state?.canPrevSibling" @click="$emit('nav', 'prev-sibling')">
                <wa-icon name="arrow-left"></wa-icon>
            </wa-button>
            <AppTooltip :for="`select-prev-${instanceId}`">Select the previous sibling</AppTooltip>
            <wa-button :id="`select-next-${instanceId}`" appearance="plain" size="small" class="nav-btn reduced-height" :disabled="!state?.canNextSibling" @click="$emit('nav', 'next-sibling')">
                <wa-icon name="arrow-right"></wa-icon>
            </wa-button>
            <AppTooltip :for="`select-next-${instanceId}`">Select the next sibling</AppTooltip>
        </div>
    </div>
</template>

<style scoped>
.select-nav {
    display: flex;
    justify-content: center;
}

.select-nav-buttons {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.nav-btn {
    flex-shrink: 0;
}

.select-nav-hint {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    text-align: center;
    /* Same height as the button row, so the card does not jump on lock. */
    line-height: 1.75rem;
}
</style>
