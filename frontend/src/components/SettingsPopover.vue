<script setup>
// SettingsPopover.vue - Settings button with popover panel
import { computed, ref, watch, nextTick } from 'vue'
import { useSettingsStore } from '../stores/settings'
import { DISPLAY_MODE, THEME_MODE, SESSION_TIME_FORMAT, DEFAULT_TITLE_SYSTEM_PROMPT } from '../constants'

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
const tooltipsSwitch = ref(null)
const fontSizeSlider = ref(null)
const themeSelect = ref(null)
const sessionTimeFormatSelect = ref(null)
const titleGenerationSwitch = ref(null)
const titleSystemPromptTextarea = ref(null)

// Settings from store
const baseDisplayMode = computed(() => store.getBaseDisplayMode)
const debugEnabled = computed(() => store.isDebugEnabled)
const fontSize = computed(() => store.getFontSize)
const themeMode = computed(() => store.getThemeMode)
const sessionTimeFormat = computed(() => store.getSessionTimeFormat)
const tooltipsEnabled = computed(() => store.areTooltipsEnabled)
const titleGenerationEnabled = computed(() => store.isTitleGenerationEnabled)
const titleSystemPrompt = computed(() => store.getTitleSystemPrompt)

// Check if the current prompt is the default
const isDefaultPrompt = computed(() => titleSystemPrompt.value === DEFAULT_TITLE_SYSTEM_PROMPT)

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
        if (tooltipsSwitch.value && tooltipsSwitch.value.checked !== tooltipsEnabled.value) {
            tooltipsSwitch.value.checked = tooltipsEnabled.value
        }
        if (titleGenerationSwitch.value && titleGenerationSwitch.value.checked !== titleGenerationEnabled.value) {
            titleGenerationSwitch.value.checked = titleGenerationEnabled.value
        }
        if (titleSystemPromptTextarea.value && titleSystemPromptTextarea.value.value !== titleSystemPrompt.value) {
            titleSystemPromptTextarea.value.value = titleSystemPrompt.value
        }
    })
}

// Watch for store changes and sync switches
watch([isSimplified, debugEnabled, fontSize, themeMode, sessionTimeFormat, tooltipsEnabled, titleGenerationEnabled, titleSystemPrompt], syncSwitchState, { immediate: true })

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
 * Toggle tooltips.
 */
function onTooltipsChange(event) {
    store.setTooltipsEnabled(event.target.checked)
}

/**
 * Toggle title generation.
 */
function onTitleGenerationChange(event) {
    store.setTitleGenerationEnabled(event.target.checked)
}

/**
 * Handle title system prompt change.
 */
function onTitleSystemPromptChange(event) {
    store.setTitleSystemPrompt(event.target.value)
}

/**
 * Reset title system prompt to default.
 */
function resetTitleSystemPrompt() {
    store.resetTitleSystemPrompt()
    // Sync the textarea value
    if (titleSystemPromptTextarea.value) {
        titleSystemPromptTextarea.value.value = DEFAULT_TITLE_SYSTEM_PROMPT
    }
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
        <wa-icon name="gear"></wa-icon><span>Settings</span>
    </wa-button>
    <wa-tooltip v-if="tooltipsEnabled" for="settings-trigger">Toggle settings</wa-tooltip>
    <wa-popover for="settings-trigger" placement="top" class="settings-popover" @wa-show="onPopoverShow">
        <div class="settings-content">
            <div class="settings-sections">
                <!-- Global Section -->
                <section class="settings-section">
                    <h3 class="settings-section-title">Global</h3>
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
                        <label class="setting-group-label">Tooltips</label>
                        <wa-switch
                            ref="tooltipsSwitch"
                            @change="onTooltipsChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                </section>

                <!-- Sessions Section -->
                <section class="settings-section">
                    <h3 class="settings-section-title">Sessions</h3>
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
                        <label class="setting-group-label">Time display</label>
                        <wa-select
                            ref="sessionTimeFormatSelect"
                            :value.prop="sessionTimeFormat"
                            @change="onSessionTimeFormatChange"
                            size="small"
                            class="session-time-format-select"
                        >
                            <wa-option
                                v-for="option in sessionTimeFormatOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
                    </div>
                </section>

                <!-- Auto Title Section -->
                <section class="settings-section">
                    <h3 class="settings-section-title">Auto title</h3>
                    <div class="setting-group">
                        <wa-switch
                            ref="titleGenerationSwitch"
                            @change="onTitleGenerationChange"
                            size="small"
                        >Enabled (Haiku)</wa-switch>
                        <div v-if="titleGenerationEnabled" class="title-prompt-section">
                            <label class="setting-group-label">System prompt</label>
                            <wa-textarea
                                ref="titleSystemPromptTextarea"
                                :value.prop="titleSystemPrompt"
                                @input="onTitleSystemPromptChange"
                                size="small"
                                rows="4"
                                resize="vertical"
                                class="title-prompt-textarea"
                            ></wa-textarea>
                            <div class="title-prompt-hint">
                                <span>Use <code>{text}</code> as placeholder.</span>
                                <wa-button
                                    v-if="!isDefaultPrompt"
                                    variant="neutral"
                                    appearance="outlined"
                                    size="small"
                                    @click.stop="resetTitleSystemPrompt"
                                >Reset to default</wa-button>
                            </div>
                        </div>
                    </div>
                </section>
            </div>
        </div>
    </wa-popover>
</template>

<style scoped>
#settings-trigger::part(label) {
    display: flex;
    gap: var(--wa-space-s);
}

.settings-popover {
    --max-width: 90vw;
    --arrow-size: 16px;
    &::part(body) {
        width: fit-content;
    }
}

.settings-content {
    max-height: 90vw;
    overflow-y: auto;
}

.settings-sections {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-m);
}

.settings-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    padding: var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-radius-m);
    --min-width: 15rem;
    max-width: 20rem;
    min-width: var(--min-width);
    flex: 1 1 var(--min-width); /* grow, shrink, basis */
}

.settings-section-title {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-bold);
    color: var(--wa-color-text-loud);
    margin: 0;
    padding-bottom: var(--wa-space-xs);
    border-bottom: 1px solid var(--wa-color-border-subtle);
}

.setting-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.setting-group-label {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-quiet);
}

.title-prompt-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-xs);
}

.title-prompt-textarea {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-xs);
}

.title-prompt-hint {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);

    code {
        background: var(--wa-color-surface);
        padding: 0 var(--wa-space-2xs);
        border-radius: var(--wa-radius-s);
    }

    wa-button {
        align-self: end;
    }
}

</style>

<style>
@container sidebar (width <= 13rem) {
    #settings-trigger {
        &::part(base) {
            padding: var(--wa-space-s);
        }
        & > span {
            display: none;
        }
    }
}
</style>
