<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
    windows: {
        type: Array,
        default: () => [],
    },
    presets: {
        type: Array,
        default: () => [],
    },
})

const emit = defineEmits(['select', 'create'])

const newName = ref('')

/** Set of running window names for quick lookup. */
const runningNames = computed(() => new Set(props.windows.map(w => w.name)))

/** Running windows that are NOT presets (ad-hoc shells). */
const adHocWindows = computed(() => {
    const presetNames = new Set(props.presets.map(p => p.name))
    return props.windows.filter(w => !presetNames.has(w.name))
})

function handleSelect(name) {
    emit('select', name)
}

function handleCreate() {
    const name = newName.value.trim()
    if (!name) return
    emit('create', name)
    newName.value = ''
}

function handlePresetClick(preset) {
    if (runningNames.value.has(preset.name)) {
        emit('select', preset.name)
    } else {
        emit('create', preset)
    }
}
</script>

<template>
    <div class="tmux-navigator">
        <div class="navigator-content">
            <h3 class="navigator-title">Shells</h3>

            <!-- Ad-hoc shells (not from presets) -->
            <div v-if="adHocWindows.length" class="shell-list">
                <wa-button
                    v-for="win in adHocWindows"
                    :key="win.name"
                    class="shell-button"
                    :variant="win.active ? 'brand' : 'neutral'"
                    :appearance="win.active ? 'outlined' : 'plain'"
                    size="medium"
                    @click="handleSelect(win.name)"
                >
                    <span v-if="win.active" class="active-indicator">●</span>
                    <span v-else class="active-indicator-placeholder"></span>
                    {{ win.name }}
                </wa-button>
            </div>

            <!-- Preset shells (always visible) -->
            <template v-if="presets.length">
                <h4 class="presets-title">Presets</h4>
                <div class="shell-list">
                    <wa-button
                        v-for="preset in presets"
                        :key="preset.name"
                        class="shell-button preset-button"
                        :class="{ 'preset-running': runningNames.has(preset.name) }"
                        variant="neutral"
                        appearance="plain"
                        size="medium"
                        @click="handlePresetClick(preset)"
                    >
                        <span class="preset-row">
                            <wa-icon
                                name="circle-play"
                                class="preset-icon"
                                :class="{ 'preset-icon--running': runningNames.has(preset.name) }"
                            ></wa-icon>
                            <span class="preset-label">
                                <span>{{ preset.name }}</span>
                                <span v-if="preset.command" class="preset-command">{{ preset.command }}</span>
                            </span>
                        </span>
                    </wa-button>
                </div>
            </template>

            <form class="create-form" @submit.prevent="handleCreate">
                <wa-input
                    :value="newName"
                    placeholder="Name"
                    size="small"
                    class="create-input"
                    @input="newName = $event.target.value"
                ></wa-input>
                <wa-button
                    type="submit"
                    variant="brand"
                    appearance="outlined"
                    size="small"
                    :disabled="!newName.trim()"
                >
                    Create
                </wa-button>
            </form>
        </div>
    </div>
</template>

<style scoped>
.tmux-navigator {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--wa-color-surface-default);
}

.navigator-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    width: min(400px, calc(100% - 2rem));
}

.navigator-title {
    margin: 0;
    font-size: var(--wa-font-size-l);
    font-weight: 600;
    color: var(--wa-color-text-default);
}

.presets-title {
    margin: 0;
    font-size: var(--wa-font-size-s);
    font-weight: 500;
    color: var(--wa-color-text-subtle);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.shell-list {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.shell-button {
    width: 100%;
}

.shell-button::part(base) {
    justify-content: flex-start;
    width: 100%;
}

.active-indicator {
    color: var(--wa-color-brand-600);
    margin-right: var(--wa-space-xs);
    font-size: 0.8em;
}

.active-indicator-placeholder {
    display: inline-block;
    width: 0.8em;
    margin-right: var(--wa-space-xs);
}

.preset-button:not(.preset-running) {
    opacity: 0.7;
}

.preset-button:not(.preset-running):hover {
    opacity: 1;
}

.preset-row {
    display: flex;
    flex-direction: row;
    align-items: flex-start;
    width: 100%;
}

.preset-icon {
    margin-right: var(--wa-space-xs);
    margin-top: 0.15em;
    font-size: 0.9em;
    flex-shrink: 0;
}

.preset-icon--running {
    color: #4ade80;
}

.preset-label {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    line-height: 1.3;
}

.preset-command {
    font-size: 0.8em;
    color: var(--wa-color-text-subtle);
    font-family: var(--wa-font-family-mono, monospace);
}

.create-form {
    display: flex;
    gap: var(--wa-space-xs);
    align-items: center;
    margin-top: var(--wa-space-s);
}

.create-input {
    flex: 1;
}
</style>
