// frontend/src/utils/providersStatus.js
//
// Pure rules behind the provider-status toasts: given one provider's record
// from ``providers-status.json`` (pushed whole by the ``providers_status_updated``
// frame), decide what — if anything — should be on screen, and word it.
//
// No Vue, no store, no toast library: everything here is a plain function of
// its inputs so it can be tested with ``node --test`` (see
// ``providersStatus.test.js``). The side effects (the Notivue items, the
// acknowledgment round-trip) live in ``providers/serviceStatusToast.js``.
//
// Vocabulary (mirrors ``twicc/providers_status.py``):
// - a record is ``{ status, incident, acknowledged }``;
// - an *incident* is ``{ started_at, status, changed_at, resolved_at }`` —
//   from the first non-operational status after an operational one to the
//   recovery; ``started_at`` is its identity and never moves, ``changed_at``
//   is the date of its latest transition;
// - an *episode* is one transition of an incident: its opening, each change
//   of level (up or down), its resolution. ``changedAt`` is the episode's
//   identity, so every step is announced and acknowledged on its own —
//   ``major → partial → major`` is three episodes, the second ``major``
//   included. The backend compares plain status values, so ``major`` followed
//   by ``major`` is one episode, not two.

export const OPERATIONAL = 'operational'

// A resolved incident is announced to anyone who has not acknowledged its
// recovery — but not forever: past this, coming back to "it was down three
// weeks ago" is noise, not information.
export const RESOLVED_ANNOUNCE_WINDOW_MS = 24 * 60 * 60_000

// Statuspage levels → toast type + the sentence fragment placed after the
// product name. A level absent here has no wording and raises no toast.
export const OUTAGE_TOASTS = {
    degraded_performance: { type: 'warning', phrase: 'is currently experiencing degraded performance' },
    partial_outage: { type: 'warning', phrase: 'is currently experiencing a partial outage' },
    major_outage: { type: 'error', phrase: 'is currently experiencing a major outage' },
    under_maintenance: { type: 'info', phrase: 'is currently under maintenance' },
}

/**
 * The episode a record currently stands for, or ``null`` when there is
 * nothing to announce.
 *
 * @param {Object|null|undefined} record
 * @param {number} [now] - Epoch ms, for the 24h resolved-announce window.
 * @returns {{ kind: 'outage'|'resolved', startedAt: string, status: string, changedAt: string, resolvedAt: string|null, lastStatus: string }|null}
 */
export function deriveEpisode(record, now = Date.now()) {
    if (!record || typeof record !== 'object') return null
    const { status, incident } = record
    if (!incident || typeof incident !== 'object' || !incident.started_at) return null

    if (status && status !== OPERATIONAL) {
        // Ongoing outage. Without wording for the level there is no toast.
        if (!OUTAGE_TOASTS[status]) return null
        return {
            kind: 'outage',
            startedAt: incident.started_at,
            status,
            // A record written before transitions were dated: the opening is
            // its only known transition.
            changedAt: incident.changed_at || incident.started_at,
            resolvedAt: null,
            lastStatus: status,
        }
    }

    if (status === OPERATIONAL && incident.resolved_at) {
        const resolvedMs = Date.parse(incident.resolved_at)
        if (Number.isNaN(resolvedMs)) return null
        if (now - resolvedMs > RESOLVED_ANNOUNCE_WINDOW_MS) return null
        return {
            kind: 'resolved',
            startedAt: incident.started_at,
            status: OPERATIONAL,
            // The resolution is the incident's last transition.
            changedAt: incident.changed_at || incident.resolved_at,
            resolvedAt: incident.resolved_at,
            lastStatus: incident.status,
        }
    }

    return null
}

/** Wire shape of an episode, as the backend stores and compares it. */
export function episodeToWire(episode) {
    return { started_at: episode.startedAt, status: episode.status, changed_at: episode.changedAt }
}

/** Stable identity of an episode, for "is this the toast already on screen". */
export function episodeKey(episode) {
    return `${episode.startedAt}|${episode.changedAt}|${episode.status}`
}

/** Whether the record's acknowledgment is exactly this episode. */
export function isAcknowledged(record, episode) {
    const ack = record?.acknowledged
    return !!ack
        && ack.started_at === episode.startedAt
        && ack.status === episode.status
        && ack.changed_at === episode.changedAt
}

/**
 * What should be on screen for this record: ``null`` for nothing, else the
 * episode, its toast type and its identity key.
 *
 * The same function serves every trigger — connect, status change,
 * acknowledgment received — so the screen is always a function of the file.
 */
export function planToast(record, now = Date.now()) {
    const episode = deriveEpisode(record, now)
    if (!episode) return null
    if (isAcknowledged(record, episode)) return null
    const type = episode.kind === 'resolved' ? 'success' : OUTAGE_TOASTS[episode.status].type
    return { key: episodeKey(episode), episode, type }
}

// ── Wording ─────────────────────────────────────────────────────────────────

export function toastTitle(vendorLabel) {
    return `${vendorLabel} status update`
}

/**
 * "14:02 → 15:30", with the date added on either end that is not today.
 *
 * @param {string} startedAt - ISO timestamp.
 * @param {string} resolvedAt - ISO timestamp.
 * @param {Object} [options] - ``locale`` / ``timeZone`` for deterministic
 *   output (tests); ``now`` (epoch ms) for the "today" check.
 */
export function formatIncidentWindow(startedAt, resolvedAt, { locale, timeZone, now = Date.now() } = {}) {
    const today = new Date(now).toLocaleDateString(locale, { timeZone })
    const format = (iso) => {
        const date = new Date(iso)
        if (Number.isNaN(date.getTime())) return '?'
        const sameDay = date.toLocaleDateString(locale, { timeZone }) === today
        return sameDay
            ? date.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', timeZone })
            : date.toLocaleString(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZone })
    }
    return `${format(startedAt)} → ${format(resolvedAt)}`
}

/**
 * The toast body, without the status-page link (the component appends it).
 *
 * @param {Object} episode - From ``deriveEpisode``.
 * @param {{ productLabel: string, vendorLabel: string }} identity
 * @param {Object} [windowOptions] - Forwarded to ``formatIncidentWindow``.
 */
export function toastSentence(episode, { productLabel, vendorLabel }, windowOptions) {
    if (episode.kind === 'resolved') {
        const window = formatIncidentWindow(episode.startedAt, episode.resolvedAt, windowOptions)
        return `${productLabel} issues on ${vendorLabel}'s side are now resolved (${window})`
    }
    return `${productLabel} ${OUTAGE_TOASTS[episode.status].phrase} on ${vendorLabel}'s side`
}
