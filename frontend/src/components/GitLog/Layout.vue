<script setup lang="ts">
import { computed } from 'vue'
import { useGitContext } from './composables/useGitContext'
import { useTheme } from './composables/useTheme'
import { pxToRem } from './utils/units'

const { classes, showHeaders, headerRowHeight } = useGitContext()
const { textColour } = useTheme()

const titleHeight = computed(() => pxToRem(headerRowHeight.value))
</script>

<template>
  <div
    id="vue-git-log"
    :style="classes?.containerStyles"
    :class="['container', classes?.containerClass]"
  >
    <div v-if="$slots.tags" class="tags">
      <div
        v-if="showHeaders"
        :style="{ color: textColour, height: titleHeight }"
        class="title"
      >
        Branch / Tag
      </div>

      <slot name="tags" />
    </div>

    <div v-if="$slots.graph" class="graph">
      <div
        v-if="showHeaders"
        :style="{ color: textColour, height: titleHeight }"
        class="title"
      >
        Graph
      </div>

      <slot name="graph" />
    </div>

    <div v-if="$slots.table" class="table">
      <slot name="table" />
    </div>
  </div>
</template>

<style scoped lang="scss">
.container {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;

  .tags, .graph {
      display: flex;
      flex-direction: column;
  }

  .tags {
    flex-grow: 1;
    max-width: 11rem;
      .title {
          margin-left: 10px;
      }
  }

  .table {
    flex-grow: 1;
  }
}

.title {
  margin: 0;
  font-weight: 600;
  display: flex;
  flex-shrink: 0;
  align-items: center;
}
</style>
