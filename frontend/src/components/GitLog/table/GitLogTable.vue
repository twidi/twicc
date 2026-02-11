<script setup lang="ts">
import { computed, provide } from 'vue'
import { TABLE_CONTEXT_KEY, type TableContextBag } from '../composables/keys'
import { useGitContext } from '../composables/useGitContext'
import { useTheme } from '../composables/useTheme'
import { usePlaceholderData } from '../composables/usePlaceholderData'
import type { Commit, CustomTableRowProps, GitLogTableStylingProps } from '../types'
import TableContainer from './components/TableContainer.vue'
import TableRow from './components/TableRow.vue'


// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

const props = withDefaults(defineProps<{
  timestampFormat?: string
  className?: string
  styles?: GitLogTableStylingProps
}>(), {
  timestampFormat: 'YYYY-MM-DD HH:mm:ss',
})

// ---------------------------------------------------------------------------
// Slots
// ---------------------------------------------------------------------------

defineSlots<{
  row?(props: CustomTableRowProps): unknown
}>()

// ---------------------------------------------------------------------------
// Provide table context
// ---------------------------------------------------------------------------

const tableContextValue: TableContextBag = {
  timestampFormat: computed(() => props.timestampFormat),
}

provide(TABLE_CONTEXT_KEY, tableContextValue)

// ---------------------------------------------------------------------------
// Git context
// ---------------------------------------------------------------------------

const { showHeaders, graphData, paging, indexCommit, isIndexVisible } = useGitContext()
const { textColour } = useTheme()
const { placeholderData } = usePlaceholderData()

// ---------------------------------------------------------------------------
// Table data (visible commits for the table)
// ---------------------------------------------------------------------------

const tableData = computed<Commit[]>(() => {
  const data = graphData.value.commits.slice(
    paging.value?.startIndex,
    paging.value?.endIndex,
  )

  if (isIndexVisible.value && indexCommit.value) {
    data.unshift(indexCommit.value)
  }

  return data
})
</script>

<template>
  <TableContainer
    :row-quantity="tableData.length"
    :class-name="className"
    :style-overrides="props.styles?.table"
    :has-custom-row="!!$slots.row"
  >
    <!-- Table headers -->
    <div
      v-if="showHeaders"
      class="head"
      id="vue-git-log-table-head"
      data-testid="vue-git-log-table-head"
      :style="props.styles?.thead"
    >
      <div
        class="header"
        id="vue-git-log-table-header-commit-message"
        data-testid="vue-git-log-table-header-commit-message"
      >
        Commit message
      </div>

      <div
        class="header"
        id="vue-git-log-table-header-author"
        data-testid="vue-git-log-table-header-author"
      >
        Author
      </div>

      <div
        class="header"
        id="vue-git-log-table-header-timestamp"
        data-testid="vue-git-log-table-header-timestamp"
      >
        Timestamp
      </div>
    </div>

    <!-- Placeholder rows (skeleton) when no data -->
    <template v-if="tableData.length === 0">
      <TableRow
        v-for="({ commit }, i) in placeholderData"
        :key="`git-log-empty-table-row-${commit.hash}`"
        :index="i"
        :commit="commit"
        :is-placeholder="true"
        :row-style-overrides="props.styles?.tr"
        :data-style-overrides="props.styles?.td"
      />
    </template>

    <!-- Data rows -->
    <TableRow
      v-for="(commit, i) in tableData"
      :key="`git-log-table-row-${commit.hash}`"
      :index="i"
      :commit="commit"
      :row-style-overrides="props.styles?.tr"
      :data-style-overrides="props.styles?.td"
    >
      <template v-if="$slots.row" #row="rowProps">
        <slot name="row" v-bind="rowProps" />
      </template>
    </TableRow>
  </TableContainer>
</template>

<style scoped>
.head {
  display: contents;
}

.header:first-child {
  padding-left: 20px;
}

.header {
  font-weight: 600;
  color: var(--git-text-color);
  display: flex;
  align-items: center;
}
</style>
