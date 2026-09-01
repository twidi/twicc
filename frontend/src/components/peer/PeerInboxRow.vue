<script setup>
/**
 * PeerInboxRow — one message row of the peer inbox, in labelled lines.
 *
 * Every piece of context on a row is a session title, so nothing is
 * self-explanatory: without a label the reader cannot tell the peer's session
 * from the local one, nor either from the message. Each fact therefore gets its
 * own line, opened by a label:
 *
 *   ↓ twicc-dell3                             [attachments] [status] [time]
 *     The message title
 *     | the message, quoted, two lines at most
 *   Delivered to session  “Peer inbox polish”  in ●twicc-poc
 *
 * Reading order encodes importance (decision of 2026-08-11): WHO speaks (the
 * peer, on the header line), then WHAT it is about (the sender-written title),
 * then what it says (the preview). The title line is skipped on rows stored
 * before the title became required.
 *
 * An outbound row is the same shape, its routing line reading "Sent from
 * session …": the local end is the sending session there. Only local context
 * appears — the peer's own session never crosses the wire. The label column
 * reads the same way in both directions, and each line wraps on its own on a
 * narrow screen.
 *
 * The message is shown as PLAIN TEXT, never rendered markdown: a preview
 * clamped to two lines cannot host headings, lists or code blocks. The full
 * markdown rendering lives in PeerMessageReviewDialog.
 */
import { computed } from 'vue'
import { usePeersStore } from '../../stores/peers'
import { useSettingsStore } from '../../stores/settings'
import { SESSION_TIME_FORMAT } from '../../constants'
import { formatDate } from '../../utils/date'
import ProjectBadge from '../project/ProjectBadge.vue'

const props = defineProps({
    message: { type: Object, required: true },
    // Pending rows sit under a "Pending messages" heading — the tag would only
    // repeat it.
    showStatus: { type: Boolean, default: true },
})

const peersStore = usePeersStore()
const settingsStore = useSettingsStore()

const isInbound = computed(() => props.message.direction === 'in')
const isPending = computed(() => props.message.status === 'pending')

const peerLabel = computed(() => peersStore.peerLabel(props.message.peer_id))
/** The direction rides on the arrow alone (shape + colour). Spelling it out
 *  ate the width the peer name needs on a phone; the wording survives as the
 *  icon's label and tooltip. */
const directionLabel = computed(() =>
    isInbound.value ? `Received from ${peerLabel.value}` : `Sent to ${peerLabel.value}`
)

// A pending inbound message is mail waiting to be opened; a resolved one is
// history, where the arrow tells the direction at a glance.
const icon = computed(() => {
    if (isInbound.value && isPending.value) return 'envelope'
    return isInbound.value ? 'arrow-down' : 'arrow-up'
})

// A session title is free text: a long one pushes the project out of sight and
// wraps the row over three lines. Cut it here; the full title stays in the
// hover tooltip.
const SESSION_TITLE_MAX = 40

function shortTitle(title) {
    const flat = String(title || '').replace(/\s+/g, ' ').trim()
    return flat.length > SESSION_TITLE_MAX ? `${flat.slice(0, SESSION_TITLE_MAX - 1)}…` : flat
}

/**
 * The routing lines, in reading order. Each is
 * `{label, title, display, projectId}`.
 *
 * Only the LOCAL end can be shown: nothing of the peer's own context crosses
 * the wire — not its session id (design §3.2), not its session title (removed
 * 2026-08-10, see `peer_messages.send`).
 */
const routes = computed(() => {
    const message = props.message
    const lines = []
    // Direct human authorship (`origin.author`, sender-declared — absent
    // means agent). A label-only line: an outbound direct message has no
    // origin session, so this is its whole provenance.
    if (message.origin?.author === 'human') {
        lines.push({
            key: 'author',
            label: isInbound.value
                ? `Written directly by ${peerLabel.value}'s user`
                : 'Written directly by you',
            title: '',
            display: '',
            projectId: null,
        })
    }
    const reply = message.reply_to_ref
    if (reply?.title) {
        lines.push({
            key: 'reply',
            label: reply.direction === 'out' ? 'In reply to your' : 'In reply to their',
            title: reply.title,
            display: shortTitle(reply.title),
            projectId: null,
        })
    }
    // The title and project come with the message, read live from the session
    // row server-side: they must not depend on what the front happens to have
    // loaded, and an id is never something a human can place. A row whose
    // session is gone (FK nulled) simply has no line.
    const local = isInbound.value ? message.delivered_to_session : message.origin_session
    if (local) {
        const title = local.title || 'Untitled session'
        lines.push({
            key: 'local',
            label: isInbound.value ? 'Delivered to session' : 'Sent from session',
            title,
            display: shortTitle(title),
            projectId: local.project_id || null,
        })
    }
    return lines
})

const attachments = computed(() => {
    const count = props.message.attachments_meta?.length || 0
    if (!count) return null
    return { count, purged: props.message.purged }
})

const statusVariant = computed(() => {
    if (props.message.status === 'delivered') return 'success'
    if (props.message.status === 'pending') return 'neutral'
    return 'danger'
})

// Timestamps follow the global time-format setting, like every other list.
const sessionTimeFormat = computed(() => settingsStore.getSessionTimeFormat)
const useRelativeTime = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ||
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_NARROW
)
const relativeTimeFormat = computed(() =>
    sessionTimeFormat.value === SESSION_TIME_FORMAT.RELATIVE_SHORT ? 'short' : 'narrow'
)
/** The moment a row shows: when it was resolved, else when it arrived. */
const date = computed(() => {
    const iso = props.message.resolved_at || props.message.created_at
    return iso ? new Date(iso) : null
})
const timestampSeconds = computed(() =>
    date.value ? Math.floor(date.value.getTime() / 1000) : 0
)
</script>

<template>
    <button type="button" class="pir">
        <!-- Header — who, and the state of the exchange. -->
        <span class="pir__head">
            <wa-icon
                :name="icon" :label="directionLabel" :title="directionLabel"
                class="pir__icon"
                :class="isInbound ? 'pir__icon--in' : 'pir__icon--out'"
            ></wa-icon>
            <span class="pir__heading" :title="directionLabel">{{ peerLabel }}</span>
            <span class="pir__fill"></span>
            <span v-if="attachments" class="pir__attachments">
                <wa-icon name="paperclip" auto-width></wa-icon>
                {{ attachments.count }}<template v-if="attachments.purged"> (purged)</template>
            </span>
            <wa-tag v-if="showStatus" :variant="statusVariant" size="small">{{ message.status }}</wa-tag>
            <span v-if="date" class="pir__time">
                <wa-relative-time
                    v-if="useRelativeTime"
                    :date.prop="date" :format="relativeTimeFormat"
                    numeric="always" sync
                ></wa-relative-time>
                <template v-else>{{ formatDate(timestampSeconds, { smart: true }) }}</template>
            </span>
        </span>

        <!-- The sender-written subject — second in importance after the peer. -->
        <span v-if="message.title" class="pir__title">{{ message.title }}</span>

        <!-- The message itself, alone on its line: it is the content, not metadata. -->
        <span class="pir__message">{{ message.text_preview }}</span>

        <!-- Routing — one labelled line per fact. -->
        <span v-for="route in routes" :key="route.key" class="pir__route">
            <span class="pir__route-label">{{ route.label }}</span>
            <span v-if="route.display" class="pir__route-title" :title="route.title">“{{ route.display }}”</span>
            <template v-if="route.projectId">
                <span class="pir__route-label">in</span>
                <ProjectBadge :project-id="route.projectId" class="pir__route-project" />
            </template>
        </span>
    </button>
</template>

<style scoped>
.pir {
    display: flex;
    flex-direction: column;
    /* native.css also centres a button's content: in a column that centres
       every line AND lets it size to its text, so long lines overflow both
       sides instead of ellipsizing. */
    align-items: stretch;
    justify-content: flex-start;
    gap: 0.15rem;
    width: 100%;
    box-sizing: border-box;
    padding: var(--wa-space-s) 0;
    border: none;
    border-bottom: 1px solid var(--wa-color-surface-border);
    min-width: 0;
    background: none;
    color: inherit;
    font: inherit;
    line-height: var(--wa-line-height-normal);
    text-align: left;
    cursor: pointer;
    /* WA's native.css gives every native <button> a fixed form-control height;
       a multi-line row overflows it and the lines collide. */
    height: auto;
    min-height: 0;
}
.pir:last-of-type { border-bottom: none; }
.pir:hover { background: var(--wa-color-surface-raised); }

.pir__head {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
    max-width: 100%;
}
.pir__fill { flex: 1; }
/* Direction at a glance, before reading the row: inbound wears the same brand
   colour as the incoming-message toast, outbound its own hue. */
.pir__icon { flex-shrink: 0; }
.pir__icon--in { color: var(--wa-color-brand-fill-loud, var(--wa-color-brand-60)); }
.pir__icon--out { color: var(--wa-color-success-fill-loud, var(--wa-color-success-60)); }
.pir__heading {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
}
.pir__attachments,
.pir__time {
    color: var(--wa-color-text-quiet);
    font-size: 0.8rem;
    white-space: nowrap;
    flex-shrink: 0;
}
.pir__attachments {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

/* The subject, indented with the quoted message it titles. Semibold is enough
   emphasis under the peer name (bold), which stays the loudest element. A
   title is one flattened line by construction; a long one ellipsizes. */
.pir__title {
    margin-left: 2rem;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    max-width: 100%;
}

/* The message is a quotation of someone else's words, so it wears the quote
   recipe of the markdown renderer (MarkdownContent.vue): quiet brand fill,
   left accent bar, square on the bar's side. Indented past the labels so it
   reads as a block inside the row, not as another field of it.
   Two lines: enough to recognise the message, never enough to push the
   routing lines off screen. */
.pir__message {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    margin: 0.2rem 0 0.3rem 2rem;
    padding: 0.35em 0.75em;
    min-width: 0;
    max-width: 100%;
    border-radius: var(--wa-border-radius-m);
    border-start-start-radius: 0;
    border-end-start-radius: 0;
    border-inline-start: 2px solid var(--wa-color-brand-fill-loud);
    background: var(--wa-color-brand-fill-quiet);
    color: var(--wa-color-text-normal);
    /* The row is a native button: WA gives it nowrap, which would keep the
       message on one endless line. */
    white-space: normal;
    overflow-wrap: anywhere;
}
/* In dark, the quiet brand fill sits almost on top of the surface it covers —
   the next step up restores a visible tint (same rule as the renderer). */
.wa-dark .pir__message { background: var(--wa-color-brand-fill-normal); }

/* One fact per line: label, value, and (when local) the project it lives in.
   Wraps instead of truncating on a narrow screen. */
.pir__route {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
    padding-left: 1.5rem;
    font-size: 0.85rem;
    min-width: 0;
    max-width: 100%;
}
.pir__route-label { color: var(--wa-color-text-quiet); flex-shrink: 0; }
.pir__route-title {
    color: var(--wa-color-text-normal);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 6ch;
}
.pir__route-project { max-width: 20ch; }
</style>
