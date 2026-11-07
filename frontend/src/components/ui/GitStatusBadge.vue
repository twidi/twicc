<script setup>
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
/**
 * GitStatusBadge — the per-file git status flag used across the Git tab.
 *
 * Renders the porcelain XY status as a two-letter code (staged | unstaged),
 * each letter coloured by change type, with a dim "·" for a clean side — so
 * staged vs unstaged reads from position, like `git status`. Untracked → "??";
 * conflicts → the raw unmerged code in a dedicated colour. Commit files (which
 * carry a single `status`) keep one letter. Optional +added / −removed line
 * counts are shown to the left. A tooltip spells the code out.
 *
 * Renders nothing when the node has no git status. This is the single source of
 * truth for the flag — used by the file tree and the desktop/mobile file-path
 * headers, so they never drift apart.
 */
import { computed, useId } from 'vue'
import AppTooltip from './AppTooltip.vue'

const props = defineProps({
    /** A file tree node carrying git status fields (status or staged/unstaged…). */
    node: { type: Object, default: null },
})

const STATUS_MAP = {
    modified:  { letter: 'M', cls: 'git-badge-modified' },
    added:     { letter: 'A', cls: 'git-badge-added' },
    deleted:   { letter: 'D', cls: 'git-badge-deleted' },
    renamed:   { letter: 'R', cls: 'git-badge-renamed' },
    copied:    { letter: 'C', cls: 'git-badge-added' },
    untracked: { letter: 'U', cls: 'git-badge-untracked' },
}

/** Human label for the unmerged (conflict) two-letter porcelain codes. */
const CONFLICT_LABELS = {
    DD: 'common.git.conflict.DD',
    AU: 'common.git.conflict.AU',
    UD: 'common.git.conflict.UD',
    UA: 'common.git.conflict.UA',
    DU: 'common.git.conflict.DU',
    AA: 'common.git.conflict.AA',
    UU: 'common.git.conflict.UU',
}

/** One slot of the staged|unstaged code: a type-coloured letter, or a dim placeholder. */
function badgeSlot(status) {
    if (!status) return { ch: '·', cls: 'git-badge-slot-empty' }
    const entry = STATUS_MAP[status]
    return entry
        ? { ch: entry.letter, cls: entry.cls }
        : { ch: status[0].toUpperCase(), cls: 'git-badge-modified' }
}

const statusLabel = status => STATUS_MAP[status] ? t(`common.git.status.${status}`) : status

const badgeId = useId()

/** The badge descriptor `{ parts: [{ ch, cls? }], tooltip, conflict? }`, or null. */
const badge = computed(() => {
    const node = props.node
    if (!node || node.type !== 'file') return null

    // Commit files: a single status letter.
    if (node.status) {
        const entry = STATUS_MAP[node.status] || { letter: node.status[0].toUpperCase(), cls: 'git-badge-modified' }
        return { parts: [{ ch: entry.letter, cls: entry.cls }], tooltip: statusLabel(node.status) }
    }

    // Conflicted (unmerged) files: the raw XY code in the conflict colour.
    if (node.conflicted) {
        const code = node.conflict_code || 'UU'
        return {
            conflict: true,
            parts: [{ ch: code[0] }, { ch: code[1] }],
            tooltip: t(CONFLICT_LABELS[code] || 'common.git.conflict.unmerged'),
        }
    }

    // Index files: staged_status (X) / unstaged_status (Y).
    const staged = node.staged_status
    const unstaged = node.unstaged_status
    if (!staged && !unstaged) return null

    if (unstaged === 'untracked' && !staged) {
        return {
            parts: [{ ch: '?', cls: 'git-badge-untracked' }, { ch: '?', cls: 'git-badge-untracked' }],
            tooltip: t('common.git.status.untracked'),
        }
    }

    const tooltip = staged && unstaged
        ? t('common.git.badge.both', { staged: statusLabel(staged), unstaged: statusLabel(unstaged) })
        : staged ? t('common.git.badge.staged', { status: statusLabel(staged) })
        : t('common.git.badge.unstaged', { status: statusLabel(unstaged) })
    return { parts: [badgeSlot(staged), badgeSlot(unstaged)], tooltip }
})

/** +added / −removed line counts (each a positive number or null), or null. */
const diffStat = computed(() => {
    const node = props.node
    if (!node || node.type !== 'file') return null
    const a = typeof node.additions === 'number' && node.additions > 0 ? node.additions : null
    const d = typeof node.deletions === 'number' && node.deletions > 0 ? node.deletions : null
    if (a === null && d === null) return null
    return { additions: a, deletions: d }
})
</script>

<template>
    <span v-if="badge" class="git-status-badge">
        <!-- Inner group carries the gap so the (zero-size) tooltip below never adds trailing space. -->
        <span class="git-status-flags">
            <span v-if="diffStat" class="git-diffstat">
                <span v-if="diffStat.additions !== null" class="diffstat-add">+{{ diffStat.additions }}</span>
                <span v-if="diffStat.deletions !== null" class="diffstat-del">−{{ diffStat.deletions }}</span>
            </span>
            <span
                :id="badgeId"
                class="git-badge"
                :class="{ 'git-badge-conflict': badge.conflict }"
            ><span v-for="(part, i) in badge.parts" :key="i" :class="part.cls">{{ part.ch }}</span></span>
        </span>
        <AppTooltip :for="badgeId">{{ badge.tooltip }}</AppTooltip>
    </span>
</template>

<style scoped>
.git-status-badge {
    display: inline-flex;
    align-items: center;
}

/* Monospace is scoped to the visible flag/stat group only — NOT the root, so the
   nested AppTooltip doesn't inherit it and keeps the normal UI font. */
.git-status-flags {
    display: inline-flex;
    align-items: center;
    gap: .65rem;
    font-family: var(--wa-font-family-code);
}

.git-diffstat {
    display: inline-flex;
    gap: .35rem;
    font-size: var(--wa-font-size-xs);
    font-variant-numeric: tabular-nums;
    line-height: 1.6;
    white-space: nowrap;
}

.diffstat-add { color: #3a9a28; }
.diffstat-del { color: #e5484d; }

.git-badge {
    flex-shrink: 0;
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    line-height: 1.6;
    letter-spacing: .5px;
    white-space: nowrap;
}

.git-badge-modified  { color: #c4841d; }
.git-badge-added     { color: #3a9a28; }
.git-badge-deleted   { color: #e5484d; }
.git-badge-renamed   { color: #6e56cf; }
.git-badge-untracked { color: #7c8594; }
.git-badge-slot-empty { color: #5b6472; }
.git-badge-conflict  { color: #ff5c5c; }
</style>
