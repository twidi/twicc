<script setup>
import { computed } from 'vue'

const props = defineProps({
    status: {
        type: String,
        required: true,
        validator: (value) => ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].includes(value)
    }
})

const statusConfig = {
    OPEN: { label: 'Connecté', color: 'var(--wa-color-success)' },
    CONNECTING: { label: 'Connexion...', color: 'var(--wa-color-warning)' },
    CLOSING: { label: 'Déconnexion...', color: 'var(--wa-color-warning)' },
    CLOSED: { label: 'Déconnecté', color: 'var(--wa-color-danger)' }
}

const config = computed(() => statusConfig[props.status] || statusConfig.CLOSED)
</script>

<template>
    <div class="connection-indicator" :title="`WebSocket: ${config.label}`">
        <span class="indicator-dot" :style="{ backgroundColor: config.color }"></span>
    </div>
</template>

<style scoped>
.connection-indicator {
    position: fixed;
    top: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 1000;
}

.indicator-dot {
    display: block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
}
</style>
