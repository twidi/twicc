<script setup>
import { computed, ref, useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import MarkdownContent from '../ui/MarkdownContent.vue'
import { vPopoverFocusFix } from '../../directives/vPopoverFocusFix'
import { toast } from '../../composables/useToast'
import GitChangeStats from './GitChangeStats.vue'

const props = defineProps({
    /** The currently selected commit object (or null/undefined). */
    selectedCommit: {
        type: Object,
        default: null,
    },
    /** File change counts: { modified, added, deleted, conflicted }. */
    stats: {
        type: Object,
        default: null,
    },
    /** Whether stats are currently being loaded. */
    statsLoading: {
        type: Boolean,
        default: false,
    },
    /** Whether the git log overlay is currently open. */
    gitLogOpen: {
        type: Boolean,
        default: false,
    },
    /** Currently selected branch name (shown as a tag). */
    selectedBranch: {
        type: String,
        default: '',
    },
    /** Async function(commitHash) → commit detail object, provided by GitPanel. */
    fetchCommitDetail: {
        type: Function,
        default: null,
    },
})

const emit = defineEmits(['toggle-git-log'])

// ---------------------------------------------------------------------------
// Computed
// ---------------------------------------------------------------------------

/** Display text for the commit selector. */
const commitLabel = computed(() => {
    if (!props.selectedCommit) {
        return 'Uncommitted changes'
    }
    // For the index pseudo-commit
    if (props.selectedCommit.hash === 'index') {
        return 'Uncommitted changes'
    }
    return props.selectedCommit.message || props.selectedCommit.hash
})

/** Short hash for display (first 7 chars). */
const commitShortHash = computed(() => {
    if (!props.selectedCommit || props.selectedCommit.hash === 'index') {
        return null
    }
    return props.selectedCommit.hash?.substring(0, 7)
})

// ---------------------------------------------------------------------------
// Commit detail popover
// ---------------------------------------------------------------------------

const commitDetail = ref(null)
const commitDetailLoading = ref(false)
/** Hash of the commit whose detail is cached, to invalidate on commit change. */
let cachedDetailHash = null

async function onPopoverShow() {
    const hash = props.selectedCommit?.hash
    if (!hash || !props.fetchCommitDetail) return

    // Use cached data if same commit.
    if (cachedDetailHash === hash && commitDetail.value) return

    commitDetailLoading.value = true
    commitDetail.value = null
    try {
        commitDetail.value = await props.fetchCommitDetail(hash)
        cachedDetailHash = hash
    } finally {
        commitDetailLoading.value = false
    }
}

/** Format an ISO-like git date string for display. */
function formatGitDate(isoStr) {
    if (!isoStr) return null
    const d = new Date(isoStr)
    if (isNaN(d)) return isoStr
    return d.toLocaleString(navigator.language, {
        day: '2-digit', month: '2-digit', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
    })
}

/** Person display: "Name <email>" or just name or email. */
function formatPerson(person) {
    if (!person) return null
    if (person.name && person.email) return `${person.name} <${person.email}>`
    return person.name || person.email
}

/** True when committer differs from author (different person shown separately). */
function hasDistinctCommitter(detail) {
    if (!detail?.committer || !detail?.author) return false
    return detail.committer.name !== detail.author.name || detail.committer.email !== detail.author.email
}

// ---------------------------------------------------------------------------
// IDs & handlers
// ---------------------------------------------------------------------------

const commitLabelId = useId()
const branchTagId = useId()
const commitHashId = useId()
const popoverHashId = useId()

function toggleGitLog() {
    emit('toggle-git-log')
}

function copyBranch(event) {
    event.stopPropagation()
    navigator.clipboard.writeText(props.selectedBranch)
    toast.success('Branch copied to clipboard', { duration: 2000 })
}

function stopProp(event) {
    event.stopPropagation()
}

function copyHash() {
    const hash = commitDetail.value?.hash || props.selectedCommit?.hash
    if (!hash) return
    navigator.clipboard.writeText(hash)
    toast.success('Commit hash copied to clipboard', { duration: 2000 })
}
</script>

<template>
    <div class="git-panel-header">
        <button
            class="commit-selector"
            :class="{ open: gitLogOpen }"
            @click="toggleGitLog"
        >
            <span class="commit-message" :id="commitLabelId">{{ commitLabel }}</span>
            <AppTooltip :for="commitLabelId">{{ commitLabel }}</AppTooltip>

            <div v-if="statsLoading" class="status-badges">
                <wa-spinner style="--spinner-size: 0.75rem;"></wa-spinner>
            </div>

            <GitChangeStats v-else :stats="stats" />

            <wa-tag v-if="selectedBranch" :id="branchTagId" variant="neutral" class="branch-tag clickable-tag" @click="copyBranch">
                {{ selectedBranch }}
            </wa-tag>
            <AppTooltip :for="branchTagId">Click to copy branch name</AppTooltip>

            <wa-tag v-if="commitShortHash" :id="commitHashId" variant="neutral" class="commit-hash clickable-tag" @click="stopProp">
                {{ commitShortHash }}
                <wa-icon name="chevron-down" class="hash-chevron"></wa-icon>
            </wa-tag>
            <AppTooltip :for="commitHashId">Click to view commit details</AppTooltip>

            <wa-icon
                class="chevron"
                :name="gitLogOpen ? 'chevron-up' : 'chevron-down'"
            ></wa-icon>
        </button>

        <!-- Commit details popover (anchored to hash tag) -->
        <wa-popover
            v-if="commitShortHash"
            v-popover-focus-fix
            :for="commitHashId"
            placement="bottom"
            class="commit-popover"
            @wa-show="onPopoverShow"
        >
            <div v-scroll-shadow class="commit-details">
                <template v-if="commitDetailLoading">
                    <div class="detail-loading">
                        <wa-spinner style="--spinner-size: 1rem;"></wa-spinner>
                    </div>
                </template>
                <template v-else-if="commitDetail">
                    <div class="detail-row">
                        <span class="detail-label">Hash</span>
                        <span :id="popoverHashId" class="detail-value hash-value" @click="copyHash">
                            {{ commitDetail.hash }}
                            <wa-icon name="copy" class="copy-icon"></wa-icon>
                        </span>
                        <AppTooltip :for="popoverHashId">Click to copy</AppTooltip>
                    </div>

                    <div class="detail-row">
                        <span class="detail-label">Title</span>
                        <span class="detail-value">{{ commitDetail.message }}</span>
                    </div>

                    <div v-if="commitDetail.body" class="detail-row">
                        <span class="detail-label">Description</span>
                        <div class="detail-value body-value">
                            <MarkdownContent :source="commitDetail.body" />
                        </div>
                    </div>

                    <div v-if="commitDetail.author" class="detail-row">
                        <span class="detail-label">Author</span>
                        <span class="detail-value">
                            {{ formatPerson(commitDetail.author) }}
                            <span v-if="commitDetail.authorDate" class="detail-date">{{ formatGitDate(commitDetail.authorDate) }}</span>
                        </span>
                    </div>

                    <template v-if="hasDistinctCommitter(commitDetail)">
                        <div class="detail-row">
                            <span class="detail-label">Committer</span>
                            <span class="detail-value">
                                {{ formatPerson(commitDetail.committer) }}
                                <span class="detail-date">{{ formatGitDate(commitDetail.committerDate) }}</span>
                            </span>
                        </div>
                    </template>
                    <template v-else-if="!commitDetail.author">
                        <div class="detail-row">
                            <span class="detail-label">Date</span>
                            <span class="detail-value">{{ formatGitDate(commitDetail.committerDate) }}</span>
                        </div>
                    </template>
                </template>
            </div>
        </wa-popover>
    </div>
</template>

<style scoped>
.git-panel-header {
    flex-shrink: 0;
}

/* ----- Commit selector button ----- */

.commit-selector {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    width: 100%;
    padding: calc(1px + var(--wa-space-3xs)) var(--wa-space-s);
    background: none;
    border: none;
    cursor: pointer;
    font-family: inherit;
    font-size: var(--wa-font-size-s);
    font-weight: normal;
    color: inherit;
    text-align: left;
    transition: background-color 0.15s ease;
    box-shadow: none;
    margin: 0;
    translate: none !important;
    transform: none !important;
    justify-content: start;
    flex-wrap: wrap;
    height: auto;
}

.commit-selector:hover {
    background-color: var(--wa-color-surface-alt);
}

.commit-selector.open {
    background-color: var(--wa-color-surface-alt);
}

/* ----- Branch tag ----- */

.branch-tag {
    font-weight: 600;
    line-height: 1;
    height: auto;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
    margin-left: auto;
}

.commit-hash {
    flex-shrink: 0;
    font-weight: 600;
    line-height: 1;
    height: auto;
    padding: var(--wa-space-2xs) var(--wa-space-xs);
}

.clickable-tag:hover {
    filter: brightness(1.2);
}

.hash-chevron {
    font-size: 0.6em;
    margin-left: -0.15em;
    opacity: 0.7;
}

.commit-message {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}

/* ----- Status badges loading placeholder (the counts render in GitChangeStats) ----- */

.status-badges {
    display: flex;
    align-items: center;
    flex-shrink: 0;
}

/* ----- Chevron ----- */

.chevron {
    flex-shrink: 0;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    transition: transform 0.2s ease;
}

/* ----- Commit details popover ----- */

.commit-popover {
    --max-width: min(50rem, 100vw);
    --arrow-size: 16px;
}

/* The scrolling zone fills the whole popover: its scrollbar and its scroll shadows reach the
   edges. The padding the popover body used to carry is the zone's own, so the content keeps
   its place. */
.commit-popover::part(body) {
    padding: 0;
}

.commit-details {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding: calc(var(--wa-space-l) + var(--wa-space-xs));
    border-radius: var(--wa-panel-border-radius);
    overflow-y: auto;
    font-size: var(--wa-font-size-m);
    user-select: text;
    cursor: text;
    max-height: 50vh;
}

.detail-loading {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s);
}

.detail-row {
    display: flex;
    gap: var(--wa-space-s);
    align-items: baseline;
}
.body-value {
    align-self: flex-start;
}

@media (max-width: 480px) {
    .commit-details {
        padding: calc(2 * var(--wa-space-xs));
    }
    .detail-row {
        flex-direction: column;
        gap: 0;
    }
}

.detail-label {
    flex-shrink: 0;
    width: 5.5rem;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    text-transform: uppercase;
    letter-spacing: 0.03em;
}

.detail-value {
    min-width: 0;
    word-break: break-word;
}

.hash-value {
    font-family: var(--wa-font-mono);
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    border-radius: var(--wa-border-radius-s);
    padding: 0 var(--wa-space-2xs);
    transition: background-color 0.15s ease;
}

.hash-value:hover {
    background-color: var(--wa-color-surface-alt);
}

.copy-icon {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.body-value {
    color: var(--wa-color-text-quiet);
}

.detail-date {
    color: var(--wa-color-text-quiet);
    margin-left: var(--wa-space-2xs);
}
</style>
