<script setup lang="ts">
import { useGitContext } from './composables/useGitContext'
import { useTheme } from './composables/useTheme'

const { classes, showHeaders } = useGitContext()
const { textColour } = useTheme()
</script>

<template>
  <div
    id="vue-git-log"
    :style="classes?.containerStyles"
    :class="['container', classes?.containerClass]"
  >
    <div v-if="$slots.tags" class="tags">
      <h4
        v-if="showHeaders"
        :style="{ color: textColour, marginLeft: '10px' }"
        class="title"
      >
        Branch / Tag
      </h4>

      <slot name="tags" />
    </div>

    <div v-if="$slots.graph" class="graph">
      <h4
        v-if="showHeaders"
        :style="{ color: textColour }"
        class="title"
      >
        Graph
      </h4>

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

  .tags {
    flex-grow: 1;
    max-width: 175px;
  }

  .table {
    flex-grow: 1;
  }
}

.title {
  margin: 12px 0 25px 0;
  line-height: 22px;
  font-weight: 600;
}
</style>
