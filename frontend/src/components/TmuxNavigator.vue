<script setup>
import { ref } from 'vue'

defineProps({
    windows: {
        type: Array,
        default: () => [],
    },
})

const emit = defineEmits(['select', 'create'])

const newName = ref('')

function handleSelect(name) {
    emit('select', name)
}

function handleCreate() {
    const name = newName.value.trim()
    if (!name) return
    emit('create', name)
    newName.value = ''
}
</script>

<template>
    <div class="tmux-navigator">
        <div class="navigator-content">
            <h3 class="navigator-title">Shells</h3>

            <div class="shell-list">
                <wa-button
                    v-for="win in windows"
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
