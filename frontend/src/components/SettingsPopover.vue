<script setup>
// SettingsPopover.vue - Settings button with popover panel
import { computed, ref, watch, nextTick } from 'vue'
import { useDataStore } from '../stores/data'
import { DISPLAY_MODE } from '../constants'

const store = useDataStore()

// Refs for wa-switch elements (needed to sync checked property with Web Components)
const baseModeSwitch = ref(null)
const debugSwitch = ref(null)

// Settings from store
const baseDisplayMode = computed(() => store.getBaseDisplayMode)
const debugEnabled = computed(() => store.isDebugEnabled)

// Computed label for the base mode switch
const baseModeLabel = computed(() =>
    baseDisplayMode.value === DISPLAY_MODE.SIMPLIFIED ? 'Simplified' : 'Detailed'
)

// Is simplified mode active?
const isSimplified = computed(() => baseDisplayMode.value === DISPLAY_MODE.SIMPLIFIED)

// Sync switch checked state with store values
// Web Components don't always pick up initial prop values from Vue bindings
function syncSwitchState() {
    nextTick(() => {
        if (baseModeSwitch.value && baseModeSwitch.value.checked !== isSimplified.value) {
            baseModeSwitch.value.checked = isSimplified.value
        }
        if (debugSwitch.value && debugSwitch.value.checked !== debugEnabled.value) {
            debugSwitch.value.checked = debugEnabled.value
        }
    })
}

// Watch for store changes and sync switches
watch([isSimplified, debugEnabled], syncSwitchState, { immediate: true })

/**
 * Toggle between normal and simplified mode.
 */
function onBaseModeChange(event) {
    const newMode = event.target.checked ? DISPLAY_MODE.SIMPLIFIED : DISPLAY_MODE.NORMAL
    store.setBaseDisplayMode(newMode)
}

/**
 * Toggle debug mode.
 */
function onDebugChange(event) {
    store.setDebugEnabled(event.target.checked)
}

/**
 * Called when popover opens - sync switch states.
 */
function onPopoverShow() {
    syncSwitchState()
}
</script>

<template>
    <wa-button id="settings-trigger" variant="neutral" appearance="filled-outlined" size="small">
        <wa-icon name="gear"></wa-icon> Settings
    </wa-button>
    <wa-popover for="settings-trigger" placement="top" class="settings-popover" @wa-show="onPopoverShow">
        <div class="settings-content">
            <div class="setting-group">
                <label class="setting-group-label">Display mode</label>
                <wa-switch
                    ref="baseModeSwitch"
                    :disabled.prop="debugEnabled"
                    @change="onBaseModeChange"
                    size="small"
                >{{ baseModeLabel }}</wa-switch>
                <wa-switch
                    ref="debugSwitch"
                    @change="onDebugChange"
                    size="small"
                >Debug</wa-switch>
            </div>
        </div>
    </wa-popover>
</template>

<style scoped>
.settings-popover {
    --max-width: 280px;
    --arrow-size: 16px;
}

.settings-content {
    padding: var(--wa-space-s);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.setting-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.setting-group-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    margin-bottom: var(--wa-space-2xs);
}

</style>
