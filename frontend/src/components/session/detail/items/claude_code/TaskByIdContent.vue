<script setup>
// Detail-panel renderer for TaskCreate / TaskUpdate / TaskGet tool_uses.
// The ``input`` prop is rendered as a JsonHumanView at the top. Per the
// helper that consumes this component:
//   - For TaskCreate, ``input`` is the helper-prepared tool input
//     (subject, description, activeForm, ...).
//   - For TaskUpdate / TaskGet, ``input`` is the full ``twiccTaskData``
//     captured at the moment of the call — more useful to the user than
//     the bare tool input (which only carries taskId + the fields being
//     updated, or just taskId for Get).
// When a non-empty ``todos`` array is also provided, a ``<wa-divider>``,
// a "Task list:" label, and the same TodoContent the TaskList tool
// renders follow. The snapshot is frozen at enrichment time, so older
// tool_uses keep their original status here regardless of any later
// TaskUpdate.
import JsonHumanView from '../../../../json/JsonHumanView.vue'
import TodoContent from '../TodoContent.vue'

defineProps({
    input: { type: Object, required: true },
    inputOverrides: { type: Object, default: () => ({}) },
    todos: { type: Array, default: null },
})
</script>

<template>
    <div class="tool-input">
        <JsonHumanView :value="input" :overrides="inputOverrides" />
    </div>
    <template v-if="todos && todos.length">
        <wa-divider></wa-divider>
        <div class="task-list-title">{{ $t('session.tools.taskList') }}</div>
        <TodoContent :todos="todos" />
    </template>
</template>

<style scoped>
.task-list-title {
    margin-top: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    font-weight: 600;
    color: var(--wa-color-text-quiet);
}
</style>
