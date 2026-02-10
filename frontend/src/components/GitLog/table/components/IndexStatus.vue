<script setup lang="ts">
import { useGitContext } from '../../composables/useGitContext'
import pencilIcon from '../../assets/pencil.svg'
import plusIcon from '../../assets/plus.svg'
import minusIcon from '../../assets/minus.svg'


// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const { indexStatus } = useGitContext()
</script>

<template>
  <div
    v-if="indexStatus"
    class="indexStatus"
    data-testid="index-status"
  >
    <div
      v-if="indexStatus.modified > 0"
      class="status"
      data-testid="index-status-modified"
    >
      <span :class="['value', 'modified']">
        {{ indexStatus.modified }}
      </span>

      <div class="iconWrapper">
        <img :src="pencilIcon" class="pencil" alt="modified">
      </div>
    </div>

    <div
      v-if="indexStatus.added > 0"
      class="status"
      data-testid="index-status-added"
    >
      <span :class="['value', 'added']">
        {{ indexStatus.added }}
      </span>

      <div class="iconWrapper">
        <img :src="plusIcon" class="plus" alt="added">
      </div>
    </div>

    <div
      v-if="indexStatus.deleted > 0"
      class="status"
      data-testid="index-status-deleted"
    >
      <span :class="['value', 'deleted']">
        {{ indexStatus.deleted }}
      </span>

      <div class="iconWrapper">
        <img :src="minusIcon" class="minus" alt="deleted">
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.pencil, .plus, .minus {
  height: 15px;
  width: 15px;
  margin-left: 2px;
}

.indexStatus {
  display: flex;
  align-items: center;
  margin-left: 5px;
  opacity: 0.8;
  transition: opacity ease-in-out 0.2s;

  &:hover {
    opacity: 1;
  }

  .status {
    display: flex;
    align-items: center;
  }
}

.value {
  margin-left: 15px;
}

.iconWrapper {
  margin-top: 2px;
}

.modified {
  color: #e5a935;
}

.deleted {
  color: #FF757C;
}

.added {
  color: #5dc044;
}
</style>
