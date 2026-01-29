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

</script>

<template>
    <div class="group-toggle">
        <wa-switch
            size="small"
            :checked="expanded"
            @change="handleClick"
        >
            <span class="toggle-label">
                {{ expanded ? 'Hide' : 'View' }} {{ itemCount }} element{{ itemCount !== 1 ? 's' : '' }}
            </span>
        </wa-switch>
    </div>
</template>

<style scoped>
.group-toggle {
    display: flex;
    align-items: center;
}

wa-switch {
    --spacing-top: calc(var(--content-card-not-start-item, 1) * var(--wa-space-s));
    --spacing-bottom: calc(var(--content-card-not-end-item, 1) * var(--wa-space-s));
    margin-top: var(--spacing-top);
    margin-bottom: var(--spacing-bottom);
    transition: opacity 0.2s;
    opacity: 0.25;
    &:hover {
        opacity: 0.75;
        .toggle-label {
            opacity: 1;
        }
    }
    display: flex;
    width: 100%;
}

wa-switch:state(checked) {
    opacity: 1;
    margin-bottom: calc(var(--wa-space-l) * -1/2);
    z-index: 1;
    .toggle-label {
        opacity: 1;
    }
}

.toggle-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-subtle);
    opacity: 0;
    transition: opacity 0.2s;
}

</style>
