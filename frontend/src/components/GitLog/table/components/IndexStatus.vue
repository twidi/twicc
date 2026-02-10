<script setup lang="ts">
import { useGitContext } from '../../composables/useGitContext'
import pencilIcon from '../../assets/pencil.svg'
import plusIcon from '../../assets/plus.svg'
import minusIcon from '../../assets/minus.svg'
import styles from './IndexStatus.module.scss'

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { indexStatus } = useGitContext()
</script>

<template>
  <div
    v-if="indexStatus"
    :class="styles.indexStatus"
    data-testid="index-status"
  >
    <div
      v-if="indexStatus.modified > 0"
      :class="styles.status"
      data-testid="index-status-modified"
    >
      <span :class="[styles.value, styles.modified]">
        {{ indexStatus.modified }}
      </span>

      <div :class="styles.iconWrapper">
        <img :src="pencilIcon" :class="styles.pencil" alt="modified">
      </div>
    </div>

    <div
      v-if="indexStatus.added > 0"
      :class="styles.status"
      data-testid="index-status-added"
    >
      <span :class="[styles.value, styles.added]">
        {{ indexStatus.added }}
      </span>

      <div :class="styles.iconWrapper">
        <img :src="plusIcon" :class="styles.plus" alt="added">
      </div>
    </div>

    <div
      v-if="indexStatus.deleted > 0"
      :class="styles.status"
      data-testid="index-status-deleted"
    >
      <span :class="[styles.value, styles.deleted]">
        {{ indexStatus.deleted }}
      </span>

      <div :class="styles.iconWrapper">
        <img :src="minusIcon" :class="styles.minus" alt="deleted">
      </div>
    </div>
  </div>
</template>
