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
 * All extra attributes are forwarded to the underlying <wa-tooltip>.
 */
import { computed } from 'vue'
import { useSettingsStore } from '../stores/settings'

const settingsStore = useSettingsStore()
const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)
</script>

<template>
    <wa-tooltip v-if="tooltipsEnabled" v-bind="$attrs"><slot /></wa-tooltip>
</template>
