<script setup>
/**
 * GroupToggle - Clickable toggle for expanding/collapsing item groups.
 *
 * Shows "..." when collapsed, with visual feedback on hover.
 * In simplified mode, this replaces collapsed group content.
 * Optionally displays edited file names inline (wrapping if needed).
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
    },
    /**
     * List of edited file paths (relative) to display next to the toggle.
     */
    editFiles: {
        type: Array,
        default: () => []
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
        <span v-if="editFiles.length > 0 && !expanded" class="edit-files">
            <span v-for="(file, index) in editFiles" :key="file" class="edit-file">
                <span class="edit-file-name">{{ file }}</span>
                <span v-if="index < editFiles.length - 1" class="edit-file-separator">,</span>
            </span>
        </span>
    </div>
</template>

<style scoped>
.group-toggle {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0 var(--wa-space-s);
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
    flex-shrink: 0;
    &:state(checked) {
        opacity: 1;
        .toggle-label {
            opacity: 1;
        }
    }
}

.toggle-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    opacity: 0;
    transition: opacity 0.2s;
}

.edit-files {
    display: inline;
    flex-wrap: wrap;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    opacity: 0.6;
    line-height: 1.4;
}

.edit-file {
    white-space: nowrap;
}

.edit-file-name {
    font-family: var(--wa-font-mono);
}

.edit-file-separator {
    margin-right: 0.3em;
}

</style>
