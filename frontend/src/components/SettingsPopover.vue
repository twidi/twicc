<script setup>
// SettingsPopover.vue - Settings button with popover panel
import { computed, ref, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore } from '../stores/settings'
import { useDataStore } from '../stores/data'
import { useAuthStore } from '../stores/auth'
import { DISPLAY_MODE, THEME_MODE, SESSION_TIME_FORMAT, DEFAULT_TITLE_SYSTEM_PROMPT, DEFAULT_MAX_CACHED_SESSIONS } from '../constants'
import NotificationSettings from './NotificationSettings.vue'

const router = useRouter()
const store = useSettingsStore()
const dataStore = useDataStore()
const authStore = useAuthStore()

// Show logout button only when password-based auth is active
const showLogout = computed(() => authStore.passwordRequired && authStore.authenticated)

// Show extra usage setting only when OAuth is configured
const showExtraUsageSetting = computed(() => dataStore.usage?.hasOauth ?? false)

function handleLogout() {
    router.push({ name: 'logout' })
}

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
const displayModeSelect = ref(null)
const tooltipsSwitch = ref(null)
const fontSizeSlider = ref(null)
const themeSelect = ref(null)
const sessionTimeFormatSelect = ref(null)
const extraUsageOnlyWhenNeededSwitch = ref(null)
const maxCachedSessionsSlider = ref(null)
const autoUnpinOnArchiveSwitch = ref(null)
const titleGenerationSwitch = ref(null)
const titleSystemPromptTextarea = ref(null)
const tmuxSwitch = ref(null)
const diffSideBySideSwitch = ref(null)
const notificationSettingsRef = ref(null)

// Settings from store
const displayMode = computed(() => store.getDisplayMode)
const fontSize = computed(() => store.getFontSize)
const themeMode = computed(() => store.getThemeMode)
const sessionTimeFormat = computed(() => store.getSessionTimeFormat)
const tooltipsEnabled = computed(() => store.areTooltipsEnabled)
const extraUsageOnlyWhenNeeded = computed(() => store.isExtraUsageOnlyWhenNeeded)
const maxCachedSessions = computed(() => store.getMaxCachedSessions)
const autoUnpinOnArchive = computed(() => store.isAutoUnpinOnArchive)
const titleGenerationEnabled = computed(() => store.isTitleGenerationEnabled)
const titleSystemPrompt = computed(() => store.getTitleSystemPrompt)
const terminalUseTmux = computed(() => store.isTerminalUseTmux)
const diffSideBySide = computed(() => store.isDiffSideBySide)

// Check if the current prompt is the default
const isDefaultPrompt = computed(() => titleSystemPrompt.value === DEFAULT_TITLE_SYSTEM_PROMPT)

// Display mode options for the select
const displayModeOptions = [
    { value: DISPLAY_MODE.CONVERSATION, label: 'Conversation' },
    { value: DISPLAY_MODE.SIMPLIFIED, label: 'Simplified' },
    { value: DISPLAY_MODE.NORMAL, label: 'Detailed' },
    { value: DISPLAY_MODE.DEBUG, label: 'Debug' },
]

// Sync switch checked state with store values
// Web Components don't always pick up initial prop values from Vue bindings
function syncSwitchState() {
    nextTick(() => {
        if (displayModeSelect.value && displayModeSelect.value.value !== displayMode.value) {
            displayModeSelect.value.value = displayMode.value
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
        if (extraUsageOnlyWhenNeededSwitch.value && extraUsageOnlyWhenNeededSwitch.value.checked !== extraUsageOnlyWhenNeeded.value) {
            extraUsageOnlyWhenNeededSwitch.value.checked = extraUsageOnlyWhenNeeded.value
        }
        if (maxCachedSessionsSlider.value && maxCachedSessionsSlider.value.value !== maxCachedSessions.value) {
            maxCachedSessionsSlider.value.value = maxCachedSessions.value
        }
        if (autoUnpinOnArchiveSwitch.value && autoUnpinOnArchiveSwitch.value.checked !== autoUnpinOnArchive.value) {
            autoUnpinOnArchiveSwitch.value.checked = autoUnpinOnArchive.value
        }
        if (titleGenerationSwitch.value && titleGenerationSwitch.value.checked !== titleGenerationEnabled.value) {
            titleGenerationSwitch.value.checked = titleGenerationEnabled.value
        }
        if (titleSystemPromptTextarea.value && titleSystemPromptTextarea.value.value !== titleSystemPrompt.value) {
            titleSystemPromptTextarea.value.value = titleSystemPrompt.value
        }
        if (tmuxSwitch.value && tmuxSwitch.value.checked !== terminalUseTmux.value) {
            tmuxSwitch.value.checked = terminalUseTmux.value
        }
        if (diffSideBySideSwitch.value && diffSideBySideSwitch.value.checked !== diffSideBySide.value) {
            diffSideBySideSwitch.value.checked = diffSideBySide.value
        }
    })
}

// Watch for store changes and sync switches
watch([displayMode, fontSize, themeMode, sessionTimeFormat, tooltipsEnabled, extraUsageOnlyWhenNeeded, maxCachedSessions, autoUnpinOnArchive, titleGenerationEnabled, titleSystemPrompt, terminalUseTmux, diffSideBySide], syncSwitchState, { immediate: true })

/**
 * Handle display mode change.
 */
function onDisplayModeChange(event) {
    store.setDisplayMode(event.target.value)
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
 * Toggle extra usage "only when needed" mode.
 */
function onExtraUsageOnlyWhenNeededChange(event) {
    store.setExtraUsageOnlyWhenNeeded(event.target.checked)
}

/**
 * Handle max cached sessions slider change.
 */
function onMaxCachedSessionsChange(event) {
    store.setMaxCachedSessions(event.target.value)
}

/**
 * Toggle auto-unpin on archive.
 */
function onAutoUnpinOnArchiveChange(event) {
    store.setAutoUnpinOnArchive(event.target.checked)
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
 * Toggle terminal tmux persistence.
 */
function onTmuxChange(event) {
    store.setTerminalUseTmux(event.target.checked)
}

/**
 * Toggle diff side-by-side default.
 */
function onDiffSideBySideChange(event) {
    store.setDiffSideBySide(event.target.checked)
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
    notificationSettingsRef.value?.sync()
}
</script>

<template>
    <wa-button id="settings-trigger" variant="neutral" appearance="filled-outlined" size="small">
        <wa-icon name="gear"></wa-icon><span>Settings</span>
    </wa-button>
    <wa-tooltip v-if="tooltipsEnabled" for="settings-trigger">Toggle settings</wa-tooltip>
    <wa-popover for="settings-trigger" placement="top" class="settings-popover" @wa-show="onPopoverShow">
        <div class="settings-content">
            <wa-button
                v-if="showLogout"
                class="logout-button"
                variant="danger"
                appearance="plain"
                size="small"
                @click="handleLogout"
            >
                <wa-icon name="right-from-bracket"></wa-icon>
            </wa-button>
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
                        <label class="setting-group-label">Font size ({{fontSize}}px)</label>
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
                    <div class="setting-group" v-if="showExtraUsageSetting">
                        <label class="setting-group-label">Show extra usage quota</label>
                        <wa-switch
                            ref="extraUsageOnlyWhenNeededSwitch"
                            @change="onExtraUsageOnlyWhenNeededChange"
                            size="small"
                        >Only when needed</wa-switch>
                    </div>
                </section>

                <!-- Sessions Section -->
                <section class="settings-section">
                    <h3 class="settings-section-title">Sessions</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Display mode</label>
                        <wa-select
                            ref="displayModeSelect"
                            :value.prop="displayMode"
                            @change="onDisplayModeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in displayModeOptions"
                                :key="option.value"
                                :value="option.value"
                            >{{ option.label }}</wa-option>
                        </wa-select>
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
                    <div class="setting-group">
                        <label class="setting-group-label">Auto-unpin on archive</label>
                        <wa-switch
                            ref="autoUnpinOnArchiveSwitch"
                            @change="onAutoUnpinOnArchiveChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">LRU cached sessions ({{ maxCachedSessions }})</label>
                        <wa-slider
                            ref="maxCachedSessionsSlider"
                            :min.prop="1"
                            :max.prop="50"
                            :step.prop="1"
                            :value.prop="maxCachedSessions"
                            @input="onMaxCachedSessionsChange"
                            size="small"
                        ></wa-slider>
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

                <!-- Notifications Section -->
                <NotificationSettings ref="notificationSettingsRef" />

                <!-- Editor Section -->
                <section class="settings-section">
                    <h3 class="settings-section-title">Editor</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Default diff layout</label>
                        <wa-switch
                            ref="diffSideBySideSwitch"
                            @change="onDiffSideBySideChange"
                            size="small"
                        >Side by side</wa-switch>
                        <span class="setting-group-hint">Inactive if the screen is too narrow.</span>
                    </div>
                </section>

                <!-- Terminal Section -->
                <section class="settings-section">
                    <h3 class="settings-section-title">Terminal</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Persistent sessions (tmux)</label>
                        <wa-switch
                            ref="tmuxSwitch"
                            @change="onTmuxChange"
                            size="small"
                        >Enabled</wa-switch>
                        <span class="setting-group-hint">Tmux sessions are destroyed when Claude sessions are archived.</span>
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
    max-height: calc(100vh - 8rem);
    overflow-y: auto;
}

.logout-button {
    position: absolute;
    top: var(--wa-space-s);
    right: var(--wa-space-s);
    z-index: 1;
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

.setting-group-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.title-prompt-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-xs);
}

.title-prompt-textarea {
    font-family: var(--wa-font-family-code);
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
