<script setup>
/**
 * ArchiveDescendantsDialog - Choose the descendant sessions to archive with a
 * session.
 *
 * Props-driven like StopProcessConfirmDialog: the parent controls visibility
 * via the `open` prop and receives `confirm` / `cancel` events.
 *
 * Spec: docs/plans/2026-11-09-archive-with-descendants-spec.md
 */
import { computed, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDataStore } from '../../stores/data'
import {
    allChecked,
    archiveDialogButtons,
    checkRow,
    initialChecked,
    isWorkInProgress,
    selectAll,
    uncheckRow,
    warningTotals,
} from '../../utils/archiveTree'
import {
    activeCronCount,
    backgroundShellCount,
    batchStopConfirmationMessage,
    userTurnBackgroundShellCount,
} from '../../utils/backgroundWork'
import { TREE_MAX_INDENT_LEVEL, treeGuides } from '../../utils/sessionTree'
import { getSessionDisplayName } from '../../utils/sessionTitle'
import AppTooltip from '../ui/AppTooltip.vue'
import ProcessIndicator from '../ui/ProcessIndicator.vue'
import ScrollableText from '../ui/ScrollableText.vue'

const props = defineProps({
    open: { type: Boolean, default: false },
    status: { type: String, default: 'loading' },   // 'loading' | 'ready' | 'error'
    targetSession: { type: Object, default: null },
    rows: { type: Array, default: () => [] },
    // Session id -> active cron count from the opening read. Empty after a read failure.
    cronCounts: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['confirm', 'cancel'])

const { t } = useI18n()
const store = useDataStore()

const dialogRef = ref(null)

// The checkboxes: set when the dialog receives its rows, then changed only by the user.
const checked = shallowRef(new Set())
watch(() => [props.open, props.rows], ([isOpen, rows], previous) => {
    const [wasOpen, previousRows] = previous ?? []
    if (isOpen && (!wasOpen || rows !== previousRows)) checked.value = initialChecked(rows)
}, { immediate: true })

// Live work of a session. Crons of a session with no live process come from the opening read.
function workOf(id, cronCounts) {
    const processState = store.getProcessState(id)
    if (!processState) return { state: null, working: false, shells: 0, userTurnShells: 0, crons: cronCounts[id] ?? 0 }
    return {
        state: processState.state,
        working: isWorkInProgress({
            state: processState.state,
            backgroundWork: processState.background_work_in_progress,
        }),
        shells: backgroundShellCount(processState),
        userTurnShells: userTurnBackgroundShellCount(processState),
        crons: activeCronCount(processState),
    }
}

// Everything the dialog displays, computed from the props, the checkboxes and the store.
const liveView = computed(() => {
    const { status, targetSession, cronCounts } = props
    const rows = status === 'ready' ? props.rows : []

    const rowViews = rows.map((row) => ({
        id: row.id,
        session: row.session,
        title: getSessionDisplayName(row.session),
        titleId: `archive-descendants-title-${row.id}`,
        guides: treeGuides(row),
        indentLevel: Math.min(row.depth, TREE_MAX_INDENT_LEVEL),
        work: workOf(row.id, cronCounts),
    }))

    const targetWork = targetSession ? workOf(targetSession.id, cronCounts) : null
    const checkedWork = rowViews.filter((row) => checked.value.has(row.id)).map((row) => row.work)
    // No warning while the tree is loading: the only button is Cancel.
    const totals = warningTotals(status === 'loading' || !targetWork ? [] : [targetWork, ...checkedWork])
    const warningVisible = totals.processCount + totals.shellCount + totals.cronCount > 0
    const targetConcerned = !!targetWork && (targetWork.working || targetWork.shells > 0 || targetWork.crons > 0)

    return {
        status,
        targetSession,
        rows,
        rowViews,
        allRowsChecked: allChecked(rows, checked.value),
        totals,
        warningMessage: warningVisible ? batchStopConfirmationMessage({ mode: 'archive', ...totals }) : '',
        buttons: archiveDialogButtons({ checkedCount: checkedWork.length, warningVisible, targetConcerned }),
    }
})

// Values shown, frozen while the dialog closes: the parent resets its props as
// soon as it closes, and the archive stops the processes at the same moment.
// Either would change the content during the hide animation.
const shown = shallowRef(liveView.value)
watch(() => [props.open, liveView.value], ([isOpen, view]) => {
    if (isOpen) shown.value = view
})

const dialogTitle = computed(() => t('session.archiveDescendants.title', {
    title: shown.value.targetSession ? getSessionDisplayName(shown.value.targetSession) : '',
}))

function buttonLabel({ key, count }) {
    return count === undefined ? t(key) : t(key, { count }, count)
}

function onRowChange(row, event) {
    const { rows } = shown.value
    checked.value = event.target.checked
        ? checkRow(rows, checked.value, row.id)
        : uncheckRow(rows, checked.value, row.id)
}

function toggleAll() {
    checked.value = shown.value.allRowsChecked ? new Set() : selectAll(shown.value.rows)
}

// Set synchronously by the button handlers so the subsequent wa-hide event
// (fired when the dialog closes in reaction to props.open going false) does
// NOT re-emit `cancel` on top of the button's emit.
let closedByButton = false

// Sync the `open` prop to the underlying wa-dialog's `open` attribute.
watch(() => props.open, (isOpen) => {
    if (!dialogRef.value) return
    dialogRef.value.open = isOpen
}, { immediate: true })

// wa-hide fires when the dialog is requested to close: either because
// props.open flipped to false (our watcher set it) or because the user
// dismissed via Escape / the X button / backdrop.
// We only want to emit `cancel` in the user-dismissal case. Tooltips of the
// rows fire a bubbling wa-hide too: ignore them.
function onWaHide(event) {
    if (event.target !== event.currentTarget) return
    if (closedByButton) {
        closedByButton = false
        return
    }
    if (props.open) emit('cancel')
}

// The buttons stay clickable during the hide animation. A late click must not
// emit again, nor leave `closedByButton` set after wa-hide has already fired.
function handlePrimary() {
    if (!props.open) return
    closedByButton = true
    const { rows } = shown.value
    const checkedIds = rows.filter((row) => checked.value.has(row.id)).map((row) => row.id)
    emit('confirm', { checkedIds, targetOnly: checkedIds.length === 0 })
}

function handleSecondary() {
    if (!props.open) return
    closedByButton = true
    emit('confirm', { checkedIds: [], targetOnly: true })
}

function handleCancel() {
    if (!props.open) return
    closedByButton = true
    emit('cancel')
}
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="dialogTitle"
        class="archive-descendants-dialog"
        @wa-hide="onWaHide"
    >
        <div class="dialog-content">
            <div v-if="shown.status === 'loading'" class="loading-state">
                <wa-spinner></wa-spinner>
                <span>{{ $t('session.archiveDescendants.loading') }}</span>
            </div>

            <wa-callout v-if="shown.status === 'error'" variant="danger" size="small" open>
                <p>{{ $t('session.archiveDescendants.loadError') }}</p>
            </wa-callout>

            <wa-callout v-if="shown.warningMessage" variant="warning" size="small" open>
                <p>{{ shown.warningMessage }}</p>
                <template v-if="shown.totals.cronCount > 0">
                    <p>{{ $t('session.stopConfirmation.cronWarning') }}</p>
                    <p>{{ $t('session.stopConfirmation.restoreHint') }}</p>
                </template>
            </wa-callout>

            <template v-if="shown.status === 'ready'">
                <div class="tree-header">
                    <span>{{ $t('session.archiveDescendants.intro') }}</span>
                    <wa-button size="small" variant="neutral" appearance="plain" @click="toggleAll">
                        {{ shown.allRowsChecked ? $t('session.archiveDescendants.deselectAll') : $t('session.archiveDescendants.selectAll') }}
                    </wa-button>
                </div>

                <div v-scroll-shadow class="tree-list">
                    <div
                        v-for="row in shown.rowViews"
                        :key="row.id"
                        class="tree-row"
                        :style="{ '--tree-indent-level': row.indentLevel }"
                    >
                        <template v-if="row.guides">
                            <span v-for="column in row.guides.columns" :key="column" class="tree-guide" :style="{ '--tree-column': column }" aria-hidden="true"></span>
                            <span class="tree-guide" :class="{ 'tree-guide--ends': row.guides.ownEnds }" :style="{ '--tree-column': row.guides.ownColumn }" aria-hidden="true"></span>
                            <span class="tree-elbow" :style="{ '--tree-column': row.guides.ownColumn }" aria-hidden="true"></span>
                        </template>
                        <wa-checkbox
                            class="tree-checkbox"
                            size="small"
                            :checked="checked.has(row.id)"
                            @change.stop="onRowChange(row, $event)"
                        >
                            <wa-icon v-if="row.session.pinned" name="thumbtack" class="pinned-icon"></wa-icon>
                            <ScrollableText :id="row.titleId" class="tree-row-title" :text="row.title" />
                            <ProcessIndicator
                                v-if="row.work.state"
                                :state="row.work.state"
                                :has-active-crons="row.work.crons > 0"
                                :background-shells="row.work.userTurnShells"
                                size="small"
                            />
                        </wa-checkbox>
                        <AppTooltip deferred :for="row.titleId">{{ row.title }}</AppTooltip>
                    </div>
                </div>
            </template>
        </div>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="handleCancel">
                {{ buttonLabel(shown.buttons.cancel) }}
            </wa-button>
            <wa-button
                v-if="shown.status !== 'loading' && shown.buttons.secondary"
                variant="neutral"
                appearance="outlined"
                @click="handleSecondary"
            >
                {{ buttonLabel(shown.buttons.secondary) }}
            </wa-button>
            <wa-button
                v-if="shown.status !== 'loading'"
                class="primary-button"
                :variant="shown.buttons.primary.danger ? 'danger' : 'brand'"
                appearance="filled"
                @click="handlePrimary"
            >
                <wa-icon slot="start" :name="shown.buttons.primary.danger ? 'ban' : 'box-archive'"></wa-icon>
                {{ buttonLabel(shown.buttons.primary) }}
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.archive-descendants-dialog {
    --width: min(40rem, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.dialog-content p {
    margin: 0;
}

.dialog-content p + p {
    margin-top: var(--wa-space-s);
}

.loading-state {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.tree-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-xs);
}

.tree-list {
    max-height: min(40vh, 22rem);
    overflow-y: auto;
}

/* The checkbox box is one indentation step wide: a guide column is centred on
   the box of the ancestor, and an elbow ends at the box of the child. */
.tree-row {
    --tree-step: 1.125rem;
    --wa-form-control-toggle-size: var(--tree-step);
    --tree-line: var(--wa-border-width-s) solid var(--wa-color-surface-border);
    --tree-elbow-gap: 2px;
    position: relative;
    display: flex;
    align-items: center;
    /* Padding, not a gap between rows: the guide lines stay continuous. */
    padding-block: var(--wa-space-3xs);
    padding-inline-start: calc(var(--tree-indent-level, 0) * var(--tree-step));
}

.tree-guide,
.tree-elbow {
    position: absolute;
    pointer-events: none;
    inset-inline-start: calc(var(--tree-column, 0) * var(--tree-step) + var(--tree-step) / 2);
}

.tree-guide {
    inset-block: 0;
    border-inline-start: var(--tree-line);
}

.tree-guide--ends {
    inset-block-end: 50%;
}

.tree-elbow {
    inset-block-start: 50%;
    inline-size: calc(var(--tree-step) / 2 - var(--tree-elbow-gap));
    border-block-start: var(--tree-line);
}

.tree-checkbox {
    display: flex;
    flex: 1 1 0;
    min-width: 0;
}

.tree-checkbox::part(base) {
    flex: 1 1 0;
    align-items: center;
    min-width: 0;
}

.tree-checkbox::part(label) {
    display: flex;
    flex: 1 1 0;
    align-items: center;
    gap: var(--wa-space-2xs);
    min-width: 0;
}

.tree-row-title {
    flex: 1 1 0;
    font-size: var(--wa-font-size-s);
}

.pinned-icon {
    flex-shrink: 0;
    font-size: var(--wa-font-size-2xs);
    color: var(--wa-color-yellow-80) !important;
    transform: rotate(30deg);
}

.dialog-footer {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}

.primary-button {
    margin-left: auto;
}
</style>
