<script setup>
/**
 * AppTooltip - Unified tooltip wrapper around wa-tooltip.
 *
 * Encapsulates the tooltipsEnabled setting check so that consumers
 * don't need to import the settings store, create a computed property,
 * or add v-if="tooltipsEnabled" at every call site.
 *
 * Usage:
 *   <AppTooltip :for="elementId">Tooltip text</AppTooltip>
 *
 * Props:
 *   - force: When true, the tooltip is always shown regardless of the
 *     tooltipsEnabled setting. Use for critical UI elements like quota
 *     indicators where the tooltip provides essential information.
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
const shouldShow = computed(() => props.force || settingsStore.areTooltipsEnabled)
</script>

<template>
    <wa-tooltip v-if="shouldShow" v-bind="$attrs"><slot /></wa-tooltip>
</template>
