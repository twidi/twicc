<script setup>
/**
 * GroupToggle - Clickable toggle for expanding/collapsing item groups.
 *
 * Shows "..." when collapsed, with visual feedback on hover.
 * In simplified mode, this replaces collapsed group content.
 */
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
    }
})

const emit = defineEmits(['toggle'])

function handleClick() {
    emit('toggle')
}

function handleKeydown(event) {
    // Support Enter and Space for keyboard accessibility
    if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        emit('toggle')
    }
}
</script>

<template>
    <div
        class="group-toggle"
        :class="{ 'group-toggle--expanded': expanded }"
        role="button"
        tabindex="0"
        :aria-expanded="expanded"
        :aria-label="expanded ? 'Collapse group' : 'Expand group'"
        @click="handleClick"
        @keydown="handleKeydown"
    >
        <span class="toggle-indicator">...</span>
        <!-- Future: show item count -->
        <!-- <span v-if="itemCount > 0" class="toggle-count">({{ itemCount }})</span> -->
    </div>
</template>

<style scoped>
.group-toggle {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-s) var(--wa-space-m);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-radius-m);
    cursor: pointer;
    user-select: none;
    transition: background-color 0.15s ease;
}

.group-toggle:hover {
    background: var(--wa-color-surface-hover, rgba(0, 0, 0, 0.05));
}

.group-toggle:focus {
    outline: 2px solid var(--wa-color-primary);
    outline-offset: 2px;
}

.group-toggle--expanded {
    background: var(--wa-color-surface-alt);
    border-bottom-left-radius: 0;
    border-bottom-right-radius: 0;
}

.toggle-indicator {
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    color: var(--wa-color-text-subtle);
    letter-spacing: 0.1em;
}

.toggle-count {
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-subtle);
}
</style>
