<script setup>
import { computed } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { resolveRailItems } from '../../utils/sidebarRail.js'
import AppTooltip from '../ui/AppTooltip.vue'
import SettingsPopover from '../app/SettingsPopover.vue'
import PeerInboxBadge from '../peer/PeerInboxBadge.vue'

const props = defineProps({
    mode: { type: String, required: true, validator: value => ['home', 'sessions', 'artifacts'].includes(value) },
    sidebarOpen: { type: Boolean, default: false },
    peerConfigured: { type: Boolean, required: true },
    inboxCount: { type: Number, required: true },
    settingsAnchor: { type: Object, default: null },
})
const emit = defineEmits(['home', 'select-mode', 'search', 'palette', 'inbox', 'toggle-sidebar'])
const settingsStore = useSettingsStore()
const items = computed(() => resolveRailItems({
    mode: props.mode,
    sidebarOpen: props.sidebarOpen,
    peerConfigured: props.peerConfigured,
    inboxCount: props.inboxCount,
    isMac: settingsStore.isMac,
}))
const top = computed(() => items.value.filter(item => item.group === 'top'))
const bottom = computed(() => items.value.filter(item => item.group === 'bottom'))

function activate(item) {
    if (item.id === 'sessions' || item.id === 'artifacts') emit('select-mode', item.id)
    else if (item.id === 'toggle') emit('toggle-sidebar')
    else emit(item.id)
}
</script>

<template>
    <nav aria-label="Main navigation" class="sidebar-rail">
        <div class="panel-card">
            <template v-for="item in top" :key="item.id">
                <button
                    :id="`sidebar-rail-${item.id}`" type="button" class="rail-button"
                    :aria-label="item.label" :aria-pressed="item.active" :disabled="item.disabled"
                    @click="activate(item)"
                >
                    <wa-icon :name="item.icon" />
                </button>
                <AppTooltip :for="`sidebar-rail-${item.id}`" placement="right">{{ item.label }}</AppTooltip>
            </template>
            <div class="rail-spacer" aria-hidden="true"></div>
            <template v-for="item in bottom" :key="item.id">
                <div v-if="item.id === 'settings'" class="rail-settings">
                    <SettingsPopover
                        trigger-appearance="plain" trigger-icon-only placement="right-end"
                        tooltip-placement="right" :trigger-label="item.label" :position-anchor="settingsAnchor"
                    />
                </div>
                <template v-else>
                    <button
                        :id="`sidebar-rail-${item.id}`" type="button" class="rail-button"
                        :aria-label="item.label" :aria-pressed="item.active" :disabled="item.disabled"
                        @click="activate(item)"
                    >
                        <wa-icon :name="item.icon" />
                        <PeerInboxBadge v-if="item.id === 'inbox'" :count="item.badge" />
                    </button>
                    <AppTooltip :for="`sidebar-rail-${item.id}`" placement="right">{{ item.label }}</AppTooltip>
                </template>
            </template>
        </div>
    </nav>
</template>

<style scoped>
.sidebar-rail {
    display: flex;
    flex: none;
    position: relative;
    width: var(--rail-width);
    container-type: size;
    container-name: rail;
    z-index: 3;
}

.panel-card {
    display: flex;
    flex-direction: column;
    gap: var(--rail-gap);
    padding: var(--rail-card-padding);
    flex: 1;
    margin-block: var(--panel-gap);
    margin-inline-start: var(--panel-gap);
}

.rail-spacer {
    flex: 1;
}

.rail-settings {
    display: flex;
    flex: none;
}

:deep(wa-tooltip) {
    position: absolute;
}

@media (width < 640px) {
    .sidebar-rail {
        z-index: 101;
        background: var(--canvas-background);
        background-attachment: fixed;
    }
}

@container rail (height < 22rem) {
    .panel-card {
        overflow-x: hidden;
        overflow-y: auto;
        scrollbar-width: none;
    }

    .panel-card::-webkit-scrollbar {
        display: none;
    }
}
</style>
