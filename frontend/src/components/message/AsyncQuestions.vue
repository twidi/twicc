<script setup>
import { computed } from 'vue'
import { useDataStore } from '../../stores/data'
import { toast } from '../../composables/useToast'
import QuestionFields from './QuestionFields.vue'

const props = defineProps({
    sessionId: { type: String, required: true },
    snapshot: { type: Object, required: true },
})
const emit = defineEmits(['dismiss'])
const store = useDataStore()
const draft = computed(() => store.getAsyncQuestionDraft(props.sessionId))
const pendingIds = computed(() => store.getPendingAsyncQuestionIds(props.sessionId))
const batches = computed(() => props.snapshot.widget_enabled === false ? []
    : props.snapshot.batches.filter(batch => batch.status === 'ready'))

function fields(batch) {
    return batch.questions.map(question => ({
        ...question,
        options: question.options.map(label => ({ label })),
    }))
}

function updateChoices(batch, choices) {
    if (pendingIds.value.includes(batch.item_id)) return
    // Preserve the entire record, including source content and request identities.
    const record = {
        ...draft.value,
        choices: { ...draft.value?.choices, [batch.item_id]: choices },
        sourceBatches: { ...draft.value?.sourceBatches, [batch.item_id]: batch },
        recoveredIds: draft.value?.recoveredIds || [],
    }
    store.setAsyncQuestionDraft(props.sessionId, record).catch(error => {
        console.warn('Failed to save question answers:', error?.name || 'Error')
        toast.error('Failed to save question answers. Your answers remain in this browser.')
    })
}
</script>

<template>
    <div class="async-questions">
        <div v-for="batch in batches" :key="batch.item_id" class="async-question-batch">
            <QuestionFields
                :questions="fields(batch)"
                :model-value="draft?.choices?.[batch.item_id] || {}"
                :disabled="!!draft?.pendingDismissals?.[batch.item_id] || pendingIds.includes(batch.item_id)"
                :auto-focus="false"
                :allow-clear="true"
                :allow-other="true"
                @update:model-value="updateChoices(batch, $event)"
            />
            <wa-button
                variant="neutral"
                appearance="outlined"
                size="small"
                class="dismiss-batch"
                :disabled="!!draft?.pendingDismissals?.[batch.item_id] || pendingIds.includes(batch.item_id)"
                @click="emit('dismiss', batch.item_id)"
            >
                <wa-icon slot="start" name="ban" variant="classic"></wa-icon>
                Dismiss
            </wa-button>
        </div>
    </div>
</template>

<style scoped>
.async-questions {
    max-height: 25dvh;
    overflow-y: auto;
    overscroll-behavior: contain;
    min-height: 0;
}
.async-question-batch {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding-bottom: var(--wa-space-s);
}
.async-question-batch + .async-question-batch {
    border-top: var(--divider-size) solid var(--wa-color-surface-border);
}
.async-question-batch :deep(.questions-container) {
    flex: none;
    overflow-y: visible;
}
.dismiss-batch {
    align-self: flex-end;
}
</style>
