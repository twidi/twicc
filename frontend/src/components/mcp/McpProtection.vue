<script setup>
import { remoteUiMessage } from '../../i18n/remoteMessages.js'
import { resolveUiMessage } from '../../i18n/uiMessages.js'
import { ref, computed } from 'vue'
import { useMcpStore } from '../../stores/mcp'

const store = useMcpStore()
const busy = ref(false)
const protection = computed(() => store.protection)
const enabled = computed(() => store.config.externalMcpEnabled === true)
const minutes = computed(() => Math.max(1, Math.ceil((protection.value.retryAfter || 0) / 60)))

async function act(action) {
    if (busy.value) return
    busy.value = true
    try { await store.act(action) }
    finally { busy.value = false }
}
</script>

<template>
    <div v-if="enabled || protection.incident" class="mcp-protection">
        <wa-callout v-if="protection.incident" :variant="protection.paused ? 'danger' : 'warning'" size="small">
            <strong>{{ $t('mcp.protection.title') }}</strong>
            <p>{{ resolveUiMessage(remoteUiMessage(protection.incident, protection.incident.reason, 'reason_ui_message')) }}.</p>
            <p v-if="!enabled">{{ $t('mcp.protection.disabled') }}</p>
            <p v-else-if="protection.paused">
                {{ $t('mcp.protection.paused', { count: minutes }, minutes) }}
            </p>
            <p v-else>{{ $t('mcp.protection.ended') }}</p>
            <p>
                {{ $t('mcp.protection.counts', { registrations: protection.registrations || 0, authorizations: protection.authorizations || 0, rejections: protection.rejections || 0, sources: protection.sources || 0 }) }}
                <span v-if="protection.sampleLimited">{{ $t('mcp.protection.sampleLimited') }}</span>
            </p>
            <wa-button v-if="!protection.paused" size="small" :disabled="busy" @click="act('acknowledge_security')">
                {{ $t('mcp.protection.dismiss') }}
            </wa-button>
        </wa-callout>
        <template v-if="enabled">
            <wa-button size="small" variant="danger" appearance="outlined" :disabled="busy" @click="act('suspend')">
                {{ $t('mcp.protection.suspend') }}
            </wa-button>
            <span class="setting-group-hint">
                {{ $t('mcp.protection.suspendHint') }}
            </span>
        </template>
    </div>
</template>

<style scoped>
.mcp-protection { display: flex; flex-direction: column; align-items: flex-start; gap: var(--wa-space-s); }
p { margin: var(--wa-space-xs) 0; }
</style>
