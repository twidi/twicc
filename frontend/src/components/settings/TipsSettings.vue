<script setup>
import { useI18n } from 'vue-i18n'
const { t, locale } = useI18n()
import { computed, ref } from 'vue'
import { useTipsStore } from '../../stores/tips'
import { useSettingsStore } from '../../stores/settings'
import { formatRelative } from '../../utils/date'
import { showTipToast } from '../tips/showTipToast'

const tipsStore = useTipsStore()
const settings = useSettingsStore()
const confirmingReset = ref(false)

const env = computed(() => ({
    platform: settings._isTouchDevice ? 'mobile' : 'desktop',
    os: settings.os,
    enabledProviders: settings.enabledProviders,
}))

const availableTips = computed(() => {
    return tipsStore.getAvailableTips(env.value).sort((a, b) => a.title.localeCompare(b.title, locale.value))
})

const seenCount = computed(() => Object.keys(tipsStore.seenTips).length)

function isSeen(key) {
    return key in tipsStore.seenTips
}

function seenLabel(key) {
    const iso = tipsStore.seenTips[key]
    if (!iso) return t('settings.tips.notSeen')
    const ms = Date.parse(iso)
    if (Number.isNaN(ms)) return t('settings.tips.seen')
    return t('settings.tips.seenAt', { time: formatRelative(ms) })
}

function onToggle(value) {
    tipsStore.setEnabled(value)
}

function onClickTip(key) {
    // Close the Settings popover before showing the toast — see spec § 6.6.
    // SettingsPopover.vue listens for this event and calls hide() on its
    // wa-popover.
    window.dispatchEvent(new CustomEvent('twicc:close-settings-popover'))
    // Defer one tick so the popover starts closing before the toast pushes.
    setTimeout(() => showTipToast(key), 0)
}

function onResetClick() {
    confirmingReset.value = true
}

function onResetConfirm() {
    tipsStore.resetAllSeen()
    confirmingReset.value = false
}

function onResetCancel() {
    confirmingReset.value = false
}
</script>

<template>
    <div class="tips-settings">
        <label class="tips-toggle">
            <input
                type="checkbox"
                :checked="tipsStore.enabled"
                @change="onToggle($event.target.checked)"
            />
            <span>{{ $t('settings.tips.autoDisplay') }}</span>
        </label>
        <p v-if="!tipsStore.enabled" class="tips-hint">
            {{ $t('settings.tips.manualHint') }}
        </p>

        <div class="tips-reset">
            <wa-button
                size="small"
                :disabled="seenCount === 0"
                @click="onResetClick"
            >
                {{ $t('settings.tips.resetAll') }}
            </wa-button>
            <wa-dialog v-if="confirmingReset" open @wa-after-hide="onResetCancel">
                <span slot="label">{{ $t('settings.tips.resetSeen') }}</span>
                <p>
                    {{ $t('settings.tips.resetHint') }}
                </p>
                <wa-button slot="footer" @click="onResetCancel">{{ $t('common.actions.cancel') }}</wa-button>
                <wa-button slot="footer" variant="brand" @click="onResetConfirm">{{ $t('settings.tips.reset') }}</wa-button>
            </wa-dialog>
        </div>

        <h4 class="tips-list-title">{{ $t('settings.tips.all') }}</h4>

        <p v-if="availableTips.length === 0" class="tips-empty">
            {{ $t('settings.tips.empty') }}
        </p>

        <ul v-else class="tips-list">
            <li
                v-for="tip in availableTips"
                :key="tip.key"
                class="tips-row"
                :class="{ seen: isSeen(tip.key) }"
                tabindex="0"
                @click="onClickTip(tip.key)"
                @keydown.enter="onClickTip(tip.key)"
            >
                <wa-icon :name="isSeen(tip.key) ? 'check' : 'circle'" class="tips-status" />
                <div class="tips-content">
                    <div class="tips-row-title">{{ tip.title }}</div>
                    <div class="tips-row-sub">{{ seenLabel(tip.key) }}</div>
                </div>
            </li>
        </ul>
    </div>
</template>

<style scoped>
.tips-settings {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.tips-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    cursor: pointer;
    user-select: none;
}

.tips-hint {
    margin: 0;
    font-size: 0.85em;
    color: var(--wa-color-neutral-on-quiet, #888);
}

.tips-list-title {
    margin: 0.5rem 0 0;
    font-size: 0.95em;
    font-weight: 600;
}

.tips-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    list-style: none;
    padding: 0;
    margin: 0;
}

.tips-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.5rem;
    border-radius: 0.25rem;
    cursor: pointer;
    background-color: var(--glass-item-rest);
}

.tips-row:hover,
.tips-row:focus-visible {
    background-color: var(--glass-item-hover);
    outline: none;
}

.tips-row.seen .tips-row-title {
    color: var(--wa-color-neutral-on-quiet, #888);
}

.tips-content {
    flex: 1;
    min-width: 0;
}

.tips-row-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tips-row-sub {
    font-size: 0.8em;
    color: var(--wa-color-neutral-on-quiet, #888);
}

.tips-empty {
    margin: 0.5rem 0;
    font-style: italic;
    color: var(--wa-color-neutral-on-quiet, #888);
}
</style>
