<script setup lang="ts">
import { usePlaceholderData } from '../../composables/usePlaceholderData'
import GraphRow from './GraphRow.vue'

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

const { placeholderData } = usePlaceholderData()
</script>

<template>
  <GraphRow
    v-for="({ commit, columns }, rowIndex) in placeholderData"
    :key="`skeleton-row_${commit.hash}`"
    :commit="commit"
    :columns="columns"
  />
</template>
