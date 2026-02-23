<script setup>
/**
 * AppTooltip - Unified tooltip wrapper around wa-tooltip.
 *
 * Automatically hides tooltips on touch devices (where hover is not available).
 * On non-touch devices, tooltips are always shown.
 *
 * Usage:
 *   <AppTooltip :for="elementId">Tooltip text</AppTooltip>
 *
 * Props:
 *   - force: When true, the tooltip is always shown even on touch devices.
 *     Use for critical UI elements like quota indicators where the tooltip
 *     provides essential information.
 *
 * All extra attributes are forwarded to the underlying <wa-tooltip>.
 */
import { computed } from 'vue'
import { useSettingsStore } from '../stores/settings'

const props = defineProps({
    force: {
        type: Boolean,
        default: false,
    },
})

const settingsStore = useSettingsStore()
const shouldShow = computed(() => props.force || !settingsStore.isTouchDevice)
</script>

<template>
    <wa-tooltip v-if="shouldShow" v-bind="$attrs"><slot /></wa-tooltip>
</template>
