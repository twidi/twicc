<script setup>
// One level of the annotation tree shown in the popover (utils/orchestrationView.js buildAnnotationTree).
// Recursive through its own file name. A node with values shows ``name: value`` per value; a node
// without values is a level (chevron + name). Children nest below, with a thin connector line.
defineProps({
    nodes: { type: Array, required: true }, // [{ name, values: string[], children: [...] }]
})
</script>

<template>
    <ul class="atl">
        <li v-for="node in nodes" :key="node.name">
            <div v-if="!node.values.length" class="atl-level">
                <wa-icon auto-width name="chevron-down" class="atl-chevron"></wa-icon>
                {{ node.name }}
            </div>
            <div v-for="(value, index) in node.values" :key="index" class="atl-leaf">
                <span class="atl-key">{{ node.name }}:</span> <span class="atl-value">{{ value }}</span>
            </div>
            <AnnotationTreeLevel v-if="node.children.length" :nodes="node.children" />
        </li>
    </ul>
</template>

<style scoped>
.atl {
    list-style: none;
    margin: 0;
    padding: 0;
}

/* Nested levels: a thin guide line, like the node tree's connectors. */
.atl .atl {
    margin-left: 0.5rem;
    padding-left: 0.8rem;
    border-left: 1px solid var(--wa-color-neutral-border-normal);
}

.atl li {
    padding-block: var(--wa-space-3xs);
}

.atl-level {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-weight: 500;
    color: var(--wa-color-text-quiet);
}

.atl-chevron {
    font-size: 0.8em;
}

.atl-key {
    color: var(--wa-color-text-quiet);
}

.atl-value {
    font-weight: 500;
    overflow-wrap: anywhere;
}
</style>
