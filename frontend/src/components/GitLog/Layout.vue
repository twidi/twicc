<script setup lang="ts">
import { useGitContext } from './composables/useGitContext'
import { useTheme } from './composables/useTheme'
import styles from './Layout.module.scss'

const { classes, showHeaders } = useGitContext()
const { textColour } = useTheme()
</script>

<template>
  <div
    id="vue-git-log"
    :style="classes?.containerStyles"
    :class="[styles.container, classes?.containerClass]"
  >
    <div v-if="$slots.tags" :class="styles.tags">
      <h4
        v-if="showHeaders"
        :style="{ color: textColour, marginLeft: '10px' }"
        :class="styles.title"
      >
        Branch / Tag
      </h4>

      <slot name="tags" />
    </div>

    <div v-if="$slots.graph" :class="styles.graph">
      <h4
        v-if="showHeaders"
        :style="{ color: textColour }"
        :class="styles.title"
      >
        Graph
      </h4>

      <slot name="graph" />
    </div>

    <div v-if="$slots.table" :class="styles.table">
      <slot name="table" />
    </div>
  </div>
</template>
