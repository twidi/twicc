<script setup>
import { computed } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { localPresence } from '../../utils/presence'

const props = defineProps({
    status: {
        type: String,
        required: true,
        validator: (value) => ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].includes(value)
    }
})

const statusConfig = {
    OPEN: { label: 'app.connection.connected', color: 'var(--wa-color-success)' },
    CONNECTING: { label: 'app.connection.connecting', color: 'var(--wa-color-warning)' },
    CLOSING: { label: 'app.connection.disconnecting', color: 'var(--wa-color-warning)' },
    CLOSED: { label: 'app.connection.disconnected', color: 'var(--wa-color-danger)' }
}

const config = computed(() => {
    const base = statusConfig[props.status] || statusConfig.CLOSED
    // Connected but no human active at this client → blue instead of green, with
    // an "(Away)" hint in the tooltip. Lets you confirm the presence signal (used
    // to suppress auto "mark as read" on an unattended device) is working. Other
    // states keep their own color.
    if (props.status === 'OPEN' && !localPresence.value) {
        return { label: 'app.connection.away', color: 'var(--wa-color-blue-60)' }
    }
    return base
})
</script>

<template>
    <div id="connection-indicator" class="connection-indicator">
        <span class="indicator-dot" :style="{ backgroundColor: config.color }"></span>
    </div>
    <AppTooltip for="connection-indicator">{{ $t(config.label) }}</AppTooltip>
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
