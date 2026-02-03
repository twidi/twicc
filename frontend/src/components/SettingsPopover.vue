<script setup>
// SettingsPopover.vue - Settings button with popover panel
import { computed, ref, watch, nextTick } from 'vue'
import { useSettingsStore } from '../stores/settings'
import { DISPLAY_MODE, THEME_MODE, SESSION_TIME_FORMAT } from '../constants'

const store = useSettingsStore()

// Theme options for the select
const themeOptions = [
    { value: THEME_MODE.SYSTEM, label: 'System' },
    { value: THEME_MODE.LIGHT, label: 'Light' },
    { value: THEME_MODE.DARK, label: 'Dark' },
]

// Session time format options for the select
const sessionTimeFormatOptions = [
    { value: SESSION_TIME_FORMAT.TIME, label: 'Time' },
    { value: SESSION_TIME_FORMAT.RELATIVE_SHORT, label: 'Relative (short)' },
    { value: SESSION_TIME_FORMAT.RELATIVE_NARROW, label: 'Relative (narrow)' },
]

// Refs for wa-switch elements (needed to sync checked property with Web Components)
const baseModeSwitch = ref(null)
const debugSwitch = ref(null)
const fontSizeSlider = ref(null)
const themeSelect = ref(null)
const sessionTimeFormatSelect = ref(null)

// Settings from store
const baseDisplayMode = computed(() => store.getBaseDisplayMode)
const debugEnabled = computed(() => store.isDebugEnabled)
const fontSize = computed(() => store.getFontSize)
const themeMode = computed(() => store.getThemeMode)
const sessionTimeFormat = computed(() => store.getSessionTimeFormat)

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
        if (fontSizeSlider.value && fontSizeSlider.value.value !== fontSize.value) {
            fontSizeSlider.value.value = fontSize.value
        }
        if (themeSelect.value && themeSelect.value.value !== themeMode.value) {
            themeSelect.value.value = themeMode.value
        }
        if (sessionTimeFormatSelect.value && sessionTimeFormatSelect.value.value !== sessionTimeFormat.value) {
            sessionTimeFormatSelect.value.value = sessionTimeFormat.value
        }
    })
}

// Watch for store changes and sync switches
watch([isSimplified, debugEnabled, fontSize, themeMode, sessionTimeFormat], syncSwitchState, { immediate: true })

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
 * Handle font size slider change.
 */
function onFontSizeChange(event) {
    store.setFontSize(event.target.value)
}

/**
 * Handle theme mode change.
 */
function onThemeModeChange(event) {
    store.setThemeMode(event.target.value)
}

/**
 * Handle session time format change.
 */
function onSessionTimeFormatChange(event) {
    store.setSessionTimeFormat(event.target.value)
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
    <wa-tooltip for="settings-trigger">Toggle settings</wa-tooltip>
    <wa-popover for="settings-trigger" placement="top" class="settings-popover" @wa-show="onPopoverShow">
        <div class="settings-content">
            <div class="setting-group">
                <label class="setting-group-label">Theme</label>
                <wa-select
                    ref="themeSelect"
                    :value.prop="themeMode"
                    @change="onThemeModeChange"
                    size="small"
                >
                    <wa-option
                        v-for="option in themeOptions"
                        :key="option.value"
                        :value="option.value"
                    >{{ option.label }}</wa-option>
                </wa-select>
            </div>
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
            <div class="setting-group">
                <label class="setting-group-label">Font size</label>
                <wa-slider
                    ref="fontSizeSlider"
                    :min.prop="12"
                    :max.prop="32"
                    :step.prop="1"
                    :value.prop="fontSize"
                    @input="onFontSizeChange"
                    size="small"
                ></wa-slider>
            </div>
            <div class="setting-group">
                <label class="setting-group-label">Session list time display</label>
                <wa-select
                    ref="sessionTimeFormatSelect"
                    :value.prop="sessionTimeFormat"
                    @change="onSessionTimeFormatChange"
                    size="small"
                >
                    <wa-option
                        v-for="option in sessionTimeFormatOptions"
                        :key="option.value"
                        :value="option.value"
                    >{{ option.label }}</wa-option>
                </wa-select>
            </div>
        </div>
    </wa-popover>
</template>

<style scoped>
#settings-trigger {
    &:hover {
        z-index: 10;
    }
}

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
