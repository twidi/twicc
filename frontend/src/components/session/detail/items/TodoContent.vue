<script setup>
import { ref, watch } from 'vue'
import { getDetail, tickRanks } from '../../../../utils/todoList'

// Provider-agnostic todo/plan renderer. Each ``todos`` entry must carry
// ``status`` plus at least one of ``content`` / ``activeForm`` (see
// ``utils/todoList.js``). Codex's ``update_plan`` carries a single
// ``step`` field per item, mapped to ``content`` upstream.
//
// ``explanation`` is an optional preamble shown above the list. Only
// Codex's ``update_plan`` populates it today (no equivalent in Claude
// Code's TodoWrite); kept optional so other providers can ignore it.
//
// ``animate`` enables the check pop of a task that becomes completed while
// the list is on screen. Only the Tasks pane passes it (while shown); the
// timeline blocks hold a fixed snapshot and never pop.
const props = defineProps({
    todos: {
        type: Array,
        required: true,
    },
    explanation: {
        type: String,
        default: null,
    },
    animate: {
        type: Boolean,
        default: false,
    },
})

// Indices whose check is popping, with their ranks (the position among the tasks
// completed in the same snapshot, which staggers the ticks). Grown by union, never
// replaced: the store hands a new array on every session_updated broadcast, even with
// no change, and a replacement would cut a running pop. Not immediate: a first render
// never pops.
const popping = ref(new Map())

watch(() => props.todos, (newValue, oldValue) => {
    if (props.animate) {
        for (const [index, rank] of tickRanks(oldValue, newValue)) popping.value.set(index, rank)
    }
    // An item that leaves `completed` during its pop loses its icon (v-if), and
    // neither animationend nor animationcancel is guaranteed: drop it here.
    for (const index of [...popping.value.keys()]) {
        if (newValue?.[index]?.status !== 'completed') popping.value.delete(index)
    }
})

watch(() => props.animate, (animate) => {
    if (!animate) popping.value.clear()
})

function onPopEnd(index) {
    popping.value.delete(index)
}
</script>

<template>
    <p v-if="explanation" class="todo-explanation">{{ explanation }}</p>
    <ol class="todo-list">
        <li
            v-for="(todo, i) in todos"
            :key="i"
            class="todo-item"
            :class="[`todo-item-${todo.status}`, { 'todo-item-ticking': popping.has(i) }]"
            :style="{ '--tick-rank': popping.get(i) ?? null }"
        >
            <wa-icon
                v-if="todo.status === 'completed'"
                name="check"
                class="todo-item-icon todo-item-icon-completed"
                :class="{ 'todo-item-icon--pop': popping.has(i) }"
                @animationend="onPopEnd(i)"
                @animationcancel="onPopEnd(i)"
            ></wa-icon>
            <wa-icon
                v-else-if="todo.status === 'in_progress'"
                name="arrow-right"
                class="todo-item-icon todo-item-icon-in-progress"
            ></wa-icon>
            <wa-icon
                v-else-if="todo.status === 'deleted'"
                name="xmark"
                class="todo-item-icon todo-item-icon-deleted"
            ></wa-icon>
            <wa-icon
                v-else
                name="circle"
                class="todo-item-icon todo-item-icon-pending"
                variant="regular"
            ></wa-icon>
            <span class="todo-item-text"><span class="todo-item-strike">{{ getDetail(todo) }}</span></span>
        </li>
    </ol>
</template>

<style scoped>
.todo-explanation {
    margin: 0 0 var(--wa-space-xs) 0;
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.todo-list {
    list-style: none;
    margin: 0;
    /* Side spacing: an open details' when the list is one of its children (moved from the
       details' content part, motion.css); none elsewhere (the Tasks tab). */
    padding: var(--wa-space-xs) var(--spacing, 0);
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.todo-item {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-xs);
}

.todo-item-icon {
    flex-shrink: 0;
    font-size: 0.85em;
}

.todo-item-icon-completed {
    color: var(--wa-color-success-60);
}

/* Check pop of a task that just became completed. 420ms: the mock's value. Under reduced
   motion the amount is 0, so the keyframe moves nothing but still ends (animationend
   cleans the popping set). The delay chains the ticks of one snapshot; `both` holds the
   icon at its `from` frame until its turn. */
.todo-item-icon--pop {
    animation: todo-check-pop 420ms var(--motion-ease-spring) both;
    animation-delay: calc(var(--tick-rank, 0) * 150ms);
}

@keyframes todo-check-pop {
    from {
        scale: calc(1 - var(--motion-amount));
        rotate: calc(-30deg * var(--motion-amount));
    }
}

.todo-item-icon-in-progress {
    color: var(--wa-color-brand-text);
}

.todo-item-icon-pending {
    color: var(--wa-color-text-quiet);
}

.todo-item-icon-deleted {
    color: var(--wa-color-danger-50);
}

.todo-item-completed .todo-item-text {
    color: var(--wa-color-text-quiet);
}

.todo-item-deleted .todo-item-text {
    color: var(--wa-color-text-quiet);
    text-decoration: line-through;
}

.todo-item-in-progress .todo-item-text {
    font-weight: var(--wa-font-weight-semibold);
}
</style>
