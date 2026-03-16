<script setup>
// SettingsPopover.vue - Settings button with popover panel
import { computed, ref, useId } from 'vue'
import { useRouter } from 'vue-router'
import { useSettingsStore } from '../stores/settings'
import { useDataStore } from '../stores/data'
import { useAuthStore } from '../stores/auth'
import { DISPLAY_MODE, THEME_MODE, SESSION_TIME_FORMAT, DEFAULT_TITLE_SYSTEM_PROMPT, DEFAULT_MAX_CACHED_SESSIONS, PERMISSION_MODE, PERMISSION_MODE_LABELS, PERMISSION_MODE_DESCRIPTIONS, MODEL, MODEL_LABELS, EFFORT, EFFORT_LABELS, THINKING, THINKING_LABELS, CLAUDE_IN_CHROME, CLAUDE_IN_CHROME_LABELS, CONTEXT_MAX, CONTEXT_MAX_LABELS } from '../constants'
import NotificationSettings from './NotificationSettings.vue'
import AppTooltip from './AppTooltip.vue'

const router = useRouter()
const store = useSettingsStore()
const dataStore = useDataStore()
const authStore = useAuthStore()

// Show logout button only when password-based auth is active
const showLogout = computed(() => authStore.passwordRequired && authStore.authenticated)
const logoutButtonId = useId()

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

const notificationSettingsRef = ref(null)

// Settings from store
const displayMode = computed(() => store.getDisplayMode)
const fontSize = computed(() => store.getFontSize)
const themeMode = computed(() => store.getThemeMode)
const sessionTimeFormat = computed(() => store.getSessionTimeFormat)
const showCosts = computed(() => store.areCostsShown)
const extraUsageOnlyWhenNeeded = computed(() => store.isExtraUsageOnlyWhenNeeded)
const maxCachedSessions = computed(() => store.getMaxCachedSessions)
const autoUnpinOnArchive = computed(() => store.isAutoUnpinOnArchive)
const titleGenerationEnabled = computed(() => store.isTitleGenerationEnabled)
const titleSystemPrompt = computed(() => store.getTitleSystemPrompt)
const terminalUseTmux = computed(() => store.isTerminalUseTmux)
const compactSessionList = computed(() => store.isCompactSessionList)
const defaultPermissionMode = computed(() => store.getDefaultPermissionMode)
const alwaysApplyDefaultPermissionMode = computed(() => store.isAlwaysApplyDefaultPermissionMode)
const defaultModel = computed(() => store.getDefaultModel)
const alwaysApplyDefaultModel = computed(() => store.isAlwaysApplyDefaultModel)
const defaultEffort = computed(() => store.getDefaultEffort)
const alwaysApplyDefaultEffort = computed(() => store.isAlwaysApplyDefaultEffort)
const defaultThinking = computed(() => store.getDefaultThinking)
const alwaysApplyDefaultThinking = computed(() => store.isAlwaysApplyDefaultThinking)
const defaultClaudeInChrome = computed(() => store.getDefaultClaudeInChrome)
const alwaysApplyDefaultClaudeInChrome = computed(() => store.isAlwaysApplyDefaultClaudeInChrome)
const defaultContextMax = computed(() => store.getDefaultContextMax)
const alwaysApplyDefaultContextMax = computed(() => store.isAlwaysApplyDefaultContextMax)
const showDiffs = computed(() => store.isShowDiffs)
const diffSideBySide = computed(() => store.isDiffSideBySide)
const editorWordWrap = computed(() => store.isEditorWordWrap)

// Check if the current prompt is the default
const isDefaultPrompt = computed(() => titleSystemPrompt.value === DEFAULT_TITLE_SYSTEM_PROMPT)

// Server info for footer
const currentVersion = computed(() => dataStore.currentVersion)
const latestVersion = computed(() => dataStore.latestVersion)
const claudeStatus = computed(() => dataStore.claudeStatus)

/**
 * Status display configuration for Claude Code component statuses.
 * Maps Atlassian Statuspage status values to UI labels and CSS modifier classes.
 */
const CLAUDE_STATUS_DISPLAY = {
    operational: { label: 'Operational', modifier: 'ok' },
    degraded_performance: { label: 'Degraded', modifier: 'warning' },
    partial_outage: { label: 'Partial outage', modifier: 'warning' },
    major_outage: { label: 'Major outage', modifier: 'error' },
    under_maintenance: { label: 'Maintenance', modifier: 'info' },
}

const claudeStatusDisplay = computed(() => {
    return CLAUDE_STATUS_DISPLAY[claudeStatus.value] || { label: claudeStatus.value, modifier: 'ok' }
})

// Display mode options for the select
const displayModeOptions = [
    { value: DISPLAY_MODE.CONVERSATION, label: 'Conversation' },
    { value: DISPLAY_MODE.SIMPLIFIED, label: 'Simplified' },
    { value: DISPLAY_MODE.NORMAL, label: 'Detailed' },
    { value: DISPLAY_MODE.DEBUG, label: 'Debug' },
]

// Permission mode options for the select
const permissionModeOptions = Object.values(PERMISSION_MODE).map(value => ({
    value,
    label: PERMISSION_MODE_LABELS[value],
    description: PERMISSION_MODE_DESCRIPTIONS[value],
}))

// Model options for the select
const modelOptions = Object.values(MODEL).map(value => ({
    value,
    label: MODEL_LABELS[value],
}))

// Effort options for the select
const effortOptions = Object.values(EFFORT).map(value => ({
    value,
    label: EFFORT_LABELS[value],
}))

// Thinking options for the select (use string values for wa-select compatibility)
const thinkingOptions = [
    { value: 'true', label: THINKING_LABELS[true] },
    { value: 'false', label: THINKING_LABELS[false] },
]

// Claude in Chrome options for the select (use string values for wa-select compatibility)
const claudeInChromeOptions = [
    { value: 'true', label: CLAUDE_IN_CHROME_LABELS[true] },
    { value: 'false', label: CLAUDE_IN_CHROME_LABELS[false] },
]

// Context max options for the select (use string values for wa-select compatibility)
const contextMaxOptions = Object.values(CONTEXT_MAX).map(value => ({
    value: String(value),
    label: CONTEXT_MAX_LABELS[value],
}))

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
 * Toggle costs display.
 */
function onShowCostsChange(event) {
    store.setShowCosts(event.target.checked)
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
 * Handle default permission mode change.
 */
function onDefaultPermissionModeChange(event) {
    store.setDefaultPermissionMode(event.target.value)
}

/**
 * Toggle "always apply default permission mode" setting.
 */
function onAlwaysApplyDefaultPermissionModeChange(event) {
    store.setAlwaysApplyDefaultPermissionMode(event.target.checked)
}

/**
 * Handle default model change.
 */
function onDefaultModelChange(event) {
    store.setDefaultModel(event.target.value)
}

/**
 * Toggle "always apply default model" setting.
 */
function onAlwaysApplyDefaultModelChange(event) {
    store.setAlwaysApplyDefaultModel(event.target.checked)
}

/**
 * Handle default effort change.
 */
function onDefaultEffortChange(event) {
    store.setDefaultEffort(event.target.value)
}

/**
 * Toggle "always apply default effort" setting.
 */
function onAlwaysApplyDefaultEffortChange(event) {
    store.setAlwaysApplyDefaultEffort(event.target.checked)
}

/**
 * Handle default thinking change.
 */
function onDefaultThinkingChange(event) {
    store.setDefaultThinking(event.target.value === 'true')
}

/**
 * Toggle "always apply default thinking" setting.
 */
function onAlwaysApplyDefaultThinkingChange(event) {
    store.setAlwaysApplyDefaultThinking(event.target.checked)
}

/**
 * Handle default Claude in Chrome change.
 */
function onDefaultClaudeInChromeChange(event) {
    store.setDefaultClaudeInChrome(event.target.value === 'true')
}

/**
 * Toggle "always apply default Claude in Chrome" setting.
 */
function onAlwaysApplyDefaultClaudeInChromeChange(event) {
    store.setAlwaysApplyDefaultClaudeInChrome(event.target.checked)
}

/**
 * Handle default context max change.
 */
function onDefaultContextMaxChange(event) {
    store.setDefaultContextMax(Number(event.target.value))
}

/**
 * Toggle "always apply default context max" setting.
 */
function onAlwaysApplyDefaultContextMaxChange(event) {
    store.setAlwaysApplyDefaultContextMax(event.target.checked)
}

/**
 * Toggle compact session list.
 */
function onCompactSessionListChange(event) {
    store.setCompactSessionList(event.target.checked)
}

/**
 * Toggle show diffs (auto-expand Edit/Write details).
 */
function onShowDiffsChange(event) {
    store.setShowDiffs(event.target.checked)
}

/**
 * Toggle diff side-by-side default.
 */
function onDiffSideBySideChange(event) {
    store.setDiffSideBySide(event.target.checked)
}

/**
 * Toggle editor word wrap default.
 */
function onEditorWordWrapChange(event) {
    store.setEditorWordWrap(event.target.checked)
}

/**
 * Reset title system prompt to default.
 */
function resetTitleSystemPrompt() {
    store.resetTitleSystemPrompt()
}

/**
 * Called when popover opens - refresh notification permission state.
 */
function onPopoverShow() {
    notificationSettingsRef.value?.sync()
}
</script>

<template>
    <wa-button id="settings-trigger" variant="neutral" appearance="filled-outlined" size="small">
        <wa-icon name="gear"></wa-icon><span>Settings</span>
    </wa-button>
    <AppTooltip for="settings-trigger">Toggle settings</AppTooltip>
    <wa-popover for="settings-trigger" placement="top" class="settings-popover" @wa-show="onPopoverShow">
        <AppTooltip v-if="showLogout" :for="logoutButtonId">Logout</AppTooltip>
        <div class="settings-content">
            <div class="settings-sections">
                <!-- Synced Settings Notice -->
                <section class="settings-section settings-notice">
                    <p>
                        <wa-icon name="cloud" class="synced-icon"></wa-icon>
                        Sections and individual settings marked with a cloud icon are synced across all your devices.
                        Others are specific to this browser.
                    </p>
                </section>

                <!-- Global Section -->
                <section class="settings-section">
                    <h3 class="settings-section-title">Global</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Theme</label>
                        <wa-select
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
                            :min.prop="12"
                            :max.prop="32"
                            :step.prop="1"
                            :value.prop="fontSize"
                            @input="onFontSizeChange"
                            size="small"
                        ></wa-slider>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Show costs</label>
                        <wa-switch
                            :checked="showCosts"
                            @change="onShowCostsChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <div class="setting-group" v-if="showExtraUsageSetting">
                        <label class="setting-group-label">Show extra usage quota</label>
                        <wa-switch
                            :checked="extraUsageOnlyWhenNeeded"
                            @change="onExtraUsageOnlyWhenNeededChange"
                            size="small"
                        >Only when needed</wa-switch>
                    </div>
                </section>

                <!-- Claude Settings Section -->
                <section class="settings-section">
                    <h3 class="settings-section-title">Claude settings <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Default permission mode</label>
                        <wa-select
                            :value.prop="defaultPermissionMode"
                            @change="onDefaultPermissionModeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in permissionModeOptions"
                                :key="option.value"
                                :value="option.value"
                                :label="option.label"
                            >
                                <span>{{ option.label }}</span>
                                <span class="option-description">{{ option.description }}</span>
                            </wa-option>
                        </wa-select>
                        <wa-switch
                            :checked="alwaysApplyDefaultPermissionMode"
                            @change="onAlwaysApplyDefaultPermissionModeChange"
                            size="small"
                        >Always apply *</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default model</label>
                        <wa-select
                            :value.prop="defaultModel"
                            @change="onDefaultModelChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in modelOptions"
                                :key="option.value"
                                :value="option.value"
                            >
                                {{ option.label }}
                            </wa-option>
                        </wa-select>
                        <wa-switch
                            :checked="alwaysApplyDefaultModel"
                            @change="onAlwaysApplyDefaultModelChange"
                            size="small"
                        >Always apply *</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default context size</label>
                        <wa-select
                            :value.prop="String(defaultContextMax)"
                            @change="onDefaultContextMaxChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in contextMaxOptions"
                                :key="option.value"
                                :value="option.value"
                            >
                                {{ option.label }}
                            </wa-option>
                        </wa-select>
                        <wa-switch
                            :checked="alwaysApplyDefaultContextMax"
                            @change="onAlwaysApplyDefaultContextMaxChange"
                            size="small"
                        >Always apply *</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default effort</label>
                        <wa-select
                            :value.prop="defaultEffort"
                            @change="onDefaultEffortChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in effortOptions"
                                :key="option.value"
                                :value="option.value"
                            >
                                {{ option.label }}
                            </wa-option>
                        </wa-select>
                        <wa-switch
                            :checked="alwaysApplyDefaultEffort"
                            @change="onAlwaysApplyDefaultEffortChange"
                            size="small"
                        >Always apply *</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default thinking</label>
                        <wa-select
                            :value.prop="String(defaultThinking)"
                            @change="onDefaultThinkingChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in thinkingOptions"
                                :key="option.value"
                                :value="option.value"
                            >
                                {{ option.label }}
                            </wa-option>
                        </wa-select>
                        <wa-switch
                            :checked="alwaysApplyDefaultThinking"
                            @change="onAlwaysApplyDefaultThinkingChange"
                            size="small"
                        >Always apply *</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default Chrome MCP</label>
                        <wa-select
                            :value.prop="String(defaultClaudeInChrome)"
                            @change="onDefaultClaudeInChromeChange"
                            size="small"
                        >
                            <wa-option
                                v-for="option in claudeInChromeOptions"
                                :key="option.value"
                                :value="option.value"
                            >
                                {{ option.label }}
                            </wa-option>
                        </wa-select>
                        <wa-switch
                            :checked="alwaysApplyDefaultClaudeInChrome"
                            @change="onAlwaysApplyDefaultClaudeInChromeChange"
                            size="small"
                        >Always apply *</wa-switch>
                        <span class="setting-group-hint">Only applies to new sessions.</span>
                    </div>
                    <div>
                        <span class="setting-group-hint">* Override the per-session saved value with the default one.</span>
                    </div>
                </section>

                <!-- Notifications Section -->
                <NotificationSettings ref="notificationSettingsRef" />

                <!-- Sessions Section -->
                <section class="settings-section">
                    <h3 class="settings-section-title">Sessions</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Display mode</label>
                        <wa-select
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
                        <label class="setting-group-label">Auto open edit diffs</label>
                        <wa-switch
                            :checked="showDiffs"
                            @change="onShowDiffsChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Time display</label>
                        <wa-select
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
                        <label class="setting-group-label">Auto-unpin on archive <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="autoUnpinOnArchive"
                            @change="onAutoUnpinOnArchiveChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Compact session list</label>
                        <wa-switch
                            :checked="compactSessionList"
                            @change="onCompactSessionListChange"
                            size="small"
                        >Enabled</wa-switch>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">LRU cached sessions ({{ maxCachedSessions }})</label>
                        <wa-slider
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
                    <h3 class="settings-section-title">Auto title <wa-icon name="cloud" class="synced-icon"></wa-icon></h3>
                    <div class="setting-group">
                        <wa-switch
                            :checked="titleGenerationEnabled"
                            @change="onTitleGenerationChange"
                            size="small"
                        >Enabled (Haiku)</wa-switch>
                        <div v-if="titleGenerationEnabled" class="title-prompt-section">
                            <label class="setting-group-label">System prompt</label>
                            <wa-textarea
                                :value.prop="titleSystemPrompt"
                                @input="onTitleSystemPromptChange"
                                size="small"
                                rows="7"
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

                <!-- Editor Section -->
                <section class="settings-section">
                    <h3 class="settings-section-title">Editor</h3>
                    <div class="setting-group">
                        <label class="setting-group-label">Word wrap</label>
                        <wa-switch
                            :checked="editorWordWrap"
                            @change="onEditorWordWrapChange"
                            size="small"
                        >Enabled</wa-switch>
                        <span class="setting-group-hint">Wrap long lines in the editor.</span>
                    </div>
                    <div class="setting-group">
                        <label class="setting-group-label">Default diff layout</label>
                        <wa-switch
                            :checked="diffSideBySide"
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
                        <label class="setting-group-label">Persistent sessions (tmux) <wa-icon name="cloud" class="synced-icon"></wa-icon></label>
                        <wa-switch
                            :checked="terminalUseTmux"
                            @change="onTmuxChange"
                            size="small"
                        >Enabled</wa-switch>
                        <span class="setting-group-hint">Tmux sessions are destroyed when Claude sessions are archived.</span>
                    </div>
                </section>
            </div>
        </div>
        <wa-divider></wa-divider>
        <footer v-if="currentVersion" class="settings-footer">
            <span class="settings-footer-version">
                <a href="https://github.com/twidi/twicc/" target="_blank" rel="noopener">TwiCC v{{ currentVersion }}</a>
                <template v-if="latestVersion">
                    &rarr;
                    <a :href="latestVersion.releaseUrl" target="_blank" rel="noopener">v{{ latestVersion.version }} available</a>
                </template>
            </span>
            ·
            <a href="https://github.com/sponsors/twidi" target="_blank" rel="noopener" class="settings-footer-sponsor">
                <span class="settings-footer-sponsor-icon"></span>
                Sponsor
            </a>
            ·
            <a
                href="https://status.claude.com/"
                target="_blank"
                rel="noopener"
                class="settings-footer-status"
                :class="`settings-footer-status--${claudeStatusDisplay.modifier}`"
                id="claude-status"
            >
                <span class="status-dot"></span>
                CC: {{ claudeStatusDisplay.label }}
            </a>
            <AppTooltip for="claude-status">Claude code status on Anthropic's side</AppTooltip>
            <wa-button
                v-if="showLogout"
                :id="logoutButtonId"
                class="logout-button"
                variant="danger"
                appearance="plain"
                size="small"
                @click="handleLogout"
            >
                <wa-icon name="right-from-bracket"></wa-icon>
            </wa-button>
        </footer>
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
}

.settings-popover::part(body) {
    padding: 0;
}

.settings-content {
    padding: var(--wa-space-m);
    max-height: calc(90vh - 8rem);
    overflow-y: auto;
}

@media (width < 640px) {
    .settings-content {
        padding: var(--wa-space-s);
    }
}

.settings-sections {
    columns: 15rem;
    column-gap: var(--wa-space-m);
    width: 90vw;
    max-width: 100%;
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

.option-description {
    display: block;
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
}

.synced-icon {
    color: var(--wa-color-brand);
}

wa-popover > wa-divider {
    --width: 4px;
    --spacing: 0;
}

.settings-footer {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    column-gap: var(--wa-space-xs);
    padding: var(--wa-space-s);
    margin-right: 2rem;
    border-top: 1px solid var(--wa-color-border-subtle);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.settings-footer a {
    color: inherit;
    text-decoration: underline;
    text-decoration-style: dotted;
    text-underline-offset: 2px;
}

.settings-footer a:hover {
    color: var(--wa-color-text);
}

.logout-button {
    position: absolute;
    right: 0;
}

.settings-footer-version {
    white-space: nowrap;
}

.settings-footer-status {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    white-space: nowrap;
    text-decoration: none !important;
}

.settings-footer-status:hover {
    text-decoration: underline !important;
}

.status-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}

.settings-footer-status--ok .status-dot {
    background-color: var(--wa-color-success);
}

.settings-footer-status--warning .status-dot {
    background-color: var(--wa-color-warning);
}

.settings-footer-status--error .status-dot {
    background-color: var(--wa-color-danger);
}

.settings-footer-status--info .status-dot {
    background-color: var(--wa-color-primary);
}

.settings-notice p {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    margin: 0;
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-xs);

    .synced-icon {
        font-size: 1em;
        position: relative;
        top: 0.1em;
        flex-shrink: 0;
    }
}

</style>

<style>
/* Shared styles for settings sections (used by child components like NotificationSettings) */
.settings-sections .settings-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    padding: var(--wa-space-s);
    background: var(--wa-color-surface-alt);
    border-radius: var(--wa-border-radius-m);
    border: solid 1px var(--wa-color-border-quiet);
    break-inside: avoid;
    margin-bottom: var(--wa-space-m);
}

.settings-sections .settings-section-title {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-bold);
    margin: 0;
    padding-bottom: var(--wa-space-xs);
    border-bottom: 1px solid var(--wa-color-border-subtle);
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.settings-sections .setting-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    > label ~ :not(label) {
        margin-left: var(--wa-space-s);
    }
}

.settings-sections .setting-group-label {
    font-size: var(--wa-font-size-m);
    font-weight: var(--wa-font-weight-semibold);
    xcolor: var(--wa-color-text-quiet);
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.settings-sections .setting-group-hint {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

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
