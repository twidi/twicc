<script setup>
import { computed } from 'vue'
import { useSettingsStore } from '../stores/settings'

const settingsStore = useSettingsStore()
const tooltipsEnabled = computed(() => settingsStore.areTooltipsEnabled)

const props = defineProps({
    status: {
        type: String,
        required: true,
        validator: (value) => ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].includes(value)
    }
})

const statusConfig = {
    OPEN: { label: 'Connected', color: 'var(--wa-color-success)' },
    CONNECTING: { label: 'Connecting...', color: 'var(--wa-color-warning)' },
    CLOSING: { label: 'Disconnecting...', color: 'var(--wa-color-warning)' },
    CLOSED: { label: 'Disconnected', color: 'var(--wa-color-danger)' }
}

const config = computed(() => statusConfig[props.status] || statusConfig.CLOSED)
</script>

<template>
    <div id="connection-indicator" class="connection-indicator">
        <span class="indicator-dot" :style="{ backgroundColor: config.color }"></span>
    </div>
    <wa-tooltip v-if="tooltipsEnabled" for="connection-indicator">WebSocket: {{ config.label }}</wa-tooltip>
</template>

<style scoped>
.connection-indicator {
    position: fixed;
    top: var(--wa-space-2xs);
    left: var(--wa-space-2xs);
    z-index: 1000;
}

.indicator-dot {
    display: block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
}
</style>
