<script setup>
import { useDataStore } from '../../../stores/data'

const props = defineProps({ sessionId: { type: String, required: true } })
const store = useDataStore()
</script>

<template>
    <div class="ephemeral-actions-bar">
        <wa-button
            v-if="store.getSession(props.sessionId)?.ephemeralPhase === 'running'"
            size="small"
            @click="store.stopEphemeralSession(props.sessionId)"
        ><wa-icon slot="start" name="stop"></wa-icon>Stop</wa-button>
        <wa-button size="small" variant="danger" appearance="outlined" @click="store.discardEphemeralSession(props.sessionId)">
            <wa-icon slot="start" name="trash"></wa-icon>Discard
        </wa-button>
    </div>
</template>

<style scoped>
.ephemeral-actions-bar {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    padding: 0.75rem;
    border-top: 1px solid var(--wa-color-surface-border);
}
</style>
