<script setup>
// Settings → Help panel. Sibling of TipsSettings.vue. Lists every help page
// available for the current environment and lets the user reopen any of them
// (always without the "Don't show this again" switch — manual opens never
// gate). There is intentionally no enable/disable toggle and no reset button:
// help is a light, automatic nudge, not something to configure.
import { computed } from 'vue'
import { useHelpStore } from '../../stores/help'
import { useSettingsStore } from '../../stores/settings'
import { showHelp } from '../help/showHelp'

const helpStore = useHelpStore()
const settings = useSettingsStore()

const env = computed(() => ({
    platform: settings._isTouchDevice ? 'mobile' : 'desktop',
    os: settings.os,
    enabledProviders: settings.enabledProviders,
}))

const availableHelp = computed(() => {
    return helpStore.getAvailableHelp(env.value).sort((a, b) => a.title.localeCompare(b.title))
})

function onClickHelp(key) {
    // Close the Settings popover before opening the dialog, like the tips
    // list does — SettingsPopover.vue listens for this event and hides.
    window.dispatchEvent(new CustomEvent('twicc:close-settings-popover'))
    // Opened from the list for re-reading — never with the dismiss switch.
    // Defer one tick so the popover starts closing before the dialog opens.
    setTimeout(() => showHelp(key, { showDontShowAgain: false }), 0)
}
</script>

<template>
    <div class="help-settings">
        <p class="help-hint">
            Help pages open by themselves the first time you reach a feature,
            and you can reopen any of them from the list below. There are only
            a few for now — the list will grow as the app evolves.
        </p>

        <h4 class="help-list-title">All help</h4>

        <p v-if="availableHelp.length === 0" class="help-empty">
            No help available yet.
        </p>

        <ul v-else class="help-list">
            <li
                v-for="item in availableHelp"
                :key="item.key"
                class="help-row"
                tabindex="0"
                @click="onClickHelp(item.key)"
                @keydown.enter="onClickHelp(item.key)"
            >
                <div class="help-row-title">{{ item.title }}</div>
            </li>
        </ul>
    </div>
</template>

<style scoped>
.help-settings {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.help-hint {
    margin: 0;
    font-size: 0.85em;
    color: var(--wa-color-neutral-on-quiet, #888);
}

.help-list-title {
    margin: 0.5rem 0 0;
    font-size: 0.95em;
    font-weight: 600;
}

.help-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    list-style: none;
    padding: 0;
    margin: 0;
}

.help-row {
    padding: 0.5rem;
    border-radius: 0.25rem;
    cursor: pointer;
    background-color: var(--wa-color-surface-lowered, transparent);
}

.help-row:hover,
.help-row:focus-visible {
    background-color: var(--wa-color-surface-default, #eee);
    outline: none;
}

.help-row-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.help-empty {
    margin: 0.5rem 0;
    font-style: italic;
    color: var(--wa-color-neutral-on-quiet, #888);
}
</style>
