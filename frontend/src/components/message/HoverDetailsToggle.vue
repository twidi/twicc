<script setup>
// Inline "(Show details on hover)" / "(Hide details)" toggle next to the matrix
// heading's help link (see MatrixHeadingLinks, which hides it on touch devices).
// The state lives in the benchmark task store and resets with the other
// transient controls whenever a surface showing the matrix opens or closes.
import { useBenchmarkTaskStore } from '../../stores/benchmarkTask'

const taskStore = useBenchmarkTaskStore()
</script>

<template>
    <span class="hover-details-toggle">(<a
        href="#"
        @click.prevent="taskStore.hoverDetails = !taskStore.hoverDetails"
    >{{ taskStore.hoverDetails ? 'Hide details' : 'Show details on hover' }}</a>)</span>
</template>

<style scoped>
.hover-details-toggle {
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-normal);
    white-space: nowrap;
}

a {
    color: var(--wa-color-brand-60);
    cursor: pointer;
    text-decoration: none;
}

a:hover,
a:focus-visible {
    text-decoration: underline;
    outline: none;
}
</style>
